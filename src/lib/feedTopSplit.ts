/**
 * Closing-arc feed split → ordered "Build your 30" allocation segments (FSR data core, #30).
 *
 * The conversational interview's CLOSING ARC captures a coarse three-axis split of the 30 feed
 * slots — `{ news, youtube, x }` summing to exactly {@link ALLOCATION_TOTAL} — instead of asking
 * the user to hand-tune every category block. {@link splitToAllocationSegments} turns that split
 * into the ordered coarse {@link AllocationSegment}[] the persistence layer
 * ({@link import("@/lib/feedAllocation").saveUserFeedAllocation}) already knows how to write.
 *
 * The `news` axis is spread across the user's SELECTED category buckets (or all 8 topic roots
 * when they selected none but still asked for news) proportional to the canonical
 * {@link DEFAULT_ALLOCATION_SEGMENTS} weights, using the LARGEST-REMAINDER (Hamilton) method so
 * the category counts sum to `news` EXACTLY — never ±1 from rounding. The `youtube`/`x` axes each
 * append one coarse source row when > 0. The result is guaranteed to sum to
 * {@link ALLOCATION_TOTAL} (the caller validates the split does too, before persisting).
 *
 * Pure + static-export safe: no `window`/server APIs, no I/O — just data in, segments out.
 */

import {
  ALLOCATION_TOTAL,
  type AllocationSegment,
  DEFAULT_ALLOCATION_SEGMENTS,
  DESIGN_BUCKETS,
  type DesignBucketId,
} from "@/lib/feedBuckets";

/** The three-axis closing-arc split — coarse slot counts that MUST sum to {@link ALLOCATION_TOTAL}. */
export interface TopSplit {
  /** Slots for topic NEWS, spread across the selected category buckets (largest-remainder). */
  news: number;
  /** Slots for the YouTube source axis (one coarse `youtube` row when > 0). */
  youtube: number;
  /** Slots for the X source axis (one coarse `x` row when > 0). */
  x: number;
}

/**
 * The canonical topic-category buckets (the 8 `kind === "cat"` roots) with their default weights,
 * in {@link DEFAULT_ALLOCATION_SEGMENTS} order. This is the weight basis + the tie-break ORDER for
 * the largest-remainder spread, and the fallback set when the user selected no categories. Derived
 * from the single declared seed so it can never drift (Rule 7).
 */
const CATEGORY_WEIGHTS: ReadonlyArray<readonly [DesignBucketId, number]> = DEFAULT_ALLOCATION_SEGMENTS.filter(
  ([bucketId]) => DESIGN_BUCKETS[bucketId].kind === "cat",
);

/** Canonical order index of each category bucket — the deterministic tie-break for equal remainders. */
const CATEGORY_ORDER: ReadonlyMap<DesignBucketId, number> = new Map(
  CATEGORY_WEIGHTS.map(([bucketId], index) => [bucketId, index]),
);

/** The weight of a category bucket (its default seed count) — the largest-remainder proportion basis. */
const WEIGHT_BY_BUCKET: ReadonlyMap<DesignBucketId, number> = new Map(CATEGORY_WEIGHTS);

/**
 * Spread `newsSlots` across `categoryBuckets` proportional to their default weights using the
 * LARGEST-REMAINDER (Hamilton) method, returning `[bucketId, count]` in canonical order with
 * zero-count buckets dropped. The counts sum to `newsSlots` EXACTLY.
 *
 * Method: give each bucket the floor of its exact quota (`newsSlots · weight / totalWeight`), then
 * hand the leftover slots (`newsSlots − Σ floors`) one apiece to the buckets with the largest
 * fractional remainders. Ties are broken deterministically by higher weight, then canonical order,
 * so the same split always yields the same segments.
 */
