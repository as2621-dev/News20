import { describe, expect, it } from "vitest";
import {
  ALLOCATION_TOTAL,
  allowedBucketsForSelections,
  buildDefaultSegments,
  buildSegmentsForSelections,
  categoryBucketsFromFollows,
  categoryBucketsFromInterestVector,
  DEFAULT_ALLOCATION_SEGMENTS,
  DESIGN_BUCKET_IDS,
  DESIGN_BUCKET_TO_ENUM,
  DESIGN_BUCKETS,
  type DesignBucketId,
  ENUM_TO_DESIGN_BUCKET,
  type FeedCategoryEnum,
  PICKER_ROOT_TO_CATEGORY_BUCKET,
  sourceBucketsFromFollows,
  sumSegmentCounts,
} from "@/lib/feedBuckets";

/**
 * Blip Flow Stage 3 — the design-bucket ↔ feed_category enum bijection + allocation helpers,
 * AFTER the SP3 taxonomy unfold (the 8 onboarding picker roots + youtube/x; no fold).
 *
 * WHY these tests exist (Rule 9 — encode the contract, not the shapes):
 *  - The screen draws 10 design ids = the 8 picker roots + 2 source axes. There is NO fold,
 *    so the design-bucket→enum map and `PICKER_ROOT_TO_CATEGORY_BUCKET` are both IDENTITY.
 *    A drift here would hydrate a saved allocation into the WRONG bucket, silently corrupting
 *    the user's feed order — we assert the map is a TOTAL bijection that round-trips every id.
 *  - The default seed MUST total exactly 30 (it is the immediately-savable starting state),
 *    and its keys+counts MUST EQUAL the Python twin `DEFAULT_FEED_ALLOCATION`
 *    (`agents/pipeline/categories.py`). A divergence between the two twins would seed a
 *    "Build your 30" the backend allocator cannot fill the same way (Rule 7). The Python
 *    side is replicated here as a literal so a change to either twin trips this test.
 *  - The old folded buckets (`world_politics`/`tech_science`/`markets`/`culture`/`podcasts`)
 *    are retired — a resurrected fold id would re-introduce a dead block.
 */

/**
 * The Python `DEFAULT_FEED_ALLOCATION` (`agents/pipeline/categories.py`), replicated as a
 * literal. This is the AUTHORITATIVE twin the TS `DEFAULT_ALLOCATION_SEGMENTS` must equal —
 * same keys + counts, summing to 30. Edited ONLY when the owner-locked split changes (in
 * which case BOTH twins move together, Rule 7).
 */
const PYTHON_DEFAULT_FEED_ALLOCATION: Readonly<Record<FeedCategoryEnum, number>> = {
  ai: 4,
  tech: 4,
  geopolitics: 4,
  business: 4,
  politics: 2,
  environment: 2,
  sport: 3,
  arts: 3,
  youtube: 2,
  x: 2,
};

describe("DESIGN_BUCKET_TO_ENUM ↔ ENUM_TO_DESIGN_BUCKET (the identity bijection that must never drift)", () => {
  it("maps every one of the 10 design buckets to a distinct enum value", () => {
    // WHY: a missing/duplicated enum value would make two design buckets collide on one
    // DB row (last-write-wins), losing one bucket's allocation silently.
    const enumValues = DESIGN_BUCKET_IDS.map((bucketId) => DESIGN_BUCKET_TO_ENUM[bucketId]);
    expect(enumValues).toHaveLength(10);
    expect(new Set(enumValues).size).toBe(10);
  });

  it("has no retired folded bucket (SP3 unfold removed world_politics/tech_science/markets/culture/podcasts)", () => {
    // WHY: the folded design taxonomy was retired; a resurrected fold id would re-introduce a
    // dead block and write an unused enum value.
    for (const retired of ["world", "world_politics", "tech_science", "markets", "culture", "podcasts", "breaking"]) {
      expect(DESIGN_BUCKET_IDS).not.toContain(retired as DesignBucketId);
    }
    expect(DESIGN_BUCKET_IDS).toHaveLength(10);
  });

  it("round-trips every design id through the inverse map (forward then back is identity)", () => {
    // WHY: hydrating the screen from saved rows uses the inverse map; if it isn't the exact
    // inverse, a returning user's saved order rebuilds as the wrong buckets.
    for (const bucketId of DESIGN_BUCKET_IDS) {
      const enumValue = DESIGN_BUCKET_TO_ENUM[bucketId];
      expect(ENUM_TO_DESIGN_BUCKET[enumValue]).toBe(bucketId);
    }
  });

  it("maps every design id to ITSELF (the SP3 unfold made the map identity — no rename)", () => {
    // WHY: post-unfold each picker root is its own enum value; a rename (e.g. writing
    // "world_politics") would be an unknown enum literal and a hard DB failure.
    for (const bucketId of DESIGN_BUCKET_IDS) {
      expect(DESIGN_BUCKET_TO_ENUM[bucketId]).toBe(bucketId as unknown as FeedCategoryEnum);
    }
  });
});

