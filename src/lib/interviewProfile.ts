/**
 * Interview onboarding persistence (FSR slice #2) — turn a validated terminal
 * micro-interest payload into REAL taxonomy nodes + a deep, per-user interest profile.
 *
 * This is the persistence half of the conversational interview (the worker's engine,
 * slice #1, produces the terminal payload; the chat UI, slice #4, confirms it, then
 * calls this). It is the interview twin of {@link import("@/lib/onboardingProfile")}'s
 * `persistPickerFollows`, and shares its depth→weight semantics
 * ({@link resolveProfileWeight}) and `profile_source` parameterization — but it does
 * ONE thing the picker path deliberately cannot: it MINTS new `interests` nodes.
 *
 * ── Why an RPC and not a direct upsert ───────────────────────────────────────────
 * `interests` is public-read with NO write policy (migration 0003), so the authed
 * browser client physically cannot INSERT a taxonomy node — the picker path surfaces
 * unmatched customs as `unpersisted` for exactly this reason. Slice #2's whole point is
 * to make a typed niche a REAL tracked node, so the write goes through the SECURITY
 * DEFINER `mint_interest_ladder` RPC (migration 0025): a single narrow, validated seam
 * that mints the full root→leaf chain and returns the leaf id, with cross-user
 * convergence (upsert by slug) so two users who typed the same niche differently land
 * on ONE node. The per-user display label rides on the profile row, never the node.
 *
 * ── Contract note (Rule 7 / consumed downstream) ─────────────────────────────────
 * The persisted shape (minted nodes with a recoverable parent chain + deep
 * `user_interest_profile` rows carrying `profile_is_strict` + `profile_display_label`)
 * is the contract slices #6 (allocator) and #7 (assembly) read. Keep it exactly as
 * `reference/interview-onboarding-spec.md` §4–§5 defines; a deviation here is a
 * downstream break, so validation rejects LOUDLY rather than persisting something off-shape.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { DESIGN_BUCKET_IDS, DESIGN_BUCKETS } from "@/lib/feedBuckets";
import { logger } from "@/lib/logger";
import { type InterestProfileSource, resolveProfileWeight } from "@/lib/onboardingProfile";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { InterviewTerminalPayload, TerminalMicroInterest, TerminalMuteTerm } from "@/types/interview";

/**
 * The 8 canonical topic ROOT slugs a micro-interest slug must anchor to — the TS twin
 * of `agents.pipeline.categories.TOPIC_CATEGORIES`. Derived from {@link DESIGN_BUCKETS}
 * (`kind === "cat"` = topic root; the `"src"` youtube/x axes are NOT interest roots) so
 * this never drifts from the single declared bucket set (Rule 7).
 */
const TOPIC_ROOT_SLUGS: ReadonlySet<string> = new Set(
  DESIGN_BUCKET_IDS.filter((bucketId) => DESIGN_BUCKETS[bucketId].kind === "cat"),
);

/** Max drill-down depth below a root (spec §3) ⇒ at most this many dotted segments incl. root. */
const MAX_SLUG_SEGMENTS = 4;

/** Minimum distinct anchor terms a niche must carry to be searchable (spec §4). */
const MIN_ANCHOR_TERMS = 2;

/** A slug segment: lowercase alphanumerics, dash-separated. Mirrors `guards._SLUG_SEGMENT_RE`. */
const SLUG_SEGMENT_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** Why a terminal micro-interest was rejected before any write (surfaced, never silent). */
export interface RejectedInterest {
  /** The offending slug (or `"<empty>"` when the payload had none). */
  canonical_slug: string;
  /** A stable machine reason code (e.g. `"slug_not_root_anchored"`). */
  reason: string;
}

/** Typed outcome of a {@link persistInterviewInterests} run. */
export interface PersistInterviewResult {
  /** How many `user_interest_profile` rows were upserted (one per accepted micro-interest). */
  minted_interest_count: number;
  /** Micro-interests dropped by the validation backstop — surfaced, not silently minted. */
  rejected_interests: RejectedInterest[];
}

