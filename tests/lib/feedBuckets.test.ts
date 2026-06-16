import { describe, expect, it } from "vitest";
import {
  ALLOCATION_TOTAL,
  ALWAYS_INCLUDED_CATEGORY_BUCKET,
  buildDefaultSegments,
  buildSegmentsForSelectedCategories,
  categoryBucketsFromFollows,
  DESIGN_BUCKET_IDS,
  DESIGN_BUCKET_TO_ENUM,
  DESIGN_BUCKETS,
  type DesignBucketId,
  ENUM_TO_DESIGN_BUCKET,
  type FeedCategoryEnum,
  sumSegmentCounts,
} from "@/lib/feedBuckets";

/**
 * Blip Flow Stage 3 — the design-bucket ↔ feed_category enum bijection + allocation helpers.
 *
 * WHY these tests exist (Rule 9 — encode the contract, not the shapes):
 *  - The screen draws 9 design ids; the DB enum uses 8(+1) snake_case keys. If the
 *    forward map and the inverse ever drift, a saved allocation would hydrate the WRONG
 *    bucket (e.g. "world" rows showing up as "markets"), silently corrupting the user's
 *    feed order. We assert the map is a TOTAL bijection that round-trips every id.
 *  - `world`/`tech` MUST rename to `world_politics`/`tech_science` (the only renames) — a
 *    regression that wrote `world` to the enum would 22P02-fail at the DB. We pin those.
 *  - The default seed MUST total exactly 30 (it is the immediately-savable starting state).
 *    A non-30 default would land the user on a screen they cannot save.
 */

describe("DESIGN_BUCKET_TO_ENUM ↔ ENUM_TO_DESIGN_BUCKET (the bijection that must never drift)", () => {
  it("maps every one of the 9 design buckets to a distinct enum value", () => {
    // WHY: a missing/duplicated enum value would make two design buckets collide on one
    // DB row (last-write-wins), losing one bucket's allocation silently.
    const enumValues = DESIGN_BUCKET_IDS.map((bucketId) => DESIGN_BUCKET_TO_ENUM[bucketId]);
    expect(enumValues).toHaveLength(9);
    expect(new Set(enumValues).size).toBe(9);
  });

  it("round-trips every design id through the inverse map (forward then back is identity)", () => {
    // WHY: hydrating the screen from saved rows uses the inverse map; if it isn't the exact
    // inverse, a returning user's saved order rebuilds as the wrong buckets.
    for (const bucketId of DESIGN_BUCKET_IDS) {
      const enumValue = DESIGN_BUCKET_TO_ENUM[bucketId];
      expect(ENUM_TO_DESIGN_BUCKET[enumValue]).toBe(bucketId);
    }
  });

  it("renames world→world_politics and tech→tech_science (the only two renames)", () => {
    // WHY: these are the design ids that differ from their enum keys. Writing the design id
    // verbatim ("world") would be an unknown enum literal — a hard DB failure.
    expect(DESIGN_BUCKET_TO_ENUM.world).toBe("world_politics");
    expect(DESIGN_BUCKET_TO_ENUM.tech).toBe("tech_science");
  });

  it("keeps the 7 identity-mapped buckets verbatim (breaking/markets/sport/culture/youtube/x/podcasts)", () => {
    const identityBuckets: DesignBucketId[] = ["breaking", "markets", "sport", "culture", "youtube", "x", "podcasts"];
    for (const bucketId of identityBuckets) {
      expect(DESIGN_BUCKET_TO_ENUM[bucketId]).toBe(bucketId as unknown as FeedCategoryEnum);
    }
  });
});

describe("sumSegmentCounts (the budget invariant helper)", () => {
  it("sums the slot counts across an ordered segment list (happy path)", () => {
    expect(sumSegmentCounts([{ count: 2 }, { count: 4 }, { count: 24 }])).toBe(30);
  });

  it("returns 0 for an empty list (edge case — a fully-cleared allocation)", () => {
    expect(sumSegmentCounts([])).toBe(0);
  });
});

describe("buildDefaultSegments (the seed for a user with no saved allocation)", () => {
  it("totals exactly ALLOCATION_TOTAL (30) so the default screen is immediately savable", () => {
    // WHY: the default must satisfy the exactly-30 budget gate — otherwise a fresh user
    // lands on a disabled "Fill N more" CTA with no obvious cause.
    expect(sumSegmentCounts(buildDefaultSegments())).toBe(ALLOCATION_TOTAL);
  });

  it("returns a fresh, mutable copy each call (no shared default-state leak between mounts)", () => {
    // WHY: the screen mutates segments in place via setState copies; a shared frozen/aliased
    // default would either throw on mutation or leak one user's edits into the next mount.
    const first = buildDefaultSegments();
    const second = buildDefaultSegments();
    expect(first).not.toBe(second);
    first[0].count = 99;
    expect(second[0].count).not.toBe(99);
  });

  it("only references valid design bucket ids", () => {
    for (const segment of buildDefaultSegments()) {
      expect(DESIGN_BUCKET_IDS).toContain(segment.bucketId);
    }
  });
});

