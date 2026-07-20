/**
 * Terminal-persist orchestrator (FSR data core, #30) — the SINGLE seam the interview's closing
 * arc (#19) calls to commit a confirmed terminal payload. It writes all four per-user surfaces of
 * a completed interview in one call, in destructive-last order, mirroring the partial-error
 * contract already established in {@link persistInterviewInterests}:
 *
 *   1. interests   → {@link persistInterviewInterests}   (mints taxonomy nodes + deep profile)
 *   2. mutes       → {@link persistMuteTerms}             (SKIP-TUNE hard filters)
 *   3. allocation  → {@link saveUserFeedAllocation}       (the closing-arc split → coarse "Build your 30")
 *   4. deferred    → {@link persistDeferredQuestions}     (skipped questions, for later resurfacing)
 *
 * ── The exactly-30 split is rejected AUTHORITATIVELY here ─────────────────────────
 * `payload.top_split` is CLIENT-COMPUTED (the worker never sends it), so this boundary — not the
 * UI — is where a split that does not sum to {@link TOP_SPLIT_TOTAL} (30) is refused. The check
 * runs at the TOP, before ANY write, so a bad split can never land a partial allocation (or any
 * other row). This satisfies the AC's "throw before any allocation write" with the strongest
 * ordering (throw before every write).
 *
 * ── Deliberately does NOT stamp `user_onboarded_at` ──────────────────────────────
 * Onboarding completion stays owned SOLELY by
 * {@link import("@/lib/onboardingProfile").markOnboardingComplete} at the true end of the flow
 * (onboarding-gate invariant, 2026-06-30). This orchestrator persists interview state only — a
 * caller that also wants to end onboarding calls `markOnboardingComplete` separately.
 *
 * ── Clean-replace (rebuild-my-feed) ──────────────────────────────────────────────
 * `replace_existing` threads clean-replace to interests, mutes, and deferred. Interests is
 * destructive-LAST internally (new rows upserted, then stale rows pruned — old profile stays live
 * on failure); mutes and deferred are delete-FIRST internally (subtractive sets, so a mid-run
 * failure only widens what's shown, never loses feed content, and a full retry converges
 * idempotently). Across the FOUR steps the destructive STEP still lands last (deferred is the tail).
 * The allocation write needs no flag: `saveUserFeedAllocation` ALWAYS prunes stale COARSE rows via
 * its `.is(interest_id,null).is(section_label,null)`-scoped delete, so it clean-replaces the coarse
 * blocks while leaving the backend niche/section rows (#12) untouched.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { type SaveAllocationResult, saveUserFeedAllocation } from "@/lib/feedAllocation";
import { categoryBucketsFromMicroInterests } from "@/lib/feedBuckets";
import { splitToAllocationSegments, TOP_SPLIT_TOTAL, type TopSplit } from "@/lib/feedTopSplit";
import {
  type PersistDeferredQuestionsResult,
  type PersistInterviewResult,
  type PersistMuteTermsResult,
  persistDeferredQuestions,
  persistInterviewInterests,
  persistMuteTerms,
} from "@/lib/interviewProfile";
import { logger } from "@/lib/logger";
import { commitClusterFollowSet, commitUserClusterFollows } from "@/lib/sourceClusters";
import { followSource } from "@/lib/sources";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { InterviewSourceFollows, InterviewTerminalPayload } from "@/types/interview";

/** Options for {@link persistOnboardingTerminal}. */
export interface PersistOnboardingTerminalOptions {
  /**
   * Rebuild-my-feed semantics: thread clean-replace to interests, mutes, and deferred so each
   * per-user set ends EQUAL to this interview's terminal list (no orphans). The allocation write
   * is always a clean coarse replace regardless. Default `false` (first-run onboarding).
   */
  replace_existing?: boolean;
}

/** Typed outcome of a {@link persistOnboardingTerminal} run — the four sub-writes' results. */
export interface PersistOnboardingTerminalResult {
  /** Interests write outcome (minted rows + any rejected micro-interests). */
  interests: PersistInterviewResult;
  /** Mute-terms write outcome (how many mutes the user ends with). */
  mutes: PersistMuteTermsResult;
  /** Feed-allocation write outcome (coarse rows persisted). */
  allocation: SaveAllocationResult;
  /** Deferred-questions write outcome (how many skipped-question rows the user ends with). */
  deferred: PersistDeferredQuestionsResult;
  /** In-chat source-picks write outcome (channel + cluster-member + cluster-ref follows). */
  sourceFollows: PersistSourceFollowsResult;
}

