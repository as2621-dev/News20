/**
 * Onboarding interest-profile persistence (Phase 1e SP4) — the SHARED upsert path
 * that turns an in-memory {@link InterestSelection} into RLS-scoped
 * `user_interest_profile` / `user_interest_traits` rows + an
 * `users.user_onboarded_at` stamp.
 *
 * This is deliberately a standalone data-access module (sibling of
 * `src/lib/feed/supabaseFeed.ts` and `src/lib/interests.ts`): a small function
 * that writes through the authed browser client under RLS, with an injectable
 * `client` default for tests. The M3 voice agent will reuse it verbatim — only
 * the `profile_source` differs ("voice" vs the chip default "typed") — so the
 * source is parameterized rather than hardcoded.
 *
 * ── Custom-interest handling (phase Open Q2, v1 = canonicalize) ──────────────
 * A free-text custom is matched (case-insensitive on `interest_label` /
 * `interest_slug`) against the existing `interests` tree. On a MATCH we upsert a
 * profile row pointing at that node. On NO MATCH we DO NOT write anything:
 * migration-0003 RLS makes `interests` **public-read with no insert policy**, so
 * the authed browser client physically cannot create a new taxonomy node (the
 * insert would 401). Rather than orphan a `user_interest_profile` row against a
 * non-existent interest (Rule 12 — never a dangling row), the unmatched label is
 * returned in {@link PersistProfileResult.unpersisted_customs} so the caller can
 * surface it to the user.
 *
 * **v1 limitation (deferred):** creating a brand-new custom taxonomy node for a
 * novel interest needs the service-role pipeline (a migration / batch job seeding
 * `interests` + its `interest_search_query`). That is out of scope for this
 * client-side flow and tracked as a follow-up.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { InterestSelection } from "@/components/onboarding/InterestChips";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { FollowSelection, FollowSource } from "@/types/picker";

/**
 * Default `profile_weight` by selection depth (phase Open Q1). Deeper picks are
 * MORE specific, so they start heavier — a depth-2 "India cricket" leaf signals a
 * stronger preference than a depth-0 "Sport" category.
 *
 * Tunable, see `reference/ranking-spec.md` §1 (Affinity = normalized
 * `profile_weight`). Centralized here so the weight is not scattered across
 * call-sites; the M3 voice path reuses the same map.
 */
export const PROFILE_WEIGHT_BY_DEPTH: Readonly<Record<number, number>> = {
  0: 1.0,
  1: 1.5,
  2: 2.0,
};

/** Fallback weight for an unexpected depth outside {@link PROFILE_WEIGHT_BY_DEPTH}. */
const DEFAULT_PROFILE_WEIGHT = 1.0;

/** The `interest_profile_source` enum values migration 0003 defines. */
export type InterestProfileSource = "voice" | "typed" | "signal";

/** Options for {@link persistInterestProfile}. */
export interface PersistProfileOptions {
  /**
   * Which path produced these picks — written to `user_interest_profile.profile_source`.
   * Chip onboarding (this phase) is `"typed"`; the M3 voice agent passes `"voice"`.
   */
  profile_source?: InterestProfileSource;
}

/** Typed outcome of a persist run. */
export interface PersistProfileResult {
  /** How many `user_interest_profile` rows were upserted (taxonomy + matched customs). */
  persisted_count: number;
  /**
   * Free-text customs that matched NO existing taxonomy node and were therefore
   * NOT written (RLS forbids client-side `interests` inserts — see module JSDoc).
   * The caller surfaces these to the user rather than dropping them silently.
   */
  unpersisted_customs: string[];
}

/** Resolve the default weight for a node depth (Open Q1 depth map). */
function resolveProfileWeight(depthLevel: number): number {
  return PROFILE_WEIGHT_BY_DEPTH[depthLevel] ?? DEFAULT_PROFILE_WEIGHT;
}

/** One `user_interest_profile` row to upsert. */
interface ProfileUpsertRow {
  profile_user_id: string;
  profile_interest_id: string;
  profile_weight: number;
  profile_source: InterestProfileSource;
  profile_is_strict: boolean;
}

