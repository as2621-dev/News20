/**
 * Feed-allocation design buckets ("Build your 30, in order", Blip Flow Stage 3) and
 * the EXPLICIT mapping between the UI's design bucket ids and the backend
 * `feed_category` enum.
 *
 * Phase SP3 (taxonomy unification on the picker tree). The onboarding picker, the
 * "Build your 30, in order" screen, the reel chip, AND this allocation surface now
 * draw the SAME canonical category set = the 8 onboarding picker roots
 * (`src/lib/pickerSeedTree.ts` depth-0 ids) + the 2 source axes:
 *
 *    AI · Geopolitics · Business · Environment ·
 *    Politics · Tech · Sport · Arts · YouTube · X
 *
 * There is NO folding: the old "5 topic buckets" design fold
 * (`world → world_politics`, `tech → tech_science`, `markets`, `sport`,
 * `culture` + youtube/x/podcasts) is RETIRED. Each picker root is its own bucket and
 * maps to its own enum value (identity); {@link PICKER_ROOT_TO_CATEGORY_BUCKET} is
 * now an identity map.
 *
 * WHY this module is the single source of truth (Rule 7 — never let two ids drift):
 *  - The DB `feed_category` enum (SP3 migration 0020 adds the 8 roots additively)
 *    carries NO color/label — those live here, in the frontend. This file holds both
 *    the design metadata (label + accent) and the bijection to the enum, so the screen
 *    and the persistence layer can never disagree about which chip writes which row.
 *  - {@link DEFAULT_ALLOCATION_SEGMENTS} is the Rule-7 TWIN of
 *    `agents/pipeline/categories.py` `DEFAULT_FEED_ALLOCATION` — same keys + counts,
 *    summing to {@link ALLOCATION_TOTAL}. If one changes, change both.
 *
 * Static-export safe: pure data + pure helpers, no `window`/server APIs at module scope.
 */

import { logger } from "@/lib/logger";
import type { ContentSourceType } from "@/types/source";

/**
 * The 10 design bucket ids the "Build your 30" screen draws — the 8 onboarding picker
 * roots (`src/lib/pickerSeedTree.ts` depth-0 ids) + the 2 source axes (`youtube`/`x`).
 * No fold: each id equals its picker root verbatim.
 */
export type DesignBucketId =
  | "ai"
  | "geopolitics"
  | "business"
  | "environment"
  | "politics"
  | "tech"
  | "sport"
  | "arts"
  | "youtube"
  | "x";

/**
 * The backend `feed_category` enum values — IDENTICAL to {@link DesignBucketId} after
 * the SP3 unfold (the 8 picker roots + `youtube`/`x`). The old folded enum values
 * (`world_politics`, `tech_science`, `markets`, `culture`, `podcasts`) are
 * retained-unused in Postgres for reversibility but never emitted here.
 */
export type FeedCategoryEnum = DesignBucketId;

/** Whether a bucket is a topic CATEGORY (solid fill) or a SOURCE axis (outlined + glyph). */
export type DesignBucketKind = "cat" | "src";

/** One design bucket's display metadata (color + label + kind + optional source glyph id). */
export interface DesignBucket {
  /** Human-readable label drawn on the chip / segment row. */
  readonly name: string;
  /** The accent color (solid fill for `cat`, outline for `src`). Matches the onboarding picker accent for the root. */
  readonly color: string;
  /** Whether this bucket is a topic category or a source axis. */
  readonly kind: DesignBucketKind;
  /** The `BlipIconDefs` `<symbol>` id for a source glyph (only set when `kind === "src"`). */
  readonly glyph?: string;
}

/**
 * The 10 design buckets, in canonical order (the 8 picker roots first, then the 2
 * source axes). Insertion order matters: {@link DESIGN_BUCKET_IDS} (the Add-sheet /
 * completeness checks) reads it.
 *
 * Accent hexes mirror the onboarding picker / app-wide category palette
 * (`src/lib/interests.ts` `SEGMENT_ACCENT_HEX`, `src/lib/sourceSwipeData.ts`
 * `ACCENT_BY_TOPIC`). `geopolitics`/`business`/`tech`/`sport`/`arts` carry the locked
 * legacy hexes (red/green/cyan/amber/rose). `environment` (emerald) and `politics`
 * (purple) come from the source-swipe palette. `ai` uses the brand primary blue —
 * the only INFERRED hex (no prior dedicated AI accent; flagged for owner review).
 */
