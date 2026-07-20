import { describe, expect, it } from "vitest";

/**
 * Pure-helper tests for the in-chat source pickers (slice #20). Each asserts a load-bearing
 * business rule (Rule 9), not incidental shape:
 *   - relevance sort: channels covering an EARLIER interview root come first (the whole point
 *     of "sorted by relevance to the interview picks");
 *   - supply expectation: sums CATALOG cadence estimates (no live signal) and never rounds a
 *     real pick down to a discouraging 0/day;
 *   - grouping: user roots first (pick order) + a single "beyond your picks" bucket;
 *   - cluster-pick expansion: a cluster follow carries its ref id + member partition.
 */

import {
  channelVideosPerWeek,
  DEFAULT_CHANNEL_VIDEOS_PER_WEEK,
  estimateDailyVideoSupply,
  formatSupplyExpectation,
  groupClustersForPicker,
  resolveClusterPicks,
  sortChannelsByRelevance,
} from "@/lib/onboardingSourcePicks";
import type { ResolvedCluster } from "@/lib/sourceClusters";
import type { ContentSource } from "@/types/source";

/** Minimal channel row factory — only the fields the helpers read. */
function channel(overrides: Partial<ContentSource> & { source_id: string; source_name: string }): ContentSource {
  return {
    content_source_type: "youtube_channel",
    external_id: `ext-${overrides.source_id}`,
    source_description: null,
    thumbnail_url: null,
    subscriber_count: null,
    platform_metadata: null,
    personas: [],
    topic_tags: [],
    popularity_score: 50,
    is_curated: true,
    last_fetched_at: null,
    ...overrides,
  };
}

function cluster(overrides: Partial<ResolvedCluster> & { cluster_slug: string }): ResolvedCluster {
  return {
    cluster_id: `id-${overrides.cluster_slug}`,
    cluster_label: overrides.cluster_slug,
    cluster_category: "ai",
    cluster_sort_order: 0,
    members: [],
    ...overrides,
  };
}

describe("sortChannelsByRelevance", () => {
  it("orders channels by the user's FIRST-matching root, in pick order (happy path)", () => {
    const sportChannel = channel({ source_id: "s", source_name: "Sport One", topic_tags: ["sport"] });
    const aiChannel = channel({ source_id: "a", source_name: "AI One", topic_tags: ["ai"] });
    const sorted = sortChannelsByRelevance([sportChannel, aiChannel], ["ai", "sport"]);
    expect(sorted.map((c) => c.source_id)).toEqual(["a", "s"]);
  });

  it("sinks channels with no root overlap to the end (beyond the picks)", () => {
    const matched = channel({ source_id: "m", source_name: "Matched", topic_tags: ["ai"] });
    const unmatched = channel({ source_id: "u", source_name: "Unmatched", topic_tags: ["business"] });
    const sorted = sortChannelsByRelevance([unmatched, matched], ["ai"]);
    expect(sorted.map((c) => c.source_id)).toEqual(["m", "u"]);
  });

  it("breaks ties by popularity desc then name (stable, no interview roots)", () => {
    const low = channel({ source_id: "l", source_name: "Alpha", topic_tags: ["ai"], popularity_score: 10 });
    const high = channel({ source_id: "h", source_name: "Zeta", topic_tags: ["ai"], popularity_score: 90 });
    const sorted = sortChannelsByRelevance([low, high], []);
    expect(sorted.map((c) => c.source_id)).toEqual(["h", "l"]);
  });

  it("does not mutate its input", () => {
    const input = [
      channel({ source_id: "b", source_name: "B", topic_tags: ["sport"] }),
      channel({ source_id: "a", source_name: "A", topic_tags: ["ai"] }),
    ];
    const before = input.map((c) => c.source_id);
    sortChannelsByRelevance(input, ["ai"]);
    expect(input.map((c) => c.source_id)).toEqual(before);
  });
});

