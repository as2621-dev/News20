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
