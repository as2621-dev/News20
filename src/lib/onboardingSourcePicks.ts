/**
 * Pure helpers for the in-chat YOUTUBE grid + X CLUSTERS pickers (slice #20).
 *
 * No I/O, no React, no clock — data in, view-model / payload out — so every rule
 * (relevance sort, the supply-expectation guesstimate, the beyond-your-picks grouping,
 * the cluster-follow expansion) is unit-tested in isolation (Rule 9). The pickers hold
 * the catalog rows in state and call these; the closing-arc persist reads
 * {@link resolveClusterPicks}'s output straight off the terminal payload — the catalog
 * is read ONCE when the picker loads, never at persist time (no live API during onboarding).
 */

import type { ResolvedCluster } from "@/lib/sourceClusters";
import type { InterviewClusterPick } from "@/types/interview";
import type { ContentSource } from "@/types/source";

/**
 * The v1 per-channel cadence guesstimate (long-form videos/week) used when the catalog
 * row carries no measured rate. Guesstimate quality is explicitly acceptable v1 (PRD
 * story #19): the supply line is a grounding hint, not a contract. ~3/week per channel
 * yields the PRD's "~12 channels ≈ ~5/day" (12 × 3 ÷ 7 ≈ 5).
 */
export const DEFAULT_CHANNEL_VIDEOS_PER_WEEK = 3;

/** A channel below its own relevance floor (no topic overlap with the user) sorts last. */
const NO_MATCH_RANK = Number.POSITIVE_INFINITY;

/**
 * One channel's cadence estimate (long-form videos/week): the catalog row's measured
 * `platform_metadata.videos_per_week` when present and positive-finite, else the shared
 * v1 default. Catalog-only — never a live probe.
 *
 * @param channel - A `content_sources` youtube_channel row.
 * @returns The channel's videos/week estimate (always > 0).
 */
export function channelVideosPerWeek(channel: Pick<ContentSource, "platform_metadata">): number {
  const raw = channel.platform_metadata?.videos_per_week;
  if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) {
    return raw;
  }
  return DEFAULT_CHANNEL_VIDEOS_PER_WEEK;
}

/**
 * The estimated daily long-form video supply from a set of selected channels — the sum
 * of their weekly cadence estimates ÷ 7, rounded to a whole number (min 1 when any
 * channel is selected, so a small pick never rounds to a discouraging "0/day").
 *
 * @param channels - The selected channels.
 * @returns Whole videos/day (0 when nothing is selected).
 */
export function estimateDailyVideoSupply(channels: ReadonlyArray<Pick<ContentSource, "platform_metadata">>): number {
  if (channels.length === 0) {
    return 0;
  }
  const weekly = channels.reduce((sum, channel) => sum + channelVideosPerWeek(channel), 0);
  return Math.max(1, Math.round(weekly / 7));
}

/**
 * The supply-expectation line for the YouTube picker, recomputed live as tiles toggle
 * (catalog-only, no network). Zero selected → a prompt to pick.
 *
 * @param channels - The currently-selected channels.
 * @returns Copy like `these 12 channels ≈ ~5 long-form videos/day`.
 */
export function formatSupplyExpectation(channels: ReadonlyArray<Pick<ContentSource, "platform_metadata">>): string {
  const count = channels.length;
  if (count === 0) {
    return "Pick a few channels to see your daily video supply.";
  }
  const perDay = estimateDailyVideoSupply(channels);
  const channelWord = count === 1 ? "channel" : "channels";
  const videoWord = perDay === 1 ? "video" : "videos";
  return `these ${count} ${channelWord} ≈ ~${perDay} long-form ${videoWord}/day`;
}

/**
 * The relevance rank of a channel against the user's ordered interview roots: the index
 * of the FIRST of the user's roots the channel's `topic_tags` covers (0 = most relevant,
 * matches the user's top pick). A channel with no overlap ranks last (beyond the picks).
 */
function channelRelevanceRank(channel: Pick<ContentSource, "topic_tags">, orderedRoots: readonly string[]): number {
  let best = NO_MATCH_RANK;
  const tags = new Set(channel.topic_tags);
  for (let index = 0; index < orderedRoots.length; index += 1) {
    if (tags.has(orderedRoots[index])) {
      best = index;
      break;
    }
  }
  return best;
}