function spreadNewsAcrossCategories(
  newsSlots: number,
  categoryBuckets: ReadonlyArray<DesignBucketId>,
): Array<[DesignBucketId, number]> {
  const totalWeight = categoryBuckets.reduce((sum, bucketId) => sum + (WEIGHT_BY_BUCKET.get(bucketId) ?? 0), 0);
  if (newsSlots <= 0 || categoryBuckets.length === 0 || totalWeight <= 0) {
    return [];
  }

  // Floor of each exact quota + its fractional remainder; sum of floors is the base allocation.
  const allocations = categoryBuckets.map((bucketId) => {
    const weight = WEIGHT_BY_BUCKET.get(bucketId) ?? 0;
    const exactQuota = (newsSlots * weight) / totalWeight;
    const base = Math.floor(exactQuota);
    return { bucketId, weight, base, remainder: exactQuota - base };
  });

  // Hand out the leftover slots (guaranteed 0..categoryBuckets.length) to the largest remainders.
  let leftover = newsSlots - allocations.reduce((sum, a) => sum + a.base, 0);
  const byRemainderDesc = [...allocations].sort((a, b) => {
    if (b.remainder !== a.remainder) {
      return b.remainder - a.remainder;
    }
    if (b.weight !== a.weight) {
      return b.weight - a.weight;
    }
    return (CATEGORY_ORDER.get(a.bucketId) ?? 0) - (CATEGORY_ORDER.get(b.bucketId) ?? 0);
  });
  for (const allocation of byRemainderDesc) {
    if (leftover <= 0) {
      break;
    }
    allocation.base += 1;
    leftover -= 1;
  }

  // Emit in canonical order, dropping zero-count buckets (a bucket that won no slots gets no row).
  return allocations
    .filter((allocation) => allocation.base > 0)
    .sort((a, b) => (CATEGORY_ORDER.get(a.bucketId) ?? 0) - (CATEGORY_ORDER.get(b.bucketId) ?? 0))
    .map((allocation) => [allocation.bucketId, allocation.base] as [DesignBucketId, number]);
}

/**
 * Turn a closing-arc {@link TopSplit} into the ordered coarse {@link AllocationSegment}[] the
 * feed-allocation writer persists. The `news` axis is spread across `selectedCategoryBuckets`
 * (or all 8 topic roots when that list is empty and `news > 0`) via largest-remainder; the
 * `youtube`/`x` axes each append one coarse row when > 0. A zero-count axis writes NO row.
 *
 * Guarantees (Rule 9 — the callers depend on these):
 *  - {@link import("@/lib/feedBuckets").sumSegmentCounts}(result) === `split.news + split.youtube + split.x`;
 *    when the caller's split sums to {@link ALLOCATION_TOTAL}, so does the result.
 *  - `30/0/0` → category rows only (no source rows).
 *  - `0/0/30` → a single `{ bucketId: "x", count: 30 }` row.
 *  - empty `selectedCategoryBuckets` + `news > 0` → the news spread lands across all 8 roots.
 *
 * @param split - The three-axis `{ news, youtube, x }` split (should sum to 30; the caller validates).
 * @param selectedCategoryBuckets - The category buckets the user backs; empty → all 8 topic roots.
 * @returns Ordered coarse allocation segments (categories first, then `youtube`, then `x`).
 *
 * @example
 * // Default 20/7/3 with no explicit category selection → 8 roots (largest-remainder) + yt + x:
 * splitToAllocationSegments({ news: 20, youtube: 7, x: 3 }, []);
 * // → [ai:3, tech:3, geopolitics:3, business:3, politics:2, environment:2, sport:2, arts:2, youtube:7, x:3]
 */
export function splitToAllocationSegments(
  split: TopSplit,
  selectedCategoryBuckets: ReadonlyArray<DesignBucketId>,
): AllocationSegment[] {
  // De-dup selected categories (preserve first-seen), keep only real topic-category buckets, and
  // fall back to all 8 roots when none were selected but news slots were requested.
  const distinctSelected: DesignBucketId[] = [];
  const seen = new Set<DesignBucketId>();
  for (const bucketId of selectedCategoryBuckets) {
    if (!seen.has(bucketId) && WEIGHT_BY_BUCKET.has(bucketId)) {
      seen.add(bucketId);
      distinctSelected.push(bucketId);
    }
  }
  const categoryBuckets =
    distinctSelected.length > 0 ? distinctSelected : CATEGORY_WEIGHTS.map(([bucketId]) => bucketId);

  const segments: AllocationSegment[] = spreadNewsAcrossCategories(split.news, categoryBuckets).map(
    ([bucketId, count]) => ({ bucketId, count }),
  );

  // Append the source axes in canonical order (youtube before x); a 0-count axis writes no row.
  if (split.youtube > 0) {
    segments.push({ bucketId: "youtube", count: split.youtube });
  }
  if (split.x > 0) {
    segments.push({ bucketId: "x", count: split.x });
  }

  return segments;
}

/** The exact-30 invariant the closing-arc split must satisfy — re-exported for the orchestrator's guard. */
export const TOP_SPLIT_TOTAL = ALLOCATION_TOTAL;