export const DESIGN_BUCKETS: Readonly<Record<DesignBucketId, DesignBucket>> = {
  ai: { name: "AI", color: "#3B82F6", kind: "cat" },
  geopolitics: { name: "Geopolitics", color: "#EF4444", kind: "cat" },
  business: { name: "Business", color: "#22C55E", kind: "cat" },
  environment: { name: "Environment", color: "#34D399", kind: "cat" },
  politics: { name: "Politics", color: "#A78BFA", kind: "cat" },
  tech: { name: "Tech", color: "#22D3EE", kind: "cat" },
  sport: { name: "Sport", color: "#F59E0B", kind: "cat" },
  arts: { name: "Arts", color: "#E8B7BC", kind: "cat" },
  youtube: { name: "YouTube", color: "#94A3B8", kind: "src", glyph: "g-yt" },
  x: { name: "X", color: "#CBD5E1", kind: "src", glyph: "g-x" },
};

/** Every design bucket id, in canonical order (the Add-sheet `>= count` gate uses the length). */
export const DESIGN_BUCKET_IDS: readonly DesignBucketId[] = Object.keys(DESIGN_BUCKETS) as DesignBucketId[];

/** The total slots the user must allocate across all buckets (the 30 in "Build your 30"). */
export const ALLOCATION_TOTAL = 30;

/**
 * Design bucket id → `feed_category` enum value. After the SP3 unfold this is an
 * IDENTITY map (each of the 10 ids maps to itself) — kept explicit (not derived) so
 * the bijection stays the ONE declared place the UI ids meet the DB keys (Rule 7).
 */
export const DESIGN_BUCKET_TO_ENUM: Readonly<Record<DesignBucketId, FeedCategoryEnum>> = {
  ai: "ai",
  geopolitics: "geopolitics",
  business: "business",
  environment: "environment",
  politics: "politics",
  tech: "tech",
  sport: "sport",
  arts: "arts",
  youtube: "youtube",
  x: "x",
};

/**
 * `feed_category` enum value → design bucket id (the inverse of {@link DESIGN_BUCKET_TO_ENUM}),
 * derived once from the forward map so the two can never drift. Used to hydrate the screen
 * from a saved allocation.
 */
export const ENUM_TO_DESIGN_BUCKET: Readonly<Record<FeedCategoryEnum, DesignBucketId>> = Object.fromEntries(
  (Object.entries(DESIGN_BUCKET_TO_ENUM) as [DesignBucketId, FeedCategoryEnum][]).map(([designId, enumKey]) => [
    enumKey,
    designId,
  ]),
) as Record<FeedCategoryEnum, DesignBucketId>;

/**
 * Legacy DB sentinel: the `podcasts` enum value the pre-SP3 persistence layer
 * graceful-degraded around (migration 0010). `podcasts` is NOT a current design
 * bucket (the SP3 taxonomy is the 8 roots + youtube/x), so it is typed as a plain
 * string — the value the feed-allocation writer still matches on when an old DB
 * raises "invalid input value for enum" for it.
 */
export const PODCASTS_ENUM_VALUE = "podcasts";

/**
 * The DEFAULT seed allocation: an ordered list of `[designBucketId, slotCount]` totalling
 * exactly {@link ALLOCATION_TOTAL}. This is the Rule-7 TWIN of
 * `agents/pipeline/categories.py` `DEFAULT_FEED_ALLOCATION` — SAME keys + counts (owner-locked
 * 2026-06-18): ai 4 + tech 4 + geopolitics 4 + business 4 + politics 2 + environment 2 +
 * sport 3 + arts 3 + youtube 2 + x 2 = 30. If one changes, change both.
 *
 * Order mirrors the Python dict's insertion order so the seeded "Build your 30" sequence
 * matches the backend allocator's category order.
 */