/** Options for {@link persistInterviewInterests}. */
export interface PersistInterviewOptions {
  /**
   * Which path produced these picks — written to `user_interest_profile.profile_source`.
   * The chat interview is `"typed"` (the default); the M3 voice interview passes `"voice"`.
   */
  profile_source?: InterestProfileSource;
  /**
   * Rebuild-my-feed semantics (issue #9): after the new rows are upserted, DELETE this
   * user's `user_interest_profile` rows that are NOT in the confirmed set, so the profile
   * ends EQUAL to the re-interview's terminal list (old and new never blend). Ordering is
   * deliberate — new rows land first, stale rows are deleted LAST — so no failure window
   * ever leaves the user with zero rows, and a replace with an EMPTY accepted set is
   * refused outright (never a wipe-all). A failure after the new rows landed throws with
   * `error.name === REPLACE_PARTIAL_ERROR_NAME` so callers can tell "old profile intact"
   * from "new rows saved, stale cleanup pending — retry". Default `false` (first-run
   * onboarding keeps pure upsert semantics).
   */
  replace_existing?: boolean;
}

/**
 * `Error.name` of a replace-mode failure that happened AFTER the new profile rows were
 * written (the traits or stale-delete step): the profile is temporarily old∪new (the old
 * rows are still live — the feed keeps working) and an idempotent retry finishes the
 * replace. Failures WITHOUT this name left the old profile fully intact.
 */
export const REPLACE_PARTIAL_ERROR_NAME = "ReplacePartialError";

/** Build an Error tagged as a post-upsert (partial) replace failure. */
function replacePartialError(message: string): Error {
  const error = new Error(message);
  error.name = REPLACE_PARTIAL_ERROR_NAME;
  return error;
}

/** One `user_mute_terms` row to upsert (a SKIP-TUNE mute, scoped to the owner). */
interface MuteTermUpsertRow {
  mute_user_id: string;
  mute_category: string;
  mute_term: string;
}

/** Typed outcome of a {@link persistMuteTerms} run. */
export interface PersistMuteTermsResult {
  /** How many `user_mute_terms` rows this user now has after the write (0 when cleared). */
  persisted_mute_count: number;
}

/**
 * Persist a completed interview's SKIP-TUNE mute terms for one user, scoped to their
 * `auth.uid()` (= `userId`). Each term becomes a HARD FILTER at feed assembly (spec §8):
 * a story matching it never enters the user's feed. Mutes are per-user private data, so
 * this is a plain owner-scoped write (migration 0029) — no definer RPC (unlike interests).
 *
 * ── Clean-replace (issue #17 AC #5) ──────────────────────────────────────────────
 * On a rebuild-my-feed re-run (`replace_existing: true`) the user's mute set must end
 * EQUAL to the re-interview's list — no ORPHANED mutes from a prior run may linger (a
 * stale mute would keep silently erasing stories the user no longer wants gone). Mutes
 * are fully re-derived by each interview and are SUBTRACTIVE (a transient empty-mute
 * window only ever lets MORE stories through, never fewer — it can never corrupt or lose
 * user content), so the simplest correct clean-replace is delete-all-then-insert: it
 * guarantees zero orphans. An EMPTY replace set is VALID (the user cleared their mutes) —
 * unlike interests, clearing mutes is a legitimate, non-destructive outcome.
 *
 * First-run onboarding (`replace_existing` omitted) is a pure idempotent upsert.
 *
 * @param userId - The authed user's id (`auth.uid()`); every row is scoped to it.
 * @param muteTerms - The terminal SKIP-TUNE mute terms (may be empty).
 * @param opts - `{ replace_existing }` — clean-replace semantics for rebuild-my-feed.
 * @param client - Optional Supabase client (injected in tests; defaults to the browser client).
 * @returns A {@link PersistMuteTermsResult} — how many mute rows the user ends with.
 * @throws If a delete or upsert fails (surfaced, never swallowed — Rule 12).
 */