/**
 * Sort channel tiles by relevance to the interview picks: channels covering an earlier
 * user root first, then (stable ties) by catalog popularity desc, then name asc. Channels
 * with no root overlap trail the grid (still selectable — the grid is the full catalog).
 * Pure + does not mutate the input.
 *
 * @param channels - The candidate youtube channels.
 * @param orderedRoots - The user's interview roots, most-wanted first (e.g. `["sport","ai"]`).
 * @returns A NEW array, most-relevant first.
 */
export function sortChannelsByRelevance(
  channels: readonly ContentSource[],
  orderedRoots: readonly string[],
): ContentSource[] {
  return [...channels].sort((a, b) => {
    const rankA = channelRelevanceRank(a, orderedRoots);
    const rankB = channelRelevanceRank(b, orderedRoots);
    if (rankA !== rankB) {
      return rankA - rankB;
    }
    if (a.popularity_score !== b.popularity_score) {
      return b.popularity_score - a.popularity_score;
    }
    return a.source_name.localeCompare(b.source_name);
  });
}

/** One rendered group in the X CLUSTERS picker (a user category, or the beyond-picks bucket). */
export interface ClusterPickerGroup {
  /** The root slug for a category group; `null` for the "beyond your picks" group. */
  root: string | null;
  /** Whether this is the trailing "beyond your picks" group. */
  is_beyond: boolean;
  /** The group's clusters, in resolver order. */
  clusters: ResolvedCluster[];
}

/**
 * Group resolved clusters for the X picker: the user's chosen roots FIRST (in pick order),
 * each with its clusters, then a single "beyond your picks" group holding every cluster in
 * a root the user did NOT pick (PRD: samples + beyond group). Empty groups are dropped.
 * Pure — reads the already-resolved catalog the picker loaded.
 *
 * @param clustersByRoot - `getClustersForCategories` output keyed by root slug.
 * @param orderedRoots - The user's interview roots, most-wanted first.
 * @returns Category groups (pick order) then at most one beyond-your-picks group.
 */
export function groupClustersForPicker(
  clustersByRoot: ReadonlyMap<string, ResolvedCluster[]>,
  orderedRoots: readonly string[],
): ClusterPickerGroup[] {
  const groups: ClusterPickerGroup[] = [];
  const pickedRoots = new Set(orderedRoots);

  for (const root of orderedRoots) {
    const clusters = clustersByRoot.get(root) ?? [];
    if (clusters.length > 0) {
      groups.push({ root, is_beyond: false, clusters });
    }
  }

  // Beyond-your-picks: clusters in any root the user did NOT pick, in map iteration order.
  const beyondClusters: ResolvedCluster[] = [];
  for (const [root, clusters] of clustersByRoot) {
    if (!pickedRoots.has(root)) {
      beyondClusters.push(...clusters);
    }
  }
  if (beyondClusters.length > 0) {
    groups.push({ root: null, is_beyond: true, clusters: beyondClusters });
  }

  return groups;
}

/**
 * Expand the user's selected X clusters into the terminal-persist payload shape
 * ({@link InterviewClusterPick}[]): each carries its cluster ref id + its members
 * partitioned into source / personality follow ids. A cluster missing a `cluster_id`
 * (should never happen — the resolver always sets it) is SKIPPED loudly rather than
 * writing a ref-less follow. Pure — the picker already resolved the members.
 *
 * @param selectedClusters - The resolved clusters the user selected.
 * @returns One {@link InterviewClusterPick} per selectable cluster.
 */
export function resolveClusterPicks(selectedClusters: readonly ResolvedCluster[]): InterviewClusterPick[] {
  const picks: InterviewClusterPick[] = [];
  for (const cluster of selectedClusters) {
    if (cluster.cluster_id === undefined) {
      continue;
    }
    const memberSourceIds: string[] = [];
    const memberPersonalityIds: string[] = [];
    for (const member of cluster.members) {
      if (member.kind === "source") {
        memberSourceIds.push(member.followable_id);
      } else {
        memberPersonalityIds.push(member.followable_id);
      }
    }
    picks.push({
      cluster_id: cluster.cluster_id,
      cluster_slug: cluster.cluster_slug,
      member_source_ids: memberSourceIds,
      member_personality_ids: memberPersonalityIds,
    });
  }
  return picks;
}