export const DEFAULT_ALLOCATION_SEGMENTS: ReadonlyArray<readonly [DesignBucketId, number]> = [
  ["ai", 4],
  ["tech", 4],
  ["geopolitics", 4],
  ["business", 4],
  ["politics", 2],
  ["environment", 2],
  ["sport", 3],
  ["arts", 3],
  ["youtube", 2],
  ["x", 2],
];

/** One ordered allocation segment as the screen + persistence layer pass it around. */
export interface AllocationSegment {
  /** The design bucket id this segment allocates slots to. */
  bucketId: DesignBucketId;
  /** How many of the 30 slots this bucket claims (>= 1 in the UI; 0 only on a muted read). */
  count: number;
}

/** Sum the slot counts across an ordered segment list (the budget invariant helper). */
export function sumSegmentCounts(segments: ReadonlyArray<{ count: number }>): number {
  return segments.reduce((runningTotal, segment) => runningTotal + segment.count, 0);
}

/**
 * Build the default ordered segment list (a fresh, mutable copy of
 * {@link DEFAULT_ALLOCATION_SEGMENTS}) for a user with no saved allocation.
 *
 * @returns A new array of {@link AllocationSegment} totalling {@link ALLOCATION_TOTAL}.
 *
 * @example
 * const segs = buildDefaultSegments();
 * sumSegmentCounts(segs); // 30
 */
export function buildDefaultSegments(): AllocationSegment[] {
  return DEFAULT_ALLOCATION_SEGMENTS.map(([bucketId, count]) => ({ bucketId, count }));
}

/**
 * Picker ROOT slug → its screen CATEGORY design bucket. After the SP3 unfold this is an
 * IDENTITY map: each of the 8 depth-0 picker roots (`src/lib/pickerSeedTree.ts`: `ai,
 * geopolitics, business, environment, politics, tech, sport, arts`) maps to the design
 * bucket of the SAME id. A selection's `followId` is a `/`-joined path whose FIRST
 * segment is its root.
 *
 * This is the FRONTEND twin of `agents/pipeline/categories.py` `SLUG_TO_CATEGORY` for the
 * 8 roots (which is likewise identity there). The old fold map
 * (`ai/tech → tech`, `geopolitics/politics/environment → world`, `business → markets`,
 * `arts → culture`) is RETIRED.
 *
 * Source axes (`youtube`/`x`) are NOT picker roots — they come from the source swipe, not
 * the topic picker, so they are absent here (and never filtered). A root NOT in this map is
 * DROPPED (logged), never mis-bucketed (Rule 12).
 */
export const PICKER_ROOT_TO_CATEGORY_BUCKET: Readonly<Record<string, DesignBucketId>> = {
  ai: "ai",
  geopolitics: "geopolitics",
  business: "business",
  environment: "environment",
  politics: "politics",
  tech: "tech",
  sport: "sport",
  arts: "arts",
};

/**
 * Derive the DISTINCT category buckets a user selected, from their picker follows.
 *
 * Each follow's `followId` is a `/`-joined path (`ai/foundation-models-llms/.../openai`),
 * so its root segment (`followId.split("/")[0]`) is the picker category root, mapped to a
 * screen category bucket via {@link PICKER_ROOT_TO_CATEGORY_BUCKET}. Unknown roots are
 * dropped + logged (never mis-bucketed — Rule 12). The result feeds
 * {@link buildSegmentsForSelections}; an EMPTY result (with no followed sources) means "no
 * selection signal" and the caller falls back to the full default seed.
 *
 * @param follows - The user's picker selections (only `followId` is read).
 * @returns The distinct category bucket ids the user picked (first-seen order).
 *
 * @example
 * categoryBucketsFromFollows([{ followId: "ai/llms/openai" }, { followId: "business/equities" }]);
 * // ["ai", "business"]
 */