describe("supply expectation (catalog-only)", () => {
  it("reads a measured platform_metadata.videos_per_week when present", () => {
    const measured = channel({ source_id: "m", source_name: "M", platform_metadata: { videos_per_week: 14 } });
    expect(channelVideosPerWeek(measured)).toBe(14);
  });

  it("falls back to the v1 default when the row carries no measured cadence", () => {
    const bare = channel({ source_id: "b", source_name: "B" });
    expect(channelVideosPerWeek(bare)).toBe(DEFAULT_CHANNEL_VIDEOS_PER_WEEK);
  });

  it("ignores a non-positive / non-finite measured cadence (edge case)", () => {
    const zero = channel({ source_id: "z", source_name: "Z", platform_metadata: { videos_per_week: 0 } });
    const bad = channel({ source_id: "n", source_name: "N", platform_metadata: { videos_per_week: "lots" } });
    expect(channelVideosPerWeek(zero)).toBe(DEFAULT_CHANNEL_VIDEOS_PER_WEEK);
    expect(channelVideosPerWeek(bad)).toBe(DEFAULT_CHANNEL_VIDEOS_PER_WEEK);
  });

  it("sums weekly cadence into a whole videos/day, matching the PRD ~12→~5 example", () => {
    const twelve = Array.from({ length: 12 }, (_, i) => channel({ source_id: `c${i}`, source_name: `C${i}` }));
    // 12 channels × 3/week ÷ 7 = 5.14 → 5.
    expect(estimateDailyVideoSupply(twelve)).toBe(5);
  });

  it("never rounds a real pick down to 0/day (min 1 when any selected)", () => {
    const one = [channel({ source_id: "c", source_name: "C", platform_metadata: { videos_per_week: 1 } })];
    expect(estimateDailyVideoSupply(one)).toBe(1);
    expect(estimateDailyVideoSupply([])).toBe(0);
  });

  it("formats the line with correct pluralization and a prompt when empty", () => {
    expect(formatSupplyExpectation([])).toBe("Pick a few channels to see your daily video supply.");
    const oneChannel = [channel({ source_id: "c", source_name: "C", platform_metadata: { videos_per_week: 7 } })];
    expect(formatSupplyExpectation(oneChannel)).toBe("these 1 channel ≈ ~1 long-form video/day");
  });
});

describe("groupClustersForPicker", () => {
  it("lists the user's roots first (pick order), then a single beyond-your-picks group", () => {
    const byRoot = new Map<string, ResolvedCluster[]>([
      ["ai", [cluster({ cluster_slug: "ai-labs", cluster_category: "ai" })]],
      ["sport", [cluster({ cluster_slug: "f1", cluster_category: "sport" })]],
      ["business", [cluster({ cluster_slug: "vc", cluster_category: "business" })]],
    ]);
    const groups = groupClustersForPicker(byRoot, ["sport", "ai"]);
    expect(groups.map((g) => g.root)).toEqual(["sport", "ai", null]);
    expect(groups[2].is_beyond).toBe(true);
    expect(groups[2].clusters.map((c) => c.cluster_slug)).toEqual(["vc"]);
  });

  it("drops empty groups and omits the beyond group when the user picked every root", () => {
    const byRoot = new Map<string, ResolvedCluster[]>([
      ["ai", [cluster({ cluster_slug: "ai-labs" })]],
      ["sport", []],
    ]);
    const groups = groupClustersForPicker(byRoot, ["ai", "sport"]);
    expect(groups.map((g) => g.root)).toEqual(["ai"]);
  });

  it("puts EVERYTHING under beyond-your-picks when the user picked nothing (skip-everything)", () => {
    const byRoot = new Map<string, ResolvedCluster[]>([["ai", [cluster({ cluster_slug: "ai-labs" })]]]);
    const groups = groupClustersForPicker(byRoot, []);
    expect(groups).toHaveLength(1);
    expect(groups[0].is_beyond).toBe(true);
  });
});

describe("resolveClusterPicks", () => {
  it("carries the cluster ref id + partitions members into source / personality ids", () => {
    const picked = cluster({
      cluster_slug: "ai-labs",
      cluster_id: "cl-1",
      members: [
        { kind: "source", followable_id: "src-1", display_name: "@a", popularity_score: 50 },
        { kind: "personality", followable_id: "per-1", display_name: "Jane", popularity_score: 50 },
        { kind: "source", followable_id: "src-2", display_name: "@b", popularity_score: 50 },
      ],
    });
    const picks = resolveClusterPicks([picked]);
    expect(picks).toEqual([
      {
        cluster_id: "cl-1",
        cluster_slug: "ai-labs",
        member_source_ids: ["src-1", "src-2"],
        member_personality_ids: ["per-1"],
      },
    ]);
  });

  it("skips a cluster with no cluster_id rather than writing a ref-less follow (defensive)", () => {
    const refless: ResolvedCluster = {
      cluster_slug: "orphan",
      cluster_label: "Orphan",
      cluster_category: "ai",
      cluster_sort_order: 0,
      members: [{ kind: "source", followable_id: "src-1", display_name: "@a", popularity_score: 50 }],
    };
    expect(resolveClusterPicks([refless])).toEqual([]);
  });
});
