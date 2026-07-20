import { describe, expect, it } from "vitest";
import {
  ALLOCATION_TOTAL,
  type AllocationSegment,
  DEFAULT_ALLOCATION_SEGMENTS,
  DESIGN_BUCKETS,
  type DesignBucketId,
  sumSegmentCounts,
} from "@/lib/feedBuckets";
import { splitToAllocationSegments, type TopSplit } from "@/lib/feedTopSplit";

/**
 * WHY these tests exist (Rule 9 — the split IS the feed shape): the closing-arc `{news,youtube,x}`
 * split becomes the user's "Build your 30". If the largest-remainder spread misses `news` by even
 * one slot, the persisted allocation no longer sums to 30 and the feed is short/over a briefing.
 * So EVERY case asserts `sumSegmentCounts === news+youtube+x` (== 30 for a valid split), and the
 * three shape guarantees the callers depend on (30/0/0 → categories only; 0/0/30 → single x row;
 * empty categories + news>0 → all 8 roots) are pinned exactly.
 */

/** The 8 canonical topic-category roots, in seed order — the roots-only fallback set. */
const CATEGORY_ROOTS: DesignBucketId[] = DEFAULT_ALLOCATION_SEGMENTS.filter(
  ([bucketId]) => DESIGN_BUCKETS[bucketId].kind === "cat",
).map(([bucketId]) => bucketId);

/** Sugar: assert a segment list against `[bucketId, count]` pairs in order. */
function pairs(segments: AllocationSegment[]): Array<[DesignBucketId, number]> {
  return segments.map((s) => [s.bucketId, s.count]);
}

describe("splitToAllocationSegments — largest-remainder correctness + shape guarantees", () => {
  // Table-driven: each row is a split + selected categories + the exact expected segment list.
  const cases: Array<{
    name: string;
    split: TopSplit;
    selected: DesignBucketId[];
    expected: Array<[DesignBucketId, number]>;
  }> = [
    {
      name: "default 20/7/3 with empty categories → all 8 roots (largest-remainder) + yt + x",
      split: { news: 20, youtube: 7, x: 3 },
      selected: [],
      // weights ai4 tech4 geo4 biz4 pol2 env2 sport3 arts3 (Σ26); 20·w/26 floors=18, +2 to the
      // two largest remainders (politics/environment at .538) → 3,3,3,3,2,2,2,2 = 20.
      expected: [
        ["ai", 3],
        ["tech", 3],
        ["geopolitics", 3],
        ["business", 3],
        ["politics", 2],
        ["environment", 2],
        ["sport", 2],
        ["arts", 2],
        ["youtube", 7],
        ["x", 3],
      ],
    },
    {
      name: "30/0/0 → category rows only (no source rows)",
      split: { news: 30, youtube: 0, x: 0 },
      selected: [],
      // 30·w/26 floors = ceil-ish; leftover distributed. Assert only the invariant + no src rows here.
      expected: [], // filled below by the invariant assertion, not an exact match
    },
    {
      name: "0/0/30 → a single x:30 row",
      split: { news: 0, youtube: 0, x: 30 },
      selected: [],
      expected: [["x", 30]],
    },
    {
      name: "0/30/0 → a single youtube:30 row",
      split: { news: 0, youtube: 30, x: 0 },
      selected: [],
      expected: [["youtube", 30]],
    },
    {
      name: "selected ai+business splits news exactly across just those two",
      split: { news: 20, youtube: 7, x: 3 },
      selected: ["business", "ai"],
      // weights ai4 biz4 (Σ8); 20·4/8 = 10 each, no remainder. Emitted in canonical order ai,business.
      expected: [
        ["ai", 10],
        ["business", 10],
        ["youtube", 7],
        ["x", 3],
      ],
    },
    {
      name: "tiny news across all roots gives whole slots to the largest remainders, drops zero rows",
      split: { news: 1, youtube: 0, x: 29 },
      selected: [],
      // 1·w/26 → all floors 0, leftover 1 → the single largest-weight/first bucket (ai, w4) wins it.
      expected: [
        ["ai", 1],
        ["x", 29],
      ],
    },
  ];

  for (const testCase of cases) {
    it(testCase.name, () => {
      const segments = splitToAllocationSegments(testCase.split, testCase.selected);
      const expectedTotal = testCase.split.news + testCase.split.youtube + testCase.split.x;

      // The load-bearing invariant (Rule 9): the spread must hit the split total EXACTLY.
      expect(sumSegmentCounts(segments)).toBe(expectedTotal);

      if (testCase.expected.length > 0) {
        expect(pairs(segments)).toEqual(testCase.expected);
      }
    });
  }

  it("30/0/0 writes category rows only and no source axis rows", () => {
    const segments = splitToAllocationSegments({ news: 30, youtube: 0, x: 0 }, []);
    expect(sumSegmentCounts(segments)).toBe(30);
    expect(segments.some((s) => s.bucketId === "youtube" || s.bucketId === "x")).toBe(false);
    // Every emitted row is a real topic-category root with a positive count.
    expect(segments.every((s) => CATEGORY_ROOTS.includes(s.bucketId) && s.count > 0)).toBe(true);
  });

  it("every emitted segment is coarse (no niche columns) so saveUserFeedAllocation writes it as a coarse block", () => {
    const segments = splitToAllocationSegments({ news: 20, youtube: 7, x: 3 }, []);
    expect(segments.every((s) => s.interestId === undefined && s.sectionLabel === undefined)).toBe(true);
  });

  it("sums to exactly 30 across a fuzz of valid three-axis splits (largest-remainder never drifts)", () => {
    // Rule 9: prove the exact-sum invariant holds for MANY partitions, not just the enumerated few —
    // a rounding bug that only bites at a specific news value would slip past a handful of cases.
    for (let news = 0; news <= ALLOCATION_TOTAL; news += 1) {
      for (let youtube = 0; youtube <= ALLOCATION_TOTAL - news; youtube += 1) {
        const x = ALLOCATION_TOTAL - news - youtube;
        const segments = splitToAllocationSegments({ news, youtube, x }, []);
        expect(sumSegmentCounts(segments)).toBe(ALLOCATION_TOTAL);
      }
    }
  });
});