export async function persistMuteTerms(
  userId: string,
  muteTerms: TerminalMuteTerm[],
  opts: { replace_existing?: boolean } = {},
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<PersistMuteTermsResult> {
  // Normalize + dedup by (category, term): the composite PK forbids a row appearing twice
  // in one upsert batch, and a mute term is only meaningful as a trimmed, non-empty string.
  const rowByKey = new Map<string, MuteTermUpsertRow>();
  for (const mute of muteTerms ?? []) {
    const category = String(mute?.mute_category ?? "").trim();
    const term = String(mute?.mute_term ?? "").trim();
    if (category === "" || term === "") {
      continue;
    }
    // Reason: dedup case-insensitively so "Crypto" and "crypto" don't both persist (the
    // assembly matcher is case-insensitive anyway); keep the FIRST casing the user gave.
    const key = `${category.toLowerCase()} ${term.toLowerCase()}`;
    if (!rowByKey.has(key)) {
      rowByKey.set(key, { mute_user_id: userId, mute_category: category, mute_term: term });
    }
  }
  const rows = [...rowByKey.values()];
  logger.info("persist_mute_terms_started", {
    mute_count: rows.length,
    replace_existing: opts.replace_existing ?? false,
  });

  // Clean-replace: delete the user's whole mute set FIRST so the insert below leaves the
  // set EQUAL to this interview's list — no orphaned mutes survive (AC #5). Safe to delete
  // first because mutes are subtractive (an empty-mute window never loses user content).
  if (opts.replace_existing) {
    const { error: deleteError } = await client.from("user_mute_terms").delete().eq("mute_user_id", userId);
    if (deleteError) {
      logger.error("persist_mute_terms_replace_delete_failed", {
        error_message: deleteError.message,
        fix_suggestion: "Confirm the user is authed and user_mute_terms owner-all RLS permits the delete.",
      });
      throw new Error(
        `Failed to clear stale mute terms: ${deleteError.message}. ` +
          "fix_suggestion: confirm the user is authed and RLS permits the owner delete.",
      );
    }
  }

  if (rows.length > 0) {
    const { error: upsertError } = await client
      .from("user_mute_terms")
      .upsert(rows, { onConflict: "mute_user_id,mute_category,mute_term" });
    if (upsertError) {
      logger.error("persist_mute_terms_upsert_failed", {
        error_message: upsertError.message,
        fix_suggestion: "Confirm the user is authed and user_mute_terms owner-all RLS permits the write.",
      });
      throw new Error(
        `Failed to persist mute terms: ${upsertError.message}. ` +
          "fix_suggestion: confirm the user is authed and RLS permits the owner write.",
      );
    }
  }

  logger.info("persist_mute_terms_completed", { persisted_mute_count: rows.length });
  return { persisted_mute_count: rows.length };
}

/** One `user_interest_profile` row to upsert (with the interview's per-user display label). */
interface InterviewProfileUpsertRow {
  profile_user_id: string;
  profile_interest_id: string;
  profile_weight: number;
  profile_source: InterestProfileSource;
  profile_is_strict: boolean;
  profile_display_label: string;
}

/**
 * Derive the fallback ladder — the ordered PARENT chain of a dotted slug, EXCLUDING the
 * leaf. TS twin of `agents.interview.guards.derive_ladder`, used to verify the payload's
 * ladder is consistent with its slug (a mismatch means a malformed/tampered payload).
 *
 * @example
 * deriveLadder("sport.cricket.ipl") // ["sport", "sport.cricket"]
 * deriveLadder("sport")             // []
 */
function deriveLadder(canonicalSlug: string): string[] {
  const segments = canonicalSlug.split(".");
  const ladder: string[] = [];
  for (let boundary = 1; boundary < segments.length; boundary += 1) {
    ladder.push(segments.slice(0, boundary).join("."));
  }
  return ladder;
}

/** Strip, drop empties, dedup case-insensitively (mirrors `guards._distinct_nonempty`). */
function distinctNonEmpty(terms: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const term of terms) {
    const cleaned = term.trim();
    const key = cleaned.toLowerCase();
    if (cleaned !== "" && !seen.has(key)) {
      seen.add(key);
      result.push(cleaned);
    }
  }
  return result;
}