/** Counts written by the in-chat source-picks persist (slice #20). */
export interface PersistSourceFollowsResult {
  /** YouTube channel sources followed (→ `user_content_sources`). */
  youtube_channels_followed: number;
  /** Distinct cluster-member sources followed (→ `user_content_sources`). */
  cluster_member_sources_followed: number;
  /** Distinct cluster-member personalities followed (→ `user_personalities`). */
  cluster_member_personalities_followed: number;
  /** Cluster refs written (→ `user_source_clusters`, for sweep scheduling / theme attribution). */
  clusters_followed: number;
}

/**
 * Persist the in-chat YOUTUBE + X CLUSTERS picks (slice #20): channel sources via
 * {@link followSource}, cluster MEMBERS (deduped across clusters) via
 * {@link commitClusterFollowSet}, and cluster REFS via {@link commitUserClusterFollows}.
 * All three primitives are idempotent upserts, so a retry converges. An absent /
 * empty-on-both-axes payload writes NOTHING and does not error (the zero-pick path —
 * those youtube/x slots default to news at assembly).
 */
async function persistSourceFollows(
  sourceFollows: InterviewSourceFollows | undefined,
  client: SupabaseClient,
): Promise<PersistSourceFollowsResult> {
  const empty: PersistSourceFollowsResult = {
    youtube_channels_followed: 0,
    cluster_member_sources_followed: 0,
    cluster_member_personalities_followed: 0,
    clusters_followed: 0,
  };
  if (!sourceFollows) {
    return empty;
  }

  // 1. YouTube channel follows (idempotent upserts). Dedup first so the count is honest even if the
  //    picker ever emits a repeat (its selection is a Set today, but don't rely on that here).
  const youtubeSourceIds = [...new Set(sourceFollows.youtube_source_ids)];
  for (const sourceId of youtubeSourceIds) {
    await followSource(sourceId, undefined, client);
  }

  // 2. Cluster follows: expand to member rows (deduped across clusters) + write cluster refs.
  const memberSourceIds = new Set<string>();
  const memberPersonalityIds = new Set<string>();
  const clusterIds: string[] = [];
  for (const cluster of sourceFollows.clusters) {
    clusterIds.push(cluster.cluster_id);
    for (const id of cluster.member_source_ids) {
      memberSourceIds.add(id);
    }
    for (const id of cluster.member_personality_ids) {
      memberPersonalityIds.add(id);
    }
  }
  // Member rows BEFORE the cluster ref (intentional order): both are idempotent upserts and the whole
  // terminal call rejects (no gate stamp) on any failure, so a retry converges. If only one half could
  // land, member follows are the safer half — the user still gets content; only cluster-level sweep
  // scheduling would be missing until the retry.
  await commitClusterFollowSet({ sources: [...memberSourceIds], personalities: [...memberPersonalityIds] }, client);
  const clusterRefs = await commitUserClusterFollows(clusterIds, client);

  return {
    youtube_channels_followed: youtubeSourceIds.length,
    cluster_member_sources_followed: memberSourceIds.size,
    cluster_member_personalities_followed: memberPersonalityIds.size,
    clusters_followed: clusterRefs.clusters_followed,
  };
}

/**
 * Validate the closing-arc split, or throw a loud, actionable error (Rule 12). A split is valid
 * only when all three axes are finite non-negative integers summing to EXACTLY
 * {@link TOP_SPLIT_TOTAL}. Runs before any write so a bad split never lands a partial row.
 */
function requireExactThirtySplit(topSplit: InterviewTerminalPayload["top_split"]): TopSplit {
  const news = topSplit?.news;
  const youtube = topSplit?.youtube;
  const x = topSplit?.x;
  const isNonNegativeInt = (axis: number | undefined): axis is number =>
    Number.isInteger(axis) && (axis as number) >= 0;
  if (
    !isNonNegativeInt(news) ||
    !isNonNegativeInt(youtube) ||
    !isNonNegativeInt(x) ||
    news + youtube + x !== TOP_SPLIT_TOTAL
  ) {
    const total = (news ?? Number.NaN) + (youtube ?? Number.NaN) + (x ?? Number.NaN);
    logger.error("onboarding_terminal_split_not_30", {
      news: news ?? null,
      youtube: youtube ?? null,
      x: x ?? null,
      total: Number.isNaN(total) ? null : total,
      expected_total: TOP_SPLIT_TOTAL,
      fix_suggestion:
        "The closing-arc top_split must be three non-negative integers summing to 30; fix the caller before persisting.",
    });
    throw new Error(
      `Cannot persist onboarding terminal: top_split must sum to ${TOP_SPLIT_TOTAL} ` +
        `(got news=${String(news)}, youtube=${String(youtube)}, x=${String(x)}). ` +
        "fix_suggestion: the closing-arc split must be three non-negative integers totalling 30.",
    );
  }
  return { news, youtube, x };
}