describe("PICKER_ROOT_TO_CATEGORY_BUCKET (the picker-root → screen-bucket map, now identity)", () => {
  it("maps each of the 8 picker roots to itself (no fold post-SP3)", () => {
    // WHY: the whole point of SP3 — the picker roots ARE the screen buckets, so a story picked
    // under "ai" must seed the "ai" block, never fold into "tech". A non-identity entry here
    // would re-introduce the retired fold.
    const eightRoots: DesignBucketId[] = ["ai", "geopolitics", "business", "environment", "politics", "tech", "sport", "arts"];
    for (const root of eightRoots) {
      expect(PICKER_ROOT_TO_CATEGORY_BUCKET[root]).toBe(root);
    }
    expect(Object.keys(PICKER_ROOT_TO_CATEGORY_BUCKET).sort()).toEqual([...eightRoots].sort());
  });
});

describe("DEFAULT_ALLOCATION_SEGMENTS equals the Python DEFAULT_FEED_ALLOCATION twin (Rule 7)", () => {
  it("has the SAME keys + counts as the Python allocation (the load-bearing twin equality)", () => {
    // WHY: the TS seed and the Python allocator must agree on the per-category split, or the
    // "Build your 30" the user saves cannot be filled the same way the backend ranks it.
    const tsAllocation = Object.fromEntries(DEFAULT_ALLOCATION_SEGMENTS) as Record<FeedCategoryEnum, number>;
    expect(tsAllocation).toEqual(PYTHON_DEFAULT_FEED_ALLOCATION);
  });

  it("totals exactly 30 across the 10 categories", () => {
    // WHY: the default must satisfy the exactly-30 budget gate — both twins sum to 30 by lock.
    const tsTotal = DEFAULT_ALLOCATION_SEGMENTS.reduce((sum, [, count]) => sum + count, 0);
    const pyTotal = Object.values(PYTHON_DEFAULT_FEED_ALLOCATION).reduce((sum, count) => sum + count, 0);
    expect(tsTotal).toBe(ALLOCATION_TOTAL);
    expect(pyTotal).toBe(ALLOCATION_TOTAL);
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
  it("maps distinct picker roots to their identity category buckets (the happy path)", () => {
    // WHY: this is the whole point of the filter — a user who picked AI + Business must
    // resolve to exactly those two category buckets, so the screen seeds only those blocks.
    const buckets = categoryBucketsFromFollows([
      { followId: "ai/foundation-models-llms/openai" },
      { followId: "business/markets-investing" },
    ]);
    expect(buckets).toEqual(["ai", "business"]);
  });

  it("keeps geopolitics / politics / environment as THREE distinct buckets (no fold post-SP3)", () => {
    // WHY: SP3 split the old single 'world' block into three first-class roots; they must NOT
    // collapse, or a user who picked all three would lose two blocks.
    const buckets = categoryBucketsFromFollows([
      { followId: "geopolitics/armed-conflict" },
      { followId: "politics/elections" },
      { followId: "environment/climate" },
    ]);
    expect(buckets).toEqual(["geopolitics", "politics", "environment"]);
  });

  it("drops an unmapped root instead of mis-bucketing it (Rule 12 — the failure case)", () => {
    // WHY: a wrong category is exactly the bug this filter removes — an unknown picker root
    // must NOT silently resurrect a category the user didn't pick.
    const buckets = categoryBucketsFromFollows([{ followId: "ai/llms" }, { followId: "totally-unknown-root/x" }]);
    expect(buckets).toEqual(["ai"]);
  });

  it("returns an empty array for no selections (edge — signals 'fall back to full default')", () => {
    expect(categoryBucketsFromFollows([])).toEqual([]);
  });
});

describe("sourceBucketsFromFollows (followed sources → the source blocks the 30 may seed)", () => {
  it("maps each content_source_type to its source bucket (youtube/x, deduped)", () => {
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
    // WHY: the catalog has 4 source types but the screen draws 2 axes; a named creator shares the
    // X axis (same circular avatar). Mis-dropping it would erase a source the user is owed.
    expect(sourceBucketsFromFollows([{ content_source_type: "personality" }])).toEqual(["x"]);
  });

  it("rides a 'podcast' follow on the YouTube axis (no dedicated SP3 podcast axis)", () => {
    // WHY: SP3 has no podcasts bucket; a followed podcast must still seed a source block, riding
    // the youtube (long-form creator) axis rather than being silently dropped.
    expect(sourceBucketsFromFollows([{ content_source_type: "podcast" }])).toEqual(["youtube"]);
  });

  it("returns an empty array for no followed sources (edge — no source blocks seeded)", () => {
    expect(sourceBucketsFromFollows([])).toEqual([]);
  });
});

describe("categoryBucketsFromInterestVector (persisted backing → the category blocks to seed)", () => {
  it("maps each positively-weighted pinned key to its identity category bucket (ai→ai, sport→sport)", () => {
    // WHY: the library 'Thirty' tab has no live picker — it must derive backed categories from the
    // rolled-up vector. Post-SP3 each pinned key is its own bucket (no fold).
    expect(categoryBucketsFromInterestVector({ ai: 4.0, sport: 1.2 })).toEqual(["ai", "sport"]);
  });

  it("keeps geopolitics + politics + environment as THREE distinct buckets (no fold)", () => {
    // WHY: three pinned keys are three first-class blocks post-SP3; collapsing them would lose
    // two of the user's backed categories.
    expect(categoryBucketsFromInterestVector({ geopolitics: 2, politics: 1, environment: 0.5 })).toEqual([
      "geopolitics",
      "politics",
      "environment",
    ]);
  });

  it("excludes a zero / non-positive weight key (no backing → no block)", () => {
    // WHY: a key present at weight 0 is NOT real backing; seeding it would resurrect a phantom block
    // — exactly the bug this fix removes.
    expect(categoryBucketsFromInterestVector({ ai: 3, business: 0 })).toEqual(["ai"]);
  });

  it("returns an empty array for an empty/zero vector (edge — new user, no backing)", () => {
    expect(categoryBucketsFromInterestVector({})).toEqual([]);
  });
});

describe("allowedBucketsForSelections (the single guard for which blocks may appear)", () => {
  it("unions exactly the backed categories + followed sources (no forced bucket)", () => {
    // WHY: this set gates BOTH the seed and the Add sheet — it must be exactly the backed
    // categories + followed sources, so neither a seed nor a hand-add can introduce a phantom.
    const allowed = allowedBucketsForSelections(["ai"], ["youtube"]);
    expect([...allowed].sort()).toEqual(["ai", "youtube"].sort());
  });

  it("is EMPTY when both inputs are empty (no bucket is forced in)", () => {
    // WHY: with zero backing, nothing is allowed — no always-on block.
    expect(allowedBucketsForSelections([], []).size).toBe(0);
  });
});

describe("buildSegmentsForSelections (the filtered seed gated on real category + source backing)", () => {
  it("keeps only the selected categories + only the followed source axes (AI+Business, YouTube+X)", () => {
    // WHY: encodes the owner rule (2026-06-17) — every UNbacked category AND unfollowed source
    // axis is dropped from the seed.
    const seededBucketIds = buildSegmentsForSelections(["ai", "business"], ["youtube", "x"]).map(
      (segment) => segment.bucketId,
    );
    expect(seededBucketIds).toEqual(["ai", "business", "youtube", "x"]);
  });

  it("DROPS a source axis the user follows nothing on (the phantom-source-block fix)", () => {
    // WHY: this is the core regression fix — a user who follows a YouTube channel but no X account
    // must NOT get an X block seeded (the old behaviour seeded every source axis unconditionally).
    const seededBucketIds = buildSegmentsForSelections(["ai", "business"], ["youtube"]).map(
      (segment) => segment.bucketId,
    );
    expect(seededBucketIds).toEqual(["ai", "business", "youtube"]);
    expect(seededBucketIds).not.toContain("x");
  });

  it("seeds NO source blocks when the user follows no sources (zero source backing)", () => {
    // WHY: with no followed sources, the 30 must contain only backed categories — never
    // a source block with no backing follow.
    const seededBucketIds = new Set(buildSegmentsForSelections(["ai"], []).map((segment) => segment.bucketId));
    for (const bucketId of seededBucketIds) {
      expect(DESIGN_BUCKETS[bucketId].kind).toBe("cat");
    }
  });

  it("seeds NOTHING when both backing sets are empty (no forced bucket)", () => {
    // WHY: with no backing the seed is empty (the screen then falls back to the full default upstream).
    const seededBucketIds = buildSegmentsForSelections([], []).map((segment) => segment.bucketId);
    expect(seededBucketIds).toEqual([]);
  });

  it("seeds UNDER 30 for a narrow pick so the screen opens on 'Fill N more' (no auto-rescale)", () => {
    // WHY: the owner chose to open under-budget rather than rescale; AI(4)+Business(4)+YouTube(2)+X(2)
    // totals 12 (< 30) so the budget CTA prompts the user to fill the rest, not auto-fill.
    const total = sumSegmentCounts(buildSegmentsForSelections(["ai", "business"], ["youtube", "x"]));
    expect(total).toBeLessThan(ALLOCATION_TOTAL);
    expect(total).toBe(12);
  });

  it("preserves the default seed order for the surviving blocks", () => {
    // WHY: order IS the briefing sequence ("the first block plays first") — the filter must not
    // reorder the kept blocks relative to the default (ai, tech, geopolitics, ... youtube, x).
    const seededBucketIds = buildSegmentsForSelections(["geopolitics", "sport", "arts"], ["youtube", "x"]).map(
      (segment) => segment.bucketId,
    );
    expect(seededBucketIds).toEqual(["geopolitics", "sport", "arts", "youtube", "x"]);
  });
});
