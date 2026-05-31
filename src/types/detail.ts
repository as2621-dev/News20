/**
 * Story Detail contract for the swipe-right Detail view (Phase 2 / M2).
 *
 * **Why this file is the seam.** Sub-phase 1 fetches these shapes from Supabase
 * (`src/lib/detail/fetchStoryDetail.ts`); Sub-phases 2–4 render them (chunked
 * Playfair body, key-figure card, trust strip, "how it developed" timeline). The
 * field names are deliberately aligned to the production Postgres schema
 * (`reference/supabase-schema.md` §2), so each interface maps 1:1 to a table.
 *
 * **OQ#2 resolution (Rule 7).** `reference/api-contracts.md` modelled a stale
 * `detail_visuals: DetailVisual[]` gallery (graph/timeline/image/chart). The
 * newer, prototype-derived `supabase-schema.md` has **no such table** — the
 * Detail's only visuals are the key-figure card + the ambient poster. Resolved
 * toward the schema (more recent, more tested): **no `DetailVisual[]` gallery**.
 * Where the contract and schema agree on a field name, the schema name wins.
 *
 * Field provenance (table.column → field):
 * - `detail_chunks.{chunk_index, chunk_text}`                  → {@link DetailChunk}
 * - `story_trust.{coverage_*_count, blindspot_lean, opposing_view_text}` → {@link TrustSummary}
 * - `story_sources.{source_outlet_name, source_bias_lean, ...}` → {@link StorySource}
 * - `story_timeline.{timeline_event_index, timeline_when_label, timeline_what_text}` → {@link TimelineEvent}
 * - `suggested_questions.{question_index, question_text}`      → {@link SuggestedQuestion}
 * - `stories.{story_key_figure_value, story_key_figure_label}` → {@link KeyFigure}
 *
 * Verbose, entity-prefixed names per CLAUDE.md.
 */

/**
 * Bias lean for outlets and per-source rows (AllSides / Ad Fontes model).
 * Mirrors the `bias_lean` Postgres enum (`reference/supabase-schema.md` §1).
 */
export type BiasLean = "left" | "center" | "right";

/**
 * One chunked paragraph of the readable Detail body — a `detail_chunks` row.
 *
 * Maps `detail_chunks.{chunk_index, chunk_text}`. Chunks render in ascending
 * `chunk_index` order as the Playfair reading body.
 */
export interface DetailChunk {
  /** 0-based paragraph order (`detail_chunks.chunk_index`). */
  chunk_index: number;
  /** One paragraph of body text (`detail_chunks.chunk_text`). */
  chunk_text: string;
}

/**
 * The per-story trust summary backing the Detail "COVERAGE" strip — a
 * `story_trust` row.
 *
 * Maps `story_trust.{coverage_left_count, coverage_center_count,
 * coverage_right_count, coverage_outlet_count, blindspot_lean,
 * opposing_view_text}`. `blindspot_lean` is `null` when no side is materially
 * under-covered; `opposing_view_text` is `null` when there is no opposing-view
 * card (both nullable in the schema). Supersedes `BiasBreakdown` in
 * `api-contracts.md`, using the schema column names.
 */
export interface TrustSummary {
  /** Outlets covering this story leaning left (`story_trust.coverage_left_count`). */
  coverage_left_count: number;
  /** Outlets leaning center (`story_trust.coverage_center_count`). */
  coverage_center_count: number;
  /** Outlets leaning right (`story_trust.coverage_right_count`). */
  coverage_right_count: number;
  /** Total outlets ("COVERED BY N OUTLETS"; `story_trust.coverage_outlet_count`). */
  coverage_outlet_count: number;
  /**
   * The materially under-covered lean, or `null` when balanced. The >70%-one-side
   * rule is applied at write time (`story_trust.blindspot_lean`); the chip shows
   * only when this is non-null.
   */
  blindspot_lean: BiasLean | null;
  /** The opposing-view card quote, or `null` (`story_trust.opposing_view_text`). */
  opposing_view_text: string | null;
}

