/**
 * Feed-allocation design buckets ("Build your 30, in order", Blip Flow Stage 3) and
 * the EXPLICIT mapping between the UI's 9 design bucket ids and the backend
 * `feed_category` enum (migration `0008_feed_allocation.sql` + `0010_feed_category_podcasts.sql`).
 *
 * WHY this module is the single source of truth (Rule 7 — never let two ids drift):
 *  - The prototype (`blip-sequence.js`) draws 9 buckets with its OWN short design ids
 *    (`world`, `tech`, …) and 9 colors/glyphs. The DB enum uses snake_case machine
 *    keys (`world_politics`, `tech_science`, …) and has NO color/label (those live in
 *    the frontend per migration 0008 §1). This file holds BOTH and the bijection between
 *    them, so the screen and the persistence layer can never disagree about which design
 *    chip writes which enum row.
 *  - `podcasts` is the ONE design bucket with no enum value YET — migration 0010 adds it
 *    (additive `alter type … add value`). Until 0010 is applied to the live DB, a
 *    `podcasts` write fails with a Postgres "invalid input value for enum" error; the
 *    persistence layer ({@link import("./feedAllocation")}) degrades gracefully on exactly
 *    that signal. The mapping is defined here regardless so the moment 0010 lands the
 *    round-trip just works with no code change.
 *
 * Static-export safe: pure data + pure helpers, no `window`/server APIs at module scope.
 */

/** The 9 design bucket ids the "Build your 30" screen draws (verbatim from the prototype). */
export type DesignBucketId =
  | "breaking"
  | "world"
  | "markets"
  | "tech"
  | "sport"
  | "culture"
  | "youtube"
  | "x"
  | "podcasts";

/** The backend `feed_category` enum values (8 from migration 0008 + `podcasts` from 0010). */
export type FeedCategoryEnum =
  | "breaking"
  | "world_politics"
  | "tech_science"
  | "youtube"
  | "markets"
  | "sport"
  | "x"
  | "culture"
  | "podcasts";

/** Whether a bucket is a topic CATEGORY (solid fill) or a SOURCE axis (outlined + glyph). */
export type DesignBucketKind = "cat" | "src";

/** One design bucket's display metadata (color + label + kind + optional source glyph id). */
export interface DesignBucket {
  /** Human-readable label drawn on the chip / segment row. */
  readonly name: string;
  /** The accent color (solid fill for `cat`, outline for `src`) — verbatim from the prototype. */
  readonly color: string;
  /** Whether this bucket is a topic category or a source axis. */
  readonly kind: DesignBucketKind;
  /** The `BlipIconDefs` `<symbol>` id for a source glyph (only set when `kind === "src"`). */
  readonly glyph?: string;
}

/**
 * The 9 design buckets, in the prototype's declaration order. Insertion order matters:
 * {@link DESIGN_BUCKET_IDS} (the Add-sheet / completeness checks) reads it.
 */
export const DESIGN_BUCKETS: Readonly<Record<DesignBucketId, DesignBucket>> = {
  breaking: { name: "Breaking News", color: "#FACC15", kind: "cat" },
  world: { name: "World & Politics", color: "#EF4444", kind: "cat" },
  markets: { name: "Markets", color: "#22C55E", kind: "cat" },
  tech: { name: "Tech & Science", color: "#22D3EE", kind: "cat" },
  sport: { name: "Sport", color: "#F59E0B", kind: "cat" },
  culture: { name: "Culture", color: "#E8B7BC", kind: "cat" },
  youtube: { name: "YouTube", color: "#94A3B8", kind: "src", glyph: "g-yt" },
  x: { name: "X", color: "#CBD5E1", kind: "src", glyph: "g-x" },
  podcasts: { name: "Podcasts", color: "#A5B4FC", kind: "src", glyph: "g-pod" },
};

/** Every design bucket id, in the prototype's order (the Add-sheet `>= count` gate uses the length). */
export const DESIGN_BUCKET_IDS: readonly DesignBucketId[] = Object.keys(DESIGN_BUCKETS) as DesignBucketId[];

/** The total slots the user must allocate across all buckets (the 30 in "Build your 30"). */
export const ALLOCATION_TOTAL = 30;

/**
 * Design bucket id → `feed_category` enum value. The ONLY place the UI ids are mapped
 * to the DB keys (Rule 7). `world`/`tech` rename to their snake_case enum keys; the
 * rest are identical strings. `podcasts` maps to the 0010 enum value (see module JSDoc).
 */
export const DESIGN_BUCKET_TO_ENUM: Readonly<Record<DesignBucketId, FeedCategoryEnum>> = {
  breaking: "breaking",
  world: "world_politics",
  markets: "markets",
  tech: "tech_science",
  sport: "sport",
  culture: "culture",
  youtube: "youtube",
  x: "x",
  podcasts: "podcasts",
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
 * The enum value `podcasts` maps to — the ONE value that may not yet exist in the live DB
 * (added by migration 0010). The persistence layer keys its graceful-degrade path off this.
 */
export const PODCASTS_ENUM_VALUE: FeedCategoryEnum = "podcasts";

/**
 * The prototype's DEFAULT seed allocation (`blip-sequence.js` line 52): an ordered list of
 * `[designBucketId, slotCount]` totalling exactly {@link ALLOCATION_TOTAL}. Used when a user
 * has no saved allocation yet.
 */
export const DEFAULT_ALLOCATION_SEGMENTS: ReadonlyArray<readonly [DesignBucketId, number]> = [
  ["breaking", 2],
  ["world", 4],
  ["tech", 5],
  ["youtube", 6],
  ["markets", 4],
  ["sport", 3],
  ["x", 3],
  ["culture", 3],
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