/**
 * Find an existing `interests` node whose label OR slug matches the custom label
 * case-insensitively. Returns the `interest_id` and resolved `depth_level` on a
 * match, else `null`.
 *
 * Reason: customs are canonicalized into the tree (Open Q2 v1). PostgREST `or` +
 * `ilike` does a case-insensitive match without pulling the whole taxonomy client
 * side. Only the public-read `interests` table is queried (no write).
 */
async function findCanonicalInterest(
  client: SupabaseClient,
  customLabel: string,
): Promise<{ interest_id: string; depth_level: number } | null> {
  const normalizedLabel = customLabel.trim();
  if (normalizedLabel === "") {
    return null;
  }
  // Escape PostgREST `or`-filter metacharacters so a label like "a,b" or "x*"
  // cannot break out of the filter expression.
  const safeLabel = normalizedLabel.replace(/[,()*]/g, " ").trim();
  if (safeLabel === "") {
    return null;
  }
  const { data, error } = await client
    .from("interests")
    .select("interest_id,depth_level")
    .or(`interest_label.ilike.${safeLabel},interest_slug.ilike.${safeLabel}`)
    .eq("interest_is_active", true)
    .limit(1)
    .returns<{ interest_id: string; depth_level: number }[]>();

  if (error) {
    // Surface, do not swallow (Rule 12): a failed canonicalization lookup must not
    // silently drop the custom. The caller treats a throw as a hard failure.
    logger.error("custom_interest_canonicalize_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm migration 0003 applied and `interests` allows anon/authed SELECT.",
    });
    throw new Error(
      `Failed to canonicalize custom interest "${normalizedLabel}": ${error.message}. ` +
        "fix_suggestion: confirm migration 0003 applied and interests are readable.",
    );
  }

  const match = (data ?? [])[0];
  return match ? { interest_id: match.interest_id, depth_level: match.depth_level } : null;
}

/**
 * Persist a completed onboarding selection for one user, scoped to their
 * `auth.uid()` (= `userId`).
 *
 * Writes, in order: each taxonomy pick + each canonicalized custom as a
 * `user_interest_profile` row (upsert on the unique
 * `(profile_user_id, profile_interest_id)`), a default `user_interest_traits`
 * row (upsert on the unique `traits_user_id`), and the `users.user_onboarded_at`
 * stamp. An empty selection is a safe no-op for the profile rows but STILL stamps
 * traits + onboarded_at (the flow gates "pick ≥1" itself; this stays robust).
 *
 * @param userId - The authed user's id (`auth.uid()`); every row is scoped to it.
 * @param selection - The in-memory picks from {@link InterestChips}.
 * @param opts - Optional {@link PersistProfileOptions} (e.g. `profile_source`).
 * @param client - Optional Supabase client (injected in tests). Defaults to the
 *   shared authed browser client.
 * @returns A {@link PersistProfileResult} — rows written + any unmatched customs.
 * @throws If any write fails (errors are surfaced, never swallowed — Rule 12).
 *
 * @example
 * const result = await persistInterestProfile(session.user.id, selection);
 * result.persisted_count; // 3
 * result.unpersisted_customs; // ["formula 1"] — surface to the user
 */