/**
 * One source outlet backing a story — a `story_sources` row.
 *
 * Maps `story_sources.{source_outlet_name, source_bias_lean, source_article_url,
 * source_published_utc, source_is_citation}`. `source_bias_lean`,
 * `source_article_url`, and `source_published_utc` are nullable in the schema.
 */
export interface StorySource {
  /** Outlet name, denormalized (`story_sources.source_outlet_name`). */
  source_outlet_name: string;
  /** Resolved bias lean (sort key), or `null` (`story_sources.source_bias_lean`). */
  source_bias_lean: BiasLean | null;
  /** Canonical article link, or `null` (`story_sources.source_article_url`). */
  source_article_url: string | null;
  /** Publish time as ISO string, or `null` (`story_sources.source_published_utc`). */
  source_published_utc: string | null;
  /** True when shown as a Q&A citation chip (`story_sources.source_is_citation`). */
  source_is_citation: boolean;
}

/**
 * One "HOW IT DEVELOPED" timeline event — a `story_timeline` row.
 *
 * Maps `story_timeline.{timeline_event_index, timeline_when_label,
 * timeline_what_text}`. Events render in ascending `timeline_event_index` order.
 */
export interface TimelineEvent {
  /** Order within the story (`story_timeline.timeline_event_index`). */
  timeline_event_index: number;
  /** Display label, mono ("08:10", "Mon", "1993"; `story_timeline.timeline_when_label`). */
  timeline_when_label: string;
  /** The development sentence (`story_timeline.timeline_what_text`). */
  timeline_what_text: string;
}

/**
 * One tappable suggested-question chip — a `suggested_questions` row.
 *
 * Maps `suggested_questions.{question_index, question_text}`. Chips render in
 * ascending `question_index` order. (Consumed by Detail Q&A in Phase 2b; carried
 * here so the Detail payload is complete.)
 */
export interface SuggestedQuestion {
  /** Chip display order (`suggested_questions.question_index`). */
  question_index: number;
  /** The question text (`suggested_questions.question_text`). */
  question_text: string;
}

/**
 * The Detail key-figure card — the `story_key_figure_*` fields on `stories`.
 *
 * Maps `stories.{story_key_figure_value, story_key_figure_label}`. Both are
 * nullable in the schema (a story may have no key figure).
 *
 * @example
 * { key_figure_value: "~20%", key_figure_label: "of global oil transits Hormuz" }
 */
export interface KeyFigure {
  /** The headline figure ("~20%", "$81.6B"; `stories.story_key_figure_value`). */
  key_figure_value: string | null;
  /** What the figure measures (`stories.story_key_figure_label`). */
  key_figure_label: string | null;
}

/**
 * The full swipe-right Detail payload for one story — one call to
 * {@link import("@/lib/detail/fetchStoryDetail").fetchStoryDetail} returns this,
 * fully populated from Supabase-direct reads.
 *
 * `story_id` is the `stories.story_id` slug (`"s1"`..`"s5"` in the prototype).
 * Note: the reel `Story.digest_id` field (`src/types/feed.ts`) holds that same
 * slug, so Sub-phase 2 passes `activeStory.digest_id` as the `story_id` arg.
 *
 * **No `detail_visuals` / `DetailVisual[]` gallery** (OQ#2) — the only Detail
 * visuals are {@link key_figure} and the ambient poster (already on `Story`).
 */
export interface StoryDetail {
  /** The `stories.story_id` slug this payload is for (`"s1"`..`"s5"`). */
  story_id: string;
  /** Readable body paragraphs, ordered by `chunk_index`. */
  detail_chunks: DetailChunk[];
  /** The trust/coverage summary (the "COVERAGE" strip). */
  trust_summary: TrustSummary;
  /** The key-figure card values (may be all-null). */
  key_figure: KeyFigure;
  /** The source outlets backing the story. */
  sources: StorySource[];
  /** "HOW IT DEVELOPED" events, ordered by `timeline_event_index`. */
  timeline: TimelineEvent[];
  /** Tappable suggested-question chips, ordered by `question_index`. */
  suggested_questions: SuggestedQuestion[];
}