export function categoryBucketsFromFollows(follows: ReadonlyArray<{ followId: string }>): DesignBucketId[] {
  const selectedBuckets = new Set<DesignBucketId>();
  for (const follow of follows) {
    const rootSegment = follow.followId.split("/")[0];
    const categoryBucketId = PICKER_ROOT_TO_CATEGORY_BUCKET[rootSegment];
    if (categoryBucketId === undefined) {
      logger.warn("category_bucket_root_unmapped", {
        follow_id: follow.followId,
        root_segment: rootSegment,
        fix_suggestion:
          "Add the picker root to PICKER_ROOT_TO_CATEGORY_BUCKET if it should seed a 'Build your 30' category block.",
      });
      continue;
    }
    selectedBuckets.add(categoryBucketId);
  }
  return [...selectedBuckets];
}

/**
 * A followed CONTENT SOURCE's `content_source_type` → the SOURCE design bucket it stocks
 * in "Build your 30". The screen draws two source axes (`youtube`/`x`), but the catalog has
 * four source types: `personality` (a named creator addressed as a source) has no axis of
 * its own and rides the `x` axis — it shares `x_account`'s circular-avatar treatment
 * (`SourceArtwork`). `podcast` likewise has no dedicated SP3 axis and rides `youtube` (the
 * closest "long-form creator feed" axis) so a followed podcast still seeds a source block.
 *
 * This is the SOURCE twin of {@link PICKER_ROOT_TO_CATEGORY_BUCKET}: it lets the screen
 * gate which SOURCE blocks may seed the 30 on the sources the user ACTUALLY follows. A
 * source bucket with no backing follow is exactly the phantom-block bug this removes —
 * owner rule (2026-06-17): only categories the user has an interest OR a source for may
 * appear in "Build your 30".
 */
export const SOURCE_TYPE_TO_DESIGN_BUCKET: Readonly<Record<ContentSourceType, DesignBucketId>> = {
  youtube_channel: "youtube",
  podcast: "youtube",
  x_account: "x",
  personality: "x",
};

/**
 * Derive the DISTINCT source buckets a user follows, from their followed content sources.
 * Each source's `content_source_type` maps to a source design bucket via
 * {@link SOURCE_TYPE_TO_DESIGN_BUCKET}. The result gates which SOURCE blocks
 * {@link buildSegmentsForSelections} may seed (and which source chips the Add-block sheet
 * offers) — so a source axis the user follows NOTHING on never appears in the 30.
 *
 * @param followedSources - The user's followed sources (only `content_source_type` is read).
 * @returns The distinct source bucket ids the user follows (first-seen order).
 *
 * @example
 * sourceBucketsFromFollows([{ content_source_type: "youtube_channel" }, { content_source_type: "x_account" }]);
 * // ["youtube", "x"]
 */
export function sourceBucketsFromFollows(
  followedSources: ReadonlyArray<{ content_source_type: ContentSourceType }>,
): DesignBucketId[] {
  const followedBuckets = new Set<DesignBucketId>();
  for (const source of followedSources) {
    const bucketId = SOURCE_TYPE_TO_DESIGN_BUCKET[source.content_source_type];
    if (bucketId !== undefined) {
      followedBuckets.add(bucketId);
    }
  }
  return [...followedBuckets];
}

/**
 * Derive the DISTINCT category buckets a user backs, from their rolled-up interest vector
 * (`src/lib/interestVector.ts` `rollUpInterestVector` — summed over BOTH topic follows and
 * entity follows). Each pinned key with a positive weight is mapped to its screen category
 * bucket via {@link PICKER_ROOT_TO_CATEGORY_BUCKET} (the vector's keys are
 * `ARCHETYPE_CATEGORY_KEYS`, which equal that map's keys exactly, so the fold is total).
 *
 * Unlike {@link categoryBucketsFromFollows} (which reads the IN-MEMORY picker selections by
 * their `/`-joined `followId`), this reads PERSISTED backing — used by the library "Thirty"
 * tab, where there is no live picker session, only the user's saved follows.
 *
 * @param interestVector - A pinned-key → weight map (an empty/zero vector = no backing).
 * @returns The distinct category bucket ids the user backs (first-seen order).
 *
 * @example
 * categoryBucketsFromInterestVector({ ai: 4.0, sport: 1.2 }); // ["ai", "sport"]
 */