/**
 * Return why a terminal micro-interest must be rejected, or `null` when it is valid.
 * Backstop for the worker's own validation (`guards.validate_and_dedup`) — a privileged
 * mint must never trust its input. Depth of a slug = segment count − 1.
 */
function rejectionReason(microInterest: TerminalMicroInterest): string | null {
  // Runtime shape guard FIRST: TS types are erased, and this is an untrusted boundary
  // (a malformed/tampered payload item with a missing or wrong-typed field must yield a
  // reject REASON, never throw — a thrown TypeError here would take down every valid
  // sibling in the same batch, the opposite of "reject the bad one, persist the rest").
  if (
    microInterest === null ||
    typeof microInterest !== "object" ||
    typeof microInterest.canonical_slug !== "string" ||
    typeof microInterest.display_label !== "string" ||
    !Array.isArray(microInterest.search_anchor_terms) ||
    !Array.isArray(microInterest.ladder)
  ) {
    return "malformed_payload_item";
  }
  const slug = microInterest.canonical_slug.trim().toLowerCase();
  if (slug === "") {
    return "empty_slug";
  }
  const segments = slug.split(".");
  if (!TOPIC_ROOT_SLUGS.has(segments[0])) {
    return "slug_not_root_anchored";
  }
  if (segments.length > MAX_SLUG_SEGMENTS) {
    return "slug_too_deep";
  }
  if (!segments.every((segment) => SLUG_SEGMENT_RE.test(segment))) {
    return "malformed_slug_segment";
  }
  if (microInterest.display_label.trim() === "") {
    return "empty_display_label";
  }
  if (distinctNonEmpty(microInterest.search_anchor_terms).length < MIN_ANCHOR_TERMS) {
    return "under_two_anchor_terms";
  }
  // Ladder integrity (acceptance criterion 2): the payload's ladder MUST equal the chain
  // derived from the slug, so a persisted node's parent chain always retraces the drill path.
  const expectedLadder = deriveLadder(slug);
  const providedLadder = microInterest.ladder.map((rung) => String(rung).trim().toLowerCase());
  if (providedLadder.length !== expectedLadder.length || providedLadder.some((rung, i) => rung !== expectedLadder[i])) {
    return "ladder_slug_mismatch";
  }
  return null;
}

/**
 * Persist a completed interview's terminal payload for one user, scoped to their
 * `auth.uid()` (= `userId`).
 *
 * For each ACCEPTED micro-interest: mint its full root→leaf node chain via the
 * `mint_interest_ladder` RPC (idempotent, cross-user convergent) and upsert a deep
 * `user_interest_profile` row (depth-weighted, strict-flagged, carrying the user's own
 * display label). Nodes are minted FIRST, then the profile rows are written in ONE batch
 * upsert — so a failure mid-run leaves no orphaned half-profile and a retry re-runs
 * cleanly (mint is a no-op on the second pass, the profile upsert is idempotent).
 *
 * Rejected items (slug not root-anchored, < 2 anchor terms, malformed, ladder mismatch)
 * are surfaced in {@link PersistInterviewResult.rejected_interests} and logged with a
 * `fix_suggestion` — the rest still persist; nothing is silently invented (Rule 12).
 *
 * An EMPTY payload (skipped through with no roots lit) writes no profile rows but STILL
 * upserts the default traits row (feed-eligible degenerate profile). Like the picker
 * path, this deliberately does NOT stamp `users.user_onboarded_at` — completion is owned
 * solely by {@link import("@/lib/onboardingProfile").markOnboardingComplete} at the true
 * end of the flow (onboarding-gate invariant, 2026-06-30).
 *
 * @param userId - The authed user's id (`auth.uid()`); every row is scoped to it.
 * @param payload - The validated terminal micro-interest list from the interview.
 * @param opts - Optional {@link PersistInterviewOptions} (e.g. `profile_source`).
 * @param client - Optional Supabase client (injected in tests; defaults to the browser client).
 * @returns A {@link PersistInterviewResult} — rows written + any rejected micro-interests.
 * @throws If a mint RPC or a write fails (surfaced, never swallowed — Rule 12).
 *
 * @example
 * const result = await persistInterviewInterests(session.user.id, payload);
 * result.minted_interest_count; // 5
 * result.rejected_interests;    // [] — nothing dropped
 */