describe("categoryBucketsFromFollows (picker selections → the category blocks to seed)", () => {
  it("maps distinct picker roots to their category buckets (the happy path)", () => {
    // WHY: this is the whole point of the filter — a user who picked Tech + Markets must
    // resolve to exactly those two category buckets, so the screen seeds only those blocks.
    const buckets = categoryBucketsFromFollows([
      { followId: "tech/ai/llms/openai" },
      { followId: "business/equities" },
    ]);
    expect(buckets).toEqual(["tech", "markets"]);
  });

  it("folds geopolitics/politics/environment into ONE 'world' bucket (deduped)", () => {
    // WHY: three distinct picker roots map to the single World & Politics block; if they
    // didn't collapse, the screen would seed (or try to seed) a duplicate world block.
    const buckets = categoryBucketsFromFollows([
      { followId: "geopolitics/elections" },
      { followId: "politics/us-congress" },
      { followId: "environment/climate" },
    ]);
    expect(buckets).toEqual(["world"]);
  });

  it("drops an unmapped root instead of mis-bucketing it (Rule 12 — the failure case)", () => {
    // WHY: a wrong category is exactly the bug this filter removes — an unknown picker root
    // must NOT silently resurrect a category the user didn't pick (e.g. fall through to culture).
    const buckets = categoryBucketsFromFollows([{ followId: "tech/ai" }, { followId: "totally-unknown-root/x" }]);
    expect(buckets).toEqual(["tech"]);
  });

  it("returns an empty array for no selections (edge — signals 'fall back to full default')", () => {
    expect(categoryBucketsFromFollows([])).toEqual([]);
  });
});

describe("buildSegmentsForSelectedCategories (the filtered seed for a partial-category pick)", () => {
  it("keeps breaking + source blocks + only the selected categories (Tech + Markets)", () => {
    // WHY: encodes the owner-locked rule — breaking is always-on, sources are never filtered,
    // and every UNpicked category (world/sport/culture) is dropped from the seed.
    const seededBucketIds = buildSegmentsForSelectedCategories(["tech", "markets"]).map((segment) => segment.bucketId);
    expect(seededBucketIds).toEqual(["breaking", "tech", "youtube", "markets", "x"]);
  });

  it("force-includes 'breaking' even when it is not in the selected set", () => {
    // WHY: breaking is not a pickable interest; omitting it would erase Breaking News from the
    // 30 for every user. It must appear regardless of the passed category set.
    const seededBucketIds = buildSegmentsForSelectedCategories([]).map((segment) => segment.bucketId);
    expect(seededBucketIds).toContain(ALWAYS_INCLUDED_CATEGORY_BUCKET);
  });

  it("never filters source blocks — every 'src' bucket in the default survives", () => {
    // WHY: sources come from the source swipe, not the topic picker (owner decision); filtering
    // them here would drop YouTube/X the user is owed even with zero category picks.
    const seededBucketIds = new Set(buildSegmentsForSelectedCategories([]).map((segment) => segment.bucketId));
    const defaultSourceBucketIds = buildDefaultSegments()
      .map((segment) => segment.bucketId)
      .filter((bucketId) => DESIGN_BUCKETS[bucketId].kind === "src");
    for (const sourceBucketId of defaultSourceBucketIds) {
      expect(seededBucketIds).toContain(sourceBucketId);
    }
  });

  it("seeds UNDER 30 for a narrow pick so the screen opens on 'Fill N more' (no auto-rescale)", () => {
    // WHY: the owner chose to open under-budget rather than rescale; a Tech+Markets pick must
    // total < 30 (here 20) so the budget CTA prompts the user to fill the rest, not auto-fill.
    const total = sumSegmentCounts(buildSegmentsForSelectedCategories(["tech", "markets"]));
    expect(total).toBeLessThan(ALLOCATION_TOTAL);
    expect(total).toBe(20);
  });

  it("preserves the default seed order for the surviving blocks", () => {
    // WHY: order IS the briefing sequence ("the first block plays first") — the filter must not
    // reorder the kept blocks relative to the default.
    const seededBucketIds = buildSegmentsForSelectedCategories(["world", "sport", "culture"]).map(
      (segment) => segment.bucketId,
    );
    expect(seededBucketIds).toEqual(["breaking", "world", "youtube", "sport", "x", "culture"]);
  });
});