export function categoryBucketsFromInterestVector(interestVector: Readonly<Record<string, number>>): DesignBucketId[] {
  const backedBuckets = new Set<DesignBucketId>();
  for (const [pinnedKey, weight] of Object.entries(interestVector)) {
    if (weight <= 0) {
      continue;
    }
    const bucketId = PICKER_ROOT_TO_CATEGORY_BUCKET[pinnedKey];
    if (bucketId === undefined) {
      logger.warn("category_bucket_pinned_key_unmapped", {
        pinned_key: pinnedKey,
        fix_suggestion:
          "Add the pinned key to PICKER_ROOT_TO_CATEGORY_BUCKET if it should back a 'Build your 30' category block.",
      });
      continue;
    }
    backedBuckets.add(bucketId);
  }
  return [...backedBuckets];
}

/**
 * The COMPLETE set of design buckets a user's "Build your 30" may contain, given their real
 * backing — the SINGLE guard enforcing the owner rule (2026-06-17: only categories the user
 * has a followed interest OR source for may appear). Membership:
 *  - every category bucket in `allowedCategoryBuckets` (backed by a followed interest);
 *  - every source bucket in `followedSourceBuckets` (backed by a followed source).
 *
 * Both the seed ({@link buildSegmentsForSelections}) and the Add-block sheet read this set, so
 * a phantom block can neither be seeded NOR manually re-added once the screen has a real
 * selection signal.
 *
 * @param allowedCategoryBuckets - The category buckets the user backs (see
 *   {@link categoryBucketsFromFollows} / {@link categoryBucketsFromInterestVector}).
 * @param followedSourceBuckets - The source buckets the user follows (see {@link sourceBucketsFromFollows}).
 * @returns A set of every bucket id the screen may show.
 */
export function allowedBucketsForSelections(
  allowedCategoryBuckets: Iterable<DesignBucketId>,
  followedSourceBuckets: Iterable<DesignBucketId>,
): Set<DesignBucketId> {
  const allowed = new Set<DesignBucketId>(allowedCategoryBuckets);
  for (const sourceBucket of followedSourceBuckets) {
    allowed.add(sourceBucket);
  }
  return allowed;
}

/**
 * Build the seed segments for a user who backs a SUBSET of buckets — the default allocation
 * filtered to the buckets they actually back, so "Build your 30" no longer shows categories
 * the user skipped OR source axes they follow nothing on (the phase-5a behaviour was: always
 * all category blocks + all source blocks).
 *
 * Kept in the seed (every other bucket is dropped):
 *  - every category bucket in `allowedCategoryBuckets` (backed by a followed interest);
 *  - every source bucket in `followedSourceBuckets` (backed by a followed source).
 * Counts are the default seed values — the kept blocks may total UNDER 30, so the screen opens
 * on "Fill N more" (owner decision: no auto-rescale), which the budget CTA already handles.
 *
 * @param allowedCategoryBuckets - The category buckets the user backs.
 * @param followedSourceBuckets - The source buckets the user follows (was previously never gated).
 * @returns A fresh, mutable ordered segment list (same order as the default seed).
 *
 * @example
 * // User picked AI + Business and follows a YouTube channel → ai, business, youtube:
 * buildSegmentsForSelections(["ai", "business"], ["youtube"]);
 */
export function buildSegmentsForSelections(
  allowedCategoryBuckets: Iterable<DesignBucketId>,
  followedSourceBuckets: Iterable<DesignBucketId>,
): AllocationSegment[] {
  const allowed = allowedBucketsForSelections(allowedCategoryBuckets, followedSourceBuckets);
  return DEFAULT_ALLOCATION_SEGMENTS.filter(([bucketId]) => allowed.has(bucketId)).map(([bucketId, count]) => ({
    bucketId,
    count,
  }));
}