export async function persistInterviewInterests(
  userId: string,
  payload: InterviewTerminalPayload,
  opts: PersistInterviewOptions = {},
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<PersistInterviewResult> {
  const profileSource: InterestProfileSource = opts.profile_source ?? "typed";
  const microInterests = payload.micro_interests ?? [];
  logger.info("persist_interview_interests_started", {
    interest_count: microInterests.length,
    roots_only_fallback: payload.roots_only_fallback ?? false,
    profile_source: profileSource,
  });

  const rejected_interests: RejectedInterest[] = [];
  // Keyed by leaf interest_id so a duplicate slug (identical leaf) cannot produce two rows
  // in one upsert batch (Postgres forbids ON CONFLICT affecting a row twice per statement).
  const profileRowByInterestId = new Map<string, InterviewProfileUpsertRow>();

  for (const microInterest of microInterests) {
    const reason = rejectionReason(microInterest);
    if (reason) {
      // `microInterest` may be malformed (non-string slug), so coerce for the label rather
      // than calling string methods on it — the guard above already classified WHY.
      const rejectedSlug =
        typeof microInterest?.canonical_slug === "string" ? microInterest.canonical_slug.trim().toLowerCase() : "";
      rejected_interests.push({ canonical_slug: rejectedSlug || "<empty>", reason });
      logger.warn("interview_interest_rejected", {
        canonical_slug: rejectedSlug || "<empty>",
        reason,
        fix_suggestion:
          "Terminal payload failed the persistence backstop; confirm the worker's guards.validate_and_dedup output matches spec §4.",
      });
      continue;
    }

    const slug = microInterest.canonical_slug.trim().toLowerCase();
    const depthLevel = slug.split(".").length - 1;
    const searchQuery = distinctNonEmpty(microInterest.search_anchor_terms).join(", ");

    // Mint the full root→leaf ladder (idempotent upsert-by-slug) and get the leaf id. A DB
    // error here is a HARD failure (surface it — Rule 12); it is not an "invalid item".
    const { data: leafInterestId, error: mintError } = await client.rpc("mint_interest_ladder", {
      p_canonical_slug: slug,
      p_search_query: searchQuery,
    });
    if (mintError) {
      logger.error("interview_mint_ladder_failed", {
        canonical_slug: slug,
        error_message: mintError.message,
        fix_suggestion: "Confirm migration 0025 applied (mint_interest_ladder RPC) and the user is authed.",
      });
      throw new Error(
        `Failed to mint interest ladder for "${slug}": ${mintError.message}. ` +
          "fix_suggestion: confirm migration 0025 applied (mint_interest_ladder RPC).",
      );
    }
    const interestId = leafInterestId as string;

    profileRowByInterestId.set(interestId, {
      profile_user_id: userId,
      profile_interest_id: interestId,
      profile_weight: resolveProfileWeight(depthLevel),
      profile_source: profileSource,
      profile_is_strict: microInterest.strict,
      profile_display_label: microInterest.display_label.trim(),
    });
  }

  // Replace-mode guard (issue #9): an EMPTY accepted set must never wipe the profile.
  // Either the payload was empty (a rebuild skip-through — the caller should treat that
  // as "keep the old profile", not persist) or the user confirmed REAL interests and the
  // backstop rejected every one (a worker/client bug). Throw (retryable) before ANY write.
  if (opts.replace_existing && profileRowByInterestId.size === 0) {
    logger.error("interview_replace_empty_set_refused", {
      payload_count: microInterests.length,
      rejected_count: rejected_interests.length,
      fix_suggestion:
        microInterests.length > 0
          ? "Every confirmed micro-interest failed the persistence backstop; the old profile was kept. " +
            "Check the worker's guards.validate_and_dedup output against spec §4."
          : "Empty terminal payload with replace semantics; the caller should keep the old profile instead.",
    });
    throw new Error(
      microInterests.length > 0
        ? "Every confirmed interest was rejected by the persistence backstop — keeping your current profile. " +
            "fix_suggestion: check the worker terminal payload against spec §4."
        : "Refusing to replace the profile with an empty set — keeping your current profile. " +
            "fix_suggestion: treat an empty rebuild confirm as keep-old, not replace.",
    );
  }

  // Write the deep profile rows in ONE batch upsert on the unique (user, interest) pair.
  const profileRows = [...profileRowByInterestId.values()];
  if (profileRows.length > 0) {
    const { error: profileError } = await client
      .from("user_interest_profile")
      .upsert(profileRows, { onConflict: "profile_user_id,profile_interest_id" });
    if (profileError) {
      logger.error("persist_interview_profile_upsert_failed", {
        error_message: profileError.message,
        fix_suggestion: "Confirm the user is authed and user_interest_profile owner-all RLS permits the write.",
      });
      throw new Error(
        `Failed to persist interview interest profile: ${profileError.message}. ` +
          "fix_suggestion: confirm the user is authed and RLS permits the owner write.",
      );
    }
  }

  // Default traits row (upsert on the unique traits_user_id) — keeps the degenerate
  // roots-only / skip-everything profile feed-eligible, mirroring the picker path.
  // Runs BEFORE the replace delete below, so the destructive step is always LAST: any
  // failure up to here leaves the old rows live (in replace mode: a retryable superset,
  // tagged partial — never "replaced but told otherwise").
  const { error: traitsError } = await client
    .from("user_interest_traits")
    .upsert({ traits_user_id: userId }, { onConflict: "traits_user_id" });
  if (traitsError) {
    logger.error("persist_interview_traits_failed", {
      error_message: traitsError.message,
      fix_suggestion: "Confirm user_interest_traits owner-all RLS permits the write.",
    });
    const traitsMessage =
      `Failed to persist interview interest traits: ${traitsError.message}. ` +
      "fix_suggestion: confirm RLS permits the owner write.";
    // In replace mode the new profile rows already landed → a post-upsert (partial) failure.
    throw opts.replace_existing ? replacePartialError(traitsMessage) : new Error(traitsMessage);
  }

  // Replace mode (issue #9): delete this user's stale profile rows — everything NOT in
  // the confirmed set — so the profile ends EQUAL to the re-interview's terminal list.
  // Deliberately the LAST write: if it fails, the profile is temporarily a superset
  // (old rows still live → the feed still works), the error surfaces as PARTIAL for a
  // retry, and the idempotent retry completes the replace.
  if (opts.replace_existing) {
    let staleDelete = client.from("user_interest_profile").delete().eq("profile_user_id", userId);
    const keptInterestIds = [...profileRowByInterestId.keys()];
    if (keptInterestIds.length > 0) {
      // PostgREST `in` filter list: `(id1,id2,...)` — UUIDs need no quoting.
      staleDelete = staleDelete.not("profile_interest_id", "in", `(${keptInterestIds.join(",")})`);
    }
    const { error: deleteError } = await staleDelete;
    if (deleteError) {
      logger.error("interview_replace_stale_delete_failed", {
        error_message: deleteError.message,
        kept_interest_count: keptInterestIds.length,
        fix_suggestion:
          "New rows are already persisted (old rows still live — feed keeps working); retry to finish the replace.",
      });
      throw replacePartialError(
        `Failed to delete stale interest profile rows: ${deleteError.message}. ` +
          "fix_suggestion: retry the confirm — the new rows are saved and the replace finishes idempotently.",
      );
    }
  }

  const result: PersistInterviewResult = {
    minted_interest_count: profileRows.length,
    rejected_interests,
  };
  logger.info("persist_interview_interests_completed", {
    minted_interest_count: result.minted_interest_count,
    rejected_count: result.rejected_interests.length,
  });
  return result;
}