/**
 * Persist a confirmed interview terminal payload for one user in ONE call (interests, mutes,
 * allocation, deferred), scoped to their `auth.uid()` (= `userId`). The single seam #19 calls.
 *
 * Rejects a `top_split` that does not sum to 30 BEFORE any write. Writes destructive-last so a
 * failure mid-run leaves earlier steps' data live (and, in replace mode, retryable) rather than a
 * torn set — a full retry converges idempotently (interests upsert + mint, mutes upsert, allocation
 * upsert+prune, deferred delete+insert are each idempotent). Deliberately does NOT stamp
 * `user_onboarded_at`.
 *
 * @param userId - The authed user's id (`auth.uid()`); every row is scoped to it.
 * @param payload - The confirmed terminal payload, INCLUDING the client-computed `top_split`.
 * @param opts - Optional {@link PersistOnboardingTerminalOptions} (e.g. `replace_existing`).
 * @param client - Optional Supabase client (injected in tests; defaults to the browser client).
 * @returns A {@link PersistOnboardingTerminalResult} — the four sub-writes' outcomes.
 * @throws If the split ≠ 30, or any sub-write fails (surfaced, never swallowed — Rule 12).
 *
 * @example
 * await persistOnboardingTerminal(session.user.id, {
 *   micro_interests: [...],
 *   mute_terms: [...],
 *   deferred_questions: [...],
 *   top_split: { news: 20, youtube: 7, x: 3 },
 * });
 */
export async function persistOnboardingTerminal(
  userId: string,
  payload: InterviewTerminalPayload,
  opts: PersistOnboardingTerminalOptions = {},
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<PersistOnboardingTerminalResult> {
  const replaceExisting = opts.replace_existing ?? false;

  // 0. Authoritative exactly-30 gate — BEFORE any write, so a bad split lands nothing (Rule 12).
  const topSplit = requireExactThirtySplit(payload.top_split);

  const microInterests = payload.micro_interests ?? [];
  logger.info("persist_onboarding_terminal_started", {
    interest_count: microInterests.length,
    mute_count: (payload.mute_terms ?? []).length,
    deferred_count: (payload.deferred_questions ?? []).length,
    top_split_news: topSplit.news,
    top_split_youtube: topSplit.youtube,
    top_split_x: topSplit.x,
    replace_existing: replaceExisting,
  });

  // 1. Interests (destructive-last internally). 2. Mutes. Both thread replace_existing.
  const interests = await persistInterviewInterests(userId, payload, { replace_existing: replaceExisting }, client);
  const mutes = await persistMuteTerms(userId, payload.mute_terms ?? [], { replace_existing: replaceExisting }, client);

  // 3. Allocation: the closing-arc split → coarse "Build your 30". saveUserFeedAllocation always
  //    clean-replaces the COARSE rows (prunes stale coarse, spares niche/section rows), so no flag.
  const selectedCategoryBuckets = categoryBucketsFromMicroInterests(microInterests);
  const segments = splitToAllocationSegments(topSplit, selectedCategoryBuckets);
  const allocation = await saveUserFeedAllocation(segments, client);

  // 4. In-chat source picks (slice #20): channel + cluster-member + cluster-ref follows.
  //    Additive idempotent upserts (non-destructive), so ordered before the destructive tail.
  const sourceFollows = await persistSourceFollows(payload.source_follows, client);

  // 5. Deferred (delete-last internally on replace). Never stamps user_onboarded_at.
  const deferred = await persistDeferredQuestions(
    userId,
    payload.deferred_questions ?? [],
    { replace_existing: replaceExisting },
    client,
  );

  logger.info("persist_onboarding_terminal_completed", {
    minted_interest_count: interests.minted_interest_count,
    persisted_mute_count: mutes.persisted_mute_count,
    allocation_persisted_count: allocation.persisted_count,
    persisted_deferred_count: deferred.persisted_deferred_count,
    youtube_channels_followed: sourceFollows.youtube_channels_followed,
    cluster_refs_followed: sourceFollows.clusters_followed,
  });
  return { interests, mutes, allocation, deferred, sourceFollows };
}