export async function persistInterestProfile(
  userId: string,
  selection: InterestSelection,
  opts: PersistProfileOptions = {},
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<PersistProfileResult> {
  const profileSource: InterestProfileSource = opts.profile_source ?? "typed";
  logger.info("persist_interest_profile_started", {
    taxonomy_count: selection.taxonomy_selections.length,
    custom_count: selection.custom_selections.length,
    profile_source: profileSource,
  });

  const upsertRows: ProfileUpsertRow[] = [];
  const unpersisted_customs: string[] = [];

  // 1. Taxonomy picks — strict flag + depth weight preserved.
  for (const taxonomy of selection.taxonomy_selections) {
    upsertRows.push({
      profile_user_id: userId,
      profile_interest_id: taxonomy.interest_id,
      profile_weight: resolveProfileWeight(taxonomy.depth_level),
      profile_source: profileSource,
      profile_is_strict: taxonomy.profile_is_strict,
    });
  }

  // 2. Customs — canonicalize to an existing node; never write a dangling row.
  for (const custom of selection.custom_selections) {
    const match = await findCanonicalInterest(client, custom.custom_label);
    if (match) {
      upsertRows.push({
        profile_user_id: userId,
        profile_interest_id: match.interest_id,
        profile_weight: resolveProfileWeight(match.depth_level),
        profile_source: profileSource,
        // A canonicalized custom is not "only this, nothing broader" — it is a
        // typed interest pointing at an existing node.
        profile_is_strict: false,
      });
    } else {
      // No taxonomy match: RLS forbids client-side `interests` inserts, so we
      // CANNOT create a node here. Surface it instead of orphaning a row (Rule 12).
      unpersisted_customs.push(custom.custom_label);
      logger.warn("custom_interest_unpersisted_no_match", {
        custom_label: custom.custom_label,
        fix_suggestion:
          "Novel custom nodes need a service-role/migration follow-up to seed `interests` (v1 limitation).",
      });
    }
  }

  // 3. Upsert profile rows (if any) on the unique (profile_user_id, profile_interest_id).
  if (upsertRows.length > 0) {
    const { error: profileError } = await client
      .from("user_interest_profile")
      .upsert(upsertRows, { onConflict: "profile_user_id,profile_interest_id" });
    if (profileError) {
      logger.error("persist_interest_profile_upsert_failed", {
        error_message: profileError.message,
        fix_suggestion: "Confirm the user is authed and user_interest_profile owner-all RLS permits the write.",
      });
      throw new Error(
        `Failed to persist interest profile: ${profileError.message}. ` +
          "fix_suggestion: confirm the user is authed and RLS permits the owner write.",
      );
    }
  }

  // 4. Default traits row (upsert on the unique traits_user_id — keep idempotent).
  const { error: traitsError } = await client
    .from("user_interest_traits")
    .upsert({ traits_user_id: userId }, { onConflict: "traits_user_id" });
  if (traitsError) {
    logger.error("persist_interest_traits_failed", {
      error_message: traitsError.message,
      fix_suggestion: "Confirm user_interest_traits owner-all RLS permits the write.",
    });
    throw new Error(
      `Failed to persist interest traits: ${traitsError.message}. ` +
        "fix_suggestion: confirm RLS permits the owner write.",
    );
  }

  // 5. Stamp onboarding completion on the user's own row.
  const { error: onboardedError } = await client
    .from("users")
    .update({ user_onboarded_at: new Date().toISOString() })
    .eq("user_id", userId);
  if (onboardedError) {
    logger.error("persist_user_onboarded_at_failed", {
      error_message: onboardedError.message,
      fix_suggestion: "Confirm users update-self RLS permits the write and the users row exists (handle_new_user).",
    });
    throw new Error(
      `Failed to stamp user_onboarded_at: ${onboardedError.message}. ` +
        "fix_suggestion: confirm users update-self RLS permits the write.",
    );
  }

  const result: PersistProfileResult = {
    persisted_count: upsertRows.length,
    unpersisted_customs,
  };
  logger.info("persist_interest_profile_completed", {
    persisted_count: result.persisted_count,
    unpersisted_count: result.unpersisted_customs.length,
  });
  return result;
}

// ─── Phase 5 SP4 — recursive-picker follows persistence ──────────────────────
//
// The recursive picker (Phase 5) produces a flat, canonical-deduped list of
// {@link FollowSelection}s (one entry per real-world follow — see
// `createSelectionStore().all()`). This is a DIFFERENT shape from the chip
// onboarding's {@link InterestSelection}, and writes to TWO axes:
//   - TOPIC follows  → `user_interest_profile` (the ranker already reads it),
//     canonicalized against the public-read `interests` tree (reusing
//     {@link findCanonicalInterest}); a topic that matches NOTHING is surfaced
//     in `unpersisted`, never orphaned (mirrors the custom-interest handling).
//   - ENTITY follows → the new `user_entity_follows` table (migration 0007),
//     weighted by `follow_source` (the §7 intent signal: custom > more ≥ seed).
//
// Free-text customs (`kind === 'freetext'`) have NO `entities` row, and
// `user_entity_follows.entity_id` is a NOT-NULL FK → they CANNOT be stored
// without orphaning. They are surfaced in `unpersisted` (never dropped silently)
// exactly like the unmatched chip customs above.

/**
 * Follow-weight by `follow_source` — the §7 intent signal (`reference/ranking-spec.md`
 * §7): a `custom` follow (the user typed it) is higher-intent than a seed/more tap, so
 * it weights MORE heavily. The single tunable map (not scattered across call-sites) so
 * the weighting is changed in ONE place. Invariant: `custom > more ≥ seed`.
 */
export const ENTITY_FOLLOW_WEIGHT_BY_SOURCE: Readonly<Record<FollowSource, number>> = {
  seed: 1.0,
  more: 1.0,
  custom: 2.0,
};

/** Resolve the `follow_weight` for an entity follow from its `source` (§7 intent signal). */
function weightFor(source: FollowSource): number {
  return ENTITY_FOLLOW_WEIGHT_BY_SOURCE[source];
}

/** One `user_entity_follows` row to upsert (migration 0007 column shape). */
interface EntityFollowUpsertRow {
  follow_user_id: string;
  entity_id: string;
  follow_path: string[];
  follow_source: FollowSource;
  follow_weight: number;
}

/** Typed outcome of a {@link persistPickerFollows} run. */
export interface PersistPickerResult {
  /** How many `user_interest_profile` rows were upserted (canonicalized topic follows). */
  profile_count: number;
  /** How many `user_entity_follows` rows were upserted (registry-resolved entity follows). */
  entity_follow_count: number;
  /**
   * Follow labels that could NOT be persisted: a topic matching no taxonomy node, or a
   * free-text custom with no registry entity (the NOT-NULL `entity_id` FK forbids a row).
   * The caller surfaces these rather than dropping them silently (Rule 12).
   */
  unpersisted: string[];
}

/**
 * Persist a completed recursive-picker selection for one user, scoped to their
 * `auth.uid()` (= `userId`).
 *
 * Writes, in order: each TOPIC follow (canonicalized against `interests`) as a
 * `user_interest_profile` upsert; each registry-resolved ENTITY follow as a
 * `user_entity_follows` upsert (weighted by `source`); then the
 * `users.user_onboarded_at` stamp. An EMPTY `selections` array is a valid skip: it
 * writes NO profile/follow rows but STILL stamps onboarded_at (the picker is
 * skippable — spec §11) and does NOT error.
 *
 * Free-text customs (`kind === 'freetext'`) and topic follows matching no taxonomy
 * node are surfaced in {@link PersistPickerResult.unpersisted} — never written as
 * orphan rows (Rule 12).
 *
 * **v1 simplification (documented, not a silent drop):** `user_entity_follows.follow_path`
 * is a single `text[]`, so only the PRIMARY `selection.path` is stored. When a canonical
 * entity was reached via several routes (`selection.extraPaths`), those alternate paths
 * are NOT persisted in v1 — the schema is one path column; multi-path persistence is a
 * tracked follow-up.
 *
 * @param userId - The authed user's id (`auth.uid()`); every row is scoped to it.
 * @param selections - The canonical, deduped follows from `createSelectionStore().all()`.
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared
 *   authed browser client.
 * @returns A {@link PersistPickerResult} — topic rows + entity rows + any unpersisted labels.
 * @throws If any write fails (errors are surfaced, never swallowed — Rule 12).
 *
 * @example
 * const result = await persistPickerFollows(session.user.id, store.all());
 * result.profile_count;        // 2  — canonicalized topic follows
 * result.entity_follow_count;  // 5  — registry entity follows
 * result.unpersisted;          // ["formula 1"] — free-text/unmatched, surface to the user
 */
export async function persistPickerFollows(
  userId: string,
  selections: FollowSelection[],
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<PersistPickerResult> {
  logger.info("persist_picker_follows_started", {
    selection_count: selections.length,
    topic_count: selections.filter((selection) => selection.type === "topic").length,
    entity_count: selections.filter((selection) => selection.type === "entity").length,
  });

  const profileRows: ProfileUpsertRow[] = [];
  const entityRows: EntityFollowUpsertRow[] = [];
  const unpersisted: string[] = [];

  for (const selection of selections) {
    if (selection.type === "topic") {
      // Topic follows reach the ranker via `user_interest_profile`. Canonicalize the
      // label against the public-read `interests` tree; a miss is surfaced (never an
      // orphan row), mirroring the chip-onboarding custom handling.
      const match = await findCanonicalInterest(client, selection.label);
      if (match) {
        profileRows.push({
          profile_user_id: userId,
          profile_interest_id: match.interest_id,
          profile_weight: resolveProfileWeight(match.depth_level),
          // The picker is a typed pick pointing at an existing node, not a strict-only.
          profile_source: "typed",
          profile_is_strict: false,
        });
      } else {
        unpersisted.push(selection.label);
        logger.warn("picker_topic_unpersisted_no_match", {
          label: selection.label,
          fix_suggestion:
            "Novel topic nodes need a service-role/migration follow-up to seed `interests` (v1 limitation).",
        });
      }
      continue;
    }

    // Entity follows. Free-text customs have NO `entities` row → the NOT-NULL
    // `entity_id` FK forbids a row. Surface them instead of orphaning (Rule 12).
    if (selection.kind === "freetext") {
      unpersisted.push(selection.label);
      logger.warn("picker_freetext_unpersisted_no_entity", {
        label: selection.label,
        follow_id: selection.followId,
        fix_suggestion:
          "Free-text customs have no entities row; resolving them needs a service-role registry seed (v1 limitation).",
      });
      continue;
    }

    // Registry-resolved entity follow → one `user_entity_follows` row. v1 stores ONLY the
    // primary `path` (the schema is one text[]; `extraPaths` are a documented v1 omission).
    entityRows.push({
      follow_user_id: userId,
      entity_id: selection.followId,
      follow_path: selection.path,
      follow_source: selection.source,
      follow_weight: weightFor(selection.source),
    });
  }

  // 1. Topic follows → user_interest_profile (upsert on the unique pair).
  if (profileRows.length > 0) {
    const { error: profileError } = await client
      .from("user_interest_profile")
      .upsert(profileRows, { onConflict: "profile_user_id,profile_interest_id" });
    if (profileError) {
      logger.error("persist_picker_profile_upsert_failed", {
        error_message: profileError.message,
        fix_suggestion: "Confirm the user is authed and user_interest_profile owner-all RLS permits the write.",
      });
      throw new Error(
        `Failed to persist picker topic follows: ${profileError.message}. ` +
          "fix_suggestion: confirm the user is authed and RLS permits the owner write.",
      );
    }
  }

  // 2. Entity follows → user_entity_follows (upsert on the PK pair — idempotent toggle).
  if (entityRows.length > 0) {
    const { error: entityError } = await client
      .from("user_entity_follows")
      .upsert(entityRows, { onConflict: "follow_user_id,entity_id" });
    if (entityError) {
      logger.error("persist_picker_entity_follows_upsert_failed", {
        error_message: entityError.message,
        fix_suggestion: "Confirm migration 0007 applied and user_entity_follows owner-all RLS permits the write.",
      });
      throw new Error(
        `Failed to persist picker entity follows: ${entityError.message}. ` +
          "fix_suggestion: confirm migration 0007 applied and RLS permits the owner write.",
      );
    }
  }

  // 3. Stamp onboarding completion on the user's own row — ALWAYS (even on a skip), so
  // the onboarded-skip gate works for a zero-follow user (spec §11 skippable).
  const { error: onboardedError } = await client
    .from("users")
    .update({ user_onboarded_at: new Date().toISOString() })
    .eq("user_id", userId);
  if (onboardedError) {
    logger.error("persist_picker_user_onboarded_at_failed", {
      error_message: onboardedError.message,
      fix_suggestion: "Confirm users update-self RLS permits the write and the users row exists (handle_new_user).",
    });
    throw new Error(
      `Failed to stamp user_onboarded_at: ${onboardedError.message}. ` +
        "fix_suggestion: confirm users update-self RLS permits the write.",
    );
  }

  const result: PersistPickerResult = {
    profile_count: profileRows.length,
    entity_follow_count: entityRows.length,
    unpersisted,
  };
  logger.info("persist_picker_follows_completed", {
    profile_count: result.profile_count,
    entity_follow_count: result.entity_follow_count,
    unpersisted_count: result.unpersisted.length,
  });
  return result;
}

// ─── Phase 5c SP4 — source-onboarding-complete marker ────────────────────────
//
// The picker step already stamps `users.user_onboarded_at` (above). The SOURCE
// step (the 3 recommendation screens) runs AFTER the picker, so it needs its OWN
// completion marker — `user_onboarded_at` is taken by the prior step and adding a
// `users.user_sources_onboarded_at` column would need a migration (out of scope
// for this client-side SP4a logic pass; no migration file is in scope).
//
// Mechanism choice (Rule 7 — pick one, name the other): the codebase has TWO
// "step state" mechanisms — the Supabase `users` column (`user_onboarded_at`) and
// localStorage (`src/lib/signals.ts`, documented there as "no DB table, no
// migration" for non-security client state). The source-step marker is a UX
// SKIP-GATE only (the FOLLOWS themselves persist RLS-scoped to the DB via
// `followSource`/`upsertUserAddedSource`), so it uses the localStorage mechanism —
// mirroring `signals.ts` (SSR-safe, try/catch, best-effort). When a migration is
// added later, this can be promoted to a `users` column (the getter/setter contract
// stays the same). Flagged for cleanup in the SP4a report.

/** The `localStorage` key holding the source-onboarding-complete marker. */
const SOURCE_ONBOARDING_COMPLETE_STORAGE_KEY = "n20-source-onboarding-complete";

/** The value written when the source step is complete (presence = done). */
const SOURCE_ONBOARDING_COMPLETE_VALUE = "1";

/**
 * Mark the source-onboarding step (the 3 recommendation screens) complete, so the
 * future flow wiring routes to the reel and a returning user skips the source
 * screens. Best-effort: a `localStorage` write failure (private mode / quota) is
 * logged and swallowed — it must never block routing to the reel (the worst case
 * is the user sees the skippable screens again, never a hard error).
 *
 * SSR / no-`localStorage` safe (the static-export build renders some pages
 * server-side): a no-op when `window`/`localStorage` is unavailable.
 *
 * @returns Nothing — best-effort persistence (mirrors `signals.ts`).
 *
 * @example
 * markSourceOnboardingComplete(); // on completing/skipping the last source screen
 */
export function markSourceOnboardingComplete(): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(SOURCE_ONBOARDING_COMPLETE_STORAGE_KEY, SOURCE_ONBOARDING_COMPLETE_VALUE);
    logger.info("source_onboarding_marked_complete", {});
  } catch (error: unknown) {
    logger.warn("source_onboarding_mark_failed", {
      error_message: error instanceof Error ? error.message : "unknown",
      fix_suggestion:
        "localStorage write failed (private mode / quota); the source step may re-show next visit (harmless, skippable).",
    });
  }
}

/**
 * Whether the source-onboarding step has been completed on THIS device — the
 * returning-user-skip gate the future flow reads to send a returning user straight
 * to the reel without re-walking the source screens.
 *
 * SSR / no-`localStorage` safe: returns `false` (not complete) when `window`/
 * `localStorage` is unavailable or the read throws — defaulting to "show the
 * (skippable) screens" is the safe failure (never silently traps the user).
 *
 * @returns `true` once {@link markSourceOnboardingComplete} has run on this device.
 *
 * @example
 * if (isSourceOnboardingComplete()) router.replace("/"); // returning user → reel
 */
export function isSourceOnboardingComplete(): boolean {
  if (typeof window === "undefined" || !window.localStorage) {
    return false;
  }
  try {
    return window.localStorage.getItem(SOURCE_ONBOARDING_COMPLETE_STORAGE_KEY) === SOURCE_ONBOARDING_COMPLETE_VALUE;
  } catch {
    // Reason: corrupt/blocked storage must never trap the user — default to "not
    // complete" so the skippable source screens show (worst case: shown again).
    return false;
  }
}
