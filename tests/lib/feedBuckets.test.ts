import { describe, expect, it } from "vitest";
import {
  ALLOCATION_TOTAL,
  allowedBucketsForSelections,
  buildDefaultSegments,
  buildSegmentsForSelections,
  categoryBucketsFromFollows,
  categoryBucketsFromInterestVector,
  DESIGN_BUCKET_IDS,
  DESIGN_BUCKET_TO_ENUM,
  DESIGN_BUCKETS,
  type DesignBucketId,
  ENUM_TO_DESIGN_BUCKET,
  type FeedCategoryEnum,
  sourceBucketsFromFollows,
  sumSegmentCounts,
} from "@/lib/feedBuckets";

/**
 * Blip Flow Stage 3 — the design-bucket ↔ feed_category enum bijection + allocation helpers.
 *
 * WHY these tests exist (Rule 9 — encode the contract, not the shapes):
 *  - The screen draws 8 design ids (phase-SP1 removed the `breaking` bucket); the DB enum
 *    uses snake_case keys. If the forward map and the inverse ever drift, a saved allocation
 *    would hydrate the WRONG bucket (e.g. "world" rows showing up as "markets"), silently
 *    corrupting the user's feed order. We assert the map is a TOTAL bijection that
 *    round-trips every id.
 *  - `world`/`tech` MUST rename to `world_politics`/`tech_science` (the only renames) — a
 *    regression that wrote `world` to the enum would 22P02-fail at the DB. We pin those.
 *  - The default seed MUST total exactly 30 (it is the immediately-savable starting state).
 *    A non-30 default would land the user on a screen they cannot save.
 *  - phase-SP1 removed the always-on `breaking` block: the seed + Add sheet are now gated on
 *    real category/source backing ONLY — no bucket is force-included.
 */

describe("DESIGN_BUCKET_TO_ENUM ↔ ENUM_TO_DESIGN_BUCKET (the bijection that must never drift)", () => {
  it("maps every one of the 8 design buckets to a distinct enum value", () => {
    // WHY: a missing/duplicated enum value would make two design buckets collide on one
    // DB row (last-write-wins), losing one bucket's allocation silently.
    const enumValues = DESIGN_BUCKET_IDS.map((bucketId) => DESIGN_BUCKET_TO_ENUM[bucketId]);
    expect(enumValues).toHaveLength(8);
    expect(new Set(enumValues).size).toBe(8);
  });

  it("has no 'breaking' design bucket (phase-SP1 removed it)", () => {
    // WHY: the breaking feed category was removed; a resurrected bucket would re-introduce
    // the dead "Breaking News" block and write the unused enum value.
    expect(DESIGN_BUCKET_IDS).not.toContain("breaking" as DesignBucketId);
    expect(DESIGN_BUCKET_IDS).toHaveLength(8);
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

  it("keeps the 6 identity-mapped buckets verbatim (markets/sport/culture/youtube/x/podcasts)", () => {
    const identityBuckets: DesignBucketId[] = ["markets", "sport", "culture", "youtube", "x", "podcasts"];
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

describe("sourceBucketsFromFollows (followed sources → the source blocks the 30 may seed)", () => {
  it("maps each content_source_type to its source bucket (youtube/podcast/x, deduped)", () => {
    // WHY: this is the gate that backs the owner rule (2026-06-17) — only a source axis the user
    // actually follows may seed a block. A YouTube + X follow must resolve to exactly those axes.
    const buckets = sourceBucketsFromFollows([
      { content_source_type: "youtube_channel" },
      { content_source_type: "x_account" },
      { content_source_type: "youtube_channel" },
    ]);
    expect(buckets).toEqual(["youtube", "x"]);
  });

  it("rides a 'personality' follow on the X axis (no axis of its own)", () => {
    // WHY: the catalog has 4 source types but the screen draws 3 axes; a named creator shares the
    // X axis (same circular avatar). Mis-dropping it would erase a source the user is owed.
    expect(sourceBucketsFromFollows([{ content_source_type: "personality" }])).toEqual(["x"]);
  });

  it("returns an empty array for no followed sources (edge — no source blocks seeded)", () => {
    expect(sourceBucketsFromFollows([])).toEqual([]);
  });
});

describe("categoryBucketsFromInterestVector (persisted backing → the category blocks to seed)", () => {
  it("maps each positively-weighted pinned key to its category bucket (ai→tech, sport→sport)", () => {
    // WHY: the library 'Thirty' tab has no live picker — it must derive backed categories from the
    // rolled-up vector. ai folds into Tech & Science; sport stays sport.
    expect(categoryBucketsFromInterestVector({ ai: 4.0, sport: 1.2 })).toEqual(["tech", "sport"]);
  });

  it("folds geopolitics + politics + environment into ONE 'world' bucket (deduped)", () => {
    // WHY: three pinned keys map to the single Geopolitics block; if they didn't collapse the seed
    // would try to seed a duplicate world block.
    expect(categoryBucketsFromInterestVector({ geopolitics: 2, politics: 1, environment: 0.5 })).toEqual(["world"]);
  });

  it("excludes a zero / non-positive weight key (no backing → no block)", () => {
    // WHY: a key present at weight 0 is NOT real backing; seeding it would resurrect a phantom block
    // — exactly the bug this fix removes.
    expect(categoryBucketsFromInterestVector({ ai: 3, business: 0 })).toEqual(["tech"]);
  });

  it("returns an empty array for an empty/zero vector (edge — new user, no backing)", () => {
    expect(categoryBucketsFromInterestVector({})).toEqual([]);
  });
});

describe("allowedBucketsForSelections (the single guard for which blocks may appear)", () => {
  it("unions exactly the backed categories + followed sources (no forced bucket)", () => {
    // WHY: this set gates BOTH the seed and the Add sheet — it must be exactly the backed
    // categories + followed sources, so neither a seed nor a hand-add can introduce a phantom.
    // phase-SP1 removed the always-on `breaking` force-include.
    const allowed = allowedBucketsForSelections(["tech"], ["youtube"]);
    expect([...allowed].sort()).toEqual(["tech", "youtube"].sort());
  });

  it("is EMPTY when both inputs are empty (no bucket is forced in anymore)", () => {
    // WHY: phase-SP1 dropped the always-on breaking tier — with zero backing, nothing is allowed.
    expect(allowedBucketsForSelections([], []).size).toBe(0);
  });
});

describe("buildSegmentsForSelections (the filtered seed gated on real category + source backing)", () => {
  it("keeps only the selected categories + only the followed source axes (Tech+Markets, YouTube+X)", () => {
    // WHY: encodes the owner rule (2026-06-17) — every UNbacked category (world/sport/culture)
    // AND unfollowed source axis is dropped from the seed. phase-SP1 removed the always-on
    // breaking block, so it never appears.
    const seededBucketIds = buildSegmentsForSelections(["tech", "markets"], ["youtube", "x"]).map(
      (segment) => segment.bucketId,
    );
    expect(seededBucketIds).toEqual(["tech", "youtube", "markets", "x"]);
    expect(seededBucketIds).not.toContain("breaking" as DesignBucketId);
  });

  it("DROPS a source axis the user follows nothing on (the phantom-source-block fix)", () => {
    // WHY: this is the core regression fix — a user who follows a YouTube channel but no X account
    // must NOT get an X block seeded (the old behaviour seeded every source axis unconditionally).
    const seededBucketIds = buildSegmentsForSelections(["tech", "markets"], ["youtube"]).map(
      (segment) => segment.bucketId,
    );
    expect(seededBucketIds).toEqual(["tech", "youtube", "markets"]);
    expect(seededBucketIds).not.toContain("x");
  });

  it("seeds NO source blocks when the user follows no sources (zero source backing)", () => {
    // WHY: with no followed sources, the 30 must contain only backed categories — never
    // a source block with no backing follow.
    const seededBucketIds = new Set(buildSegmentsForSelections(["tech"], []).map((segment) => segment.bucketId));
    for (const bucketId of seededBucketIds) {
      expect(DESIGN_BUCKETS[bucketId].kind).toBe("cat");
    }
  });

  it("seeds NOTHING when both backing sets are empty (no forced bucket post phase-SP1)", () => {
    // WHY: breaking was the only force-included block; with it removed, an empty backing set
    // yields an empty seed (the screen then falls back to the full default upstream).
    const seededBucketIds = buildSegmentsForSelections([], []).map((segment) => segment.bucketId);
    expect(seededBucketIds).toEqual([]);
  });

  it("seeds UNDER 30 for a narrow pick so the screen opens on 'Fill N more' (no auto-rescale)", () => {
    // WHY: the owner chose to open under-budget rather than rescale; Tech+Markets+YouTube+X totals
    // 18 (< 30) so the budget CTA prompts the user to fill the rest, not auto-fill.
    const total = sumSegmentCounts(buildSegmentsForSelections(["tech", "markets"], ["youtube", "x"]));
    expect(total).toBeLessThan(ALLOCATION_TOTAL);
    expect(total).toBe(18);
  });

  it("preserves the default seed order for the surviving blocks", () => {
    // WHY: order IS the briefing sequence ("the first block plays first") — the filter must not
    // reorder the kept blocks relative to the default.
    const seededBucketIds = buildSegmentsForSelections(["world", "sport", "culture"], ["youtube", "x"]).map(
      (segment) => segment.bucketId,
    );
    expect(seededBucketIds).toEqual(["world", "youtube", "sport", "x", "culture"]);
  });
});
