/**
 * Supabase-backed Story Detail source (Phase 2 SP1) for the swipe-right Detail
 * view of the audio-first karaoke reel (blip / "News20").
 *
 * Sibling of `src/lib/feed/supabaseFeed.ts`: same Supabase-direct read pattern
 * (injected client, PostgREST selects, `fix_suggestion` error idiom), different
 * payload. {@link fetchStoryDetail} issues the reads needed to populate the
 * {@link StoryDetail} contract (`src/types/detail.ts`) for one story:
 * `detail_chunks` (by `chunk_index`), `story_trust` (1:1 coverage + blindspot +
 * opposing view), `story_timeline` (by `timeline_event_index`), `story_sources`,
 * `suggested_questions`, plus the key-figure fields on `stories`.
 *
 * OQ#2 (Rule 7): **no `detail_visuals` / `DetailVisual[]` gallery** — the schema
 * has no such table; the Detail's only visuals are the key-figure card + ambient
 * poster. See `src/types/detail.ts`.
 *
 * Ordering is done at the query layer (`.order(...)`), so chunks and timeline
 * events come back in index order even though PostgREST does not guarantee row
 * order otherwise.
 */

import type { PostgrestError, SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type {
  AnalyticKind,
  AnalyticRow,
  BiasLean,
  CoverageMode,
  DetailChunk,
  DetailKeyPoint,
  KeyFigure,
  SecondAnalytic,
  StoryDetail,
  StorySource,
  SuggestedQuestion,
  TimelineEvent,
  TrustSummary,
} from "@/types/detail";

/** `detail_chunks` columns read for the Playfair body. */
const DETAIL_CHUNK_SELECT = "chunk_index,chunk_text";
/**
 * `story_trust` columns read for the COVERAGE strip (1:1 with the story). Phase
 * 2c adds the four adaptive-coverage reach columns alongside the partisan counts.
 */
const STORY_TRUST_SELECT =
  "coverage_left_count,coverage_center_count,coverage_right_count," +
  "coverage_outlet_count,blindspot_lean,opposing_view_text," +
  "coverage_mode,coverage_momentum,coverage_originating_outlet_name,coverage_notable_outlet_names";
/** `story_timeline` columns read for the "HOW IT DEVELOPED" drawer. */
const STORY_TIMELINE_SELECT = "timeline_event_index,timeline_when_label,timeline_what_text";
/** `story_sources` columns read for the sources / citation chips. */
const STORY_SOURCE_SELECT =
  "source_outlet_name,source_bias_lean,source_article_url,source_published_utc,source_is_citation";
/** `suggested_questions` columns read for the Q&A chips. */
const SUGGESTED_QUESTION_SELECT = "question_index,question_text";
/** `stories` key-figure + detail-category columns for the Detail card + panel template. */
const STORY_KEY_FIGURE_SELECT = "story_key_figure_value,story_key_figure_label,story_detail_category";
/** `story_analytics` columns read for the category's analytic panels (1:N, ordered by slot). */
const STORY_ANALYTICS_SELECT =
  "analytic_slot_index,analytic_kind,analytic_tab_label,analytic_headline,analytic_summary_text,analytic_rows,analytic_is_grounded";
/** `detail_key_points` columns read for the 5 at-a-glance bullets (Phase 2c). */
const DETAIL_KEY_POINT_SELECT = "key_point_index,key_point_text";

/** Raw `detail_chunks` row. */
interface DetailChunkRow {
  chunk_index: number;
  chunk_text: string;
}

/** Raw `story_trust` row (1:1; nullable blindspot/opposing-view + Phase 2c reach cols). */
interface StoryTrustRow {
  coverage_left_count: number;
  coverage_center_count: number;
  coverage_right_count: number;
  coverage_outlet_count: number;
  blindspot_lean: BiasLean | null;
  opposing_view_text: string | null;
  coverage_mode: CoverageMode;
  coverage_momentum: string | null;
  coverage_originating_outlet_name: string | null;
  coverage_notable_outlet_names: string[] | null;
}

/** Raw `story_analytics` row (1:N; one per slot). `analytic_rows` is a JSONB array. */
interface StoryAnalyticsRow {
  analytic_slot_index: number;
  analytic_kind: AnalyticKind;
  analytic_tab_label: string;
  analytic_headline: string;
  analytic_summary_text: string;
  analytic_rows: AnalyticRow[] | null;
  analytic_is_grounded: boolean;
}

/** Raw `detail_key_points` row (Phase 2c). */
interface DetailKeyPointRow {
  key_point_index: number;
  key_point_text: string;
}

/** Raw `story_timeline` row. */
interface StoryTimelineRow {
  timeline_event_index: number;
  timeline_when_label: string;
  timeline_what_text: string;
}

/** Raw `story_sources` row (nullable bias/url/published). */
interface StorySourceRow {
  source_outlet_name: string;
  source_bias_lean: BiasLean | null;
  source_article_url: string | null;
  source_published_utc: string | null;
  source_is_citation: boolean;
}

/** Raw `suggested_questions` row. */
interface SuggestedQuestionRow {
  question_index: number;
  question_text: string;
}

/** Raw `stories` key-figure + detail-category projection. */
interface StoryKeyFigureRow {
  story_key_figure_value: string | null;
  story_key_figure_label: string | null;
  story_detail_category: string | null;
}

/**
 * Build the standard `fix_suggestion` error for a failed Detail read.
 *
 * @param table - The table the failing query targeted.
 * @param story_id - The story slug being fetched.
 * @param error - The PostgREST error (its `.message` is safe to surface; never log tokens).
 */
function detailReadError(table: string, story_id: string, error: PostgrestError): Error {
  return new Error(
    `Failed to load ${table} for story "${story_id}": ${error.message}. ` +
      "fix_suggestion: confirm migrations applied, RLS allows anon SELECT, and the seed ran.",
  );
}

/**
 * Pick the share-target URL from `story_sources` rows.
 *
 * Preference order: the first citation row with a URL (the article the digest
 * was actually written from), else the first row with any URL, else `null`.
 *
 * @param rows - The story's `story_sources` rows (any order).
 * @returns The best shareable article URL, or `null` when none carries a URL.
 *
 * @example
 * pickPrimarySourceArticleUrl([
 *   { source_article_url: "https://b.example", source_is_citation: false },
 *   { source_article_url: "https://a.example", source_is_citation: true },
 * ]); // → "https://a.example"
 */
export function pickPrimarySourceArticleUrl(
  rows: Pick<StorySourceRow, "source_article_url" | "source_is_citation">[],
): string | null {
  const citationUrl = rows.find((row) => row.source_is_citation && row.source_article_url)?.source_article_url;
  if (citationUrl) {
    return citationUrl;
  }
  return rows.find((row) => row.source_article_url)?.source_article_url ?? null;
}

/**
 * Fetch ONLY the primary source article URL for one story (the share payload).
 *
 * A lean single-table read for the reel's Share button — avoids paying
 * {@link fetchStoryDetail}'s eight concurrent reads on a share tap. Best-effort:
 * a read error logs and resolves `null` (the caller shares headline-only).
 *
 * @param story_id - The `stories.story_id` slug (carried on `Story.digest_id`).
 * @param client - Optional Supabase client (injected in tests).
 * @returns The primary source article URL, or `null` when unavailable.
 */
export async function fetchPrimarySourceArticleUrl(
  story_id: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<string | null> {
  const { data, error } = await client
    .from("story_sources")
    .select("source_article_url,source_is_citation")
    .eq("source_story_id", story_id)
    .returns<Pick<StorySourceRow, "source_article_url" | "source_is_citation">[]>();
  if (error) {
    logger.warn("share_source_url_fetch_failed", {
      story_id,
      error_message: error.message,
      fix_suggestion: "Sharing falls back to headline-only; check story_sources RLS/seed if persistent.",
    });
    return null;
  }
  return pickPrimarySourceArticleUrl(data ?? []);
}

/**
 * Fetch the full Story Detail payload for one story from Supabase.
 *
 * Issues the per-table reads that back the swipe-right Detail view and returns
 * one populated {@link StoryDetail}. `detail_chunks` come back ordered by
 * `chunk_index`; `story_timeline` ordered by `timeline_event_index`;
 * `suggested_questions` ordered by `question_index`. `story_trust` is 1:1, so a
 * missing trust row throws (a broken seed) rather than rendering a blank strip.
 *
 * @param story_id - The `stories.story_id` slug (`"s1"`..`"s5"` in the prototype).
 *   Sub-phase 2 passes the reel's `activeStory.digest_id`, which holds this slug.
 * @param client - Optional Supabase client (injected in tests). Defaults to the
 *   shared browser anon client.
 * @returns The fully populated Detail payload for the story.
 * @throws If any read errors, or if the story has no `story_trust` row.
 *
 * @example
 * const detail = await fetchStoryDetail("s1");
 * detail.detail_chunks[0].chunk_text;          // first body paragraph
 * detail.trust_summary.coverage_outlet_count;  // "COVERED BY N OUTLETS"
 * detail.key_figure.key_figure_value;          // "~20%"
 */
export async function fetchStoryDetail(
  story_id: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<StoryDetail> {
  // Reason: independent reads, no inter-query dependency — issue them concurrently.
  const [
    chunksResult,
    trustResult,
    timelineResult,
    sourcesResult,
    questionsResult,
    storyResult,
    analyticsResult,
    keyPointsResult,
  ] = await Promise.all([
    client
      .from("detail_chunks")
      .select(DETAIL_CHUNK_SELECT)
      .eq("detail_story_id", story_id)
      .order("chunk_index", { ascending: true })
      .returns<DetailChunkRow[]>(),
    client.from("story_trust").select(STORY_TRUST_SELECT).eq("trust_story_id", story_id).maybeSingle<StoryTrustRow>(),
    client
      .from("story_timeline")
      .select(STORY_TIMELINE_SELECT)
      .eq("timeline_story_id", story_id)
      .order("timeline_event_index", { ascending: true })
      .returns<StoryTimelineRow[]>(),
    client
      .from("story_sources")
      .select(STORY_SOURCE_SELECT)
      .eq("source_story_id", story_id)
      .returns<StorySourceRow[]>(),
    client
      .from("suggested_questions")
      .select(SUGGESTED_QUESTION_SELECT)
      .eq("question_story_id", story_id)
      .order("question_index", { ascending: true })
      .returns<SuggestedQuestionRow[]>(),
    client.from("stories").select(STORY_KEY_FIGURE_SELECT).eq("story_id", story_id).maybeSingle<StoryKeyFigureRow>(),
    // The category's analytic panels (1-3), ordered by slot (may be absent → []).
    client
      .from("story_analytics")
      .select(STORY_ANALYTICS_SELECT)
      .eq("analytic_story_id", story_id)
      .order("analytic_slot_index", { ascending: true })
      .returns<StoryAnalyticsRow[]>(),
    // Phase 2c: the 5 at-a-glance bullets, ordered by index.
    client
      .from("detail_key_points")
      .select(DETAIL_KEY_POINT_SELECT)
      .eq("key_point_story_id", story_id)
      .order("key_point_index", { ascending: true })
      .returns<DetailKeyPointRow[]>(),
  ]);

  if (chunksResult.error) {
    throw detailReadError("detail_chunks", story_id, chunksResult.error);
  }
  if (trustResult.error) {
    throw detailReadError("story_trust", story_id, trustResult.error);
  }
  if (timelineResult.error) {
    throw detailReadError("story_timeline", story_id, timelineResult.error);
  }
  if (sourcesResult.error) {
    throw detailReadError("story_sources", story_id, sourcesResult.error);
  }
  if (questionsResult.error) {
    throw detailReadError("suggested_questions", story_id, questionsResult.error);
  }
  if (storyResult.error) {
    throw detailReadError("stories", story_id, storyResult.error);
  }
  if (analyticsResult.error) {
    throw detailReadError("story_analytics", story_id, analyticsResult.error);
  }
  if (keyPointsResult.error) {
    throw detailReadError("detail_key_points", story_id, keyPointsResult.error);
  }
  if (!storyResult.data) {
    throw new Error(
      `Story "${story_id}" not found. fix_suggestion: confirm the story_id slug exists and the seed ran.`,
    );
  }
  if (!trustResult.data) {
    throw new Error(
      `Story "${story_id}" has no story_trust row. fix_suggestion: re-run the seed (story_trust is 1:1).`,
    );
  }

  const trustRow = trustResult.data;
  const trust_summary: TrustSummary = {
    coverage_left_count: trustRow.coverage_left_count,
    coverage_center_count: trustRow.coverage_center_count,
    coverage_right_count: trustRow.coverage_right_count,
    coverage_outlet_count: trustRow.coverage_outlet_count,
    blindspot_lean: trustRow.blindspot_lean,
    opposing_view_text: trustRow.opposing_view_text,
    // Phase 2c adaptive-coverage reach fields (`reach` mode uses these; `partisan`
    // mode leaves momentum/originating null + notable empty).
    coverage_mode: trustRow.coverage_mode,
    coverage_momentum: trustRow.coverage_momentum,
    coverage_originating_outlet: trustRow.coverage_originating_outlet_name,
    coverage_notable_outlets: trustRow.coverage_notable_outlet_names ?? [],
  };

  const key_figure: KeyFigure = {
    key_figure_value: storyResult.data.story_key_figure_value,
    key_figure_label: storyResult.data.story_key_figure_label,
  };

  const detail_chunks: DetailChunk[] = (chunksResult.data ?? []).map((row) => ({
    chunk_index: row.chunk_index,
    chunk_text: row.chunk_text,
  }));

  const timeline: TimelineEvent[] = (timelineResult.data ?? []).map((row) => ({
    timeline_event_index: row.timeline_event_index,
    timeline_when_label: row.timeline_when_label,
    timeline_what_text: row.timeline_what_text,
  }));

  const sources: StorySource[] = (sourcesResult.data ?? []).map((row) => ({
    source_outlet_name: row.source_outlet_name,
    source_bias_lean: row.source_bias_lean,
    source_article_url: row.source_article_url,
    source_published_utc: row.source_published_utc,
    source_is_citation: row.source_is_citation,
  }));

  const suggested_questions: SuggestedQuestion[] = (questionsResult.data ?? []).map((row) => ({
    question_index: row.question_index,
    question_text: row.question_text,
  }));

  // The category's analytic panels, ordered by slot (empty when the story has no
  // analytic rows yet — e.g. a pre-migration story; the UI null-guards).
  const analytic_panels: SecondAnalytic[] = (analyticsResult.data ?? []).map((row) => ({
    analytic_slot_index: row.analytic_slot_index,
    analytic_kind: row.analytic_kind,
    analytic_tab_label: row.analytic_tab_label,
    analytic_headline: row.analytic_headline,
    analytic_summary_text: row.analytic_summary_text,
    analytic_rows: row.analytic_rows ?? [],
    analytic_is_grounded: row.analytic_is_grounded,
  }));

  const detail_key_points: DetailKeyPoint[] = (keyPointsResult.data ?? []).map((row) => ({
    key_point_index: row.key_point_index,
    key_point_text: row.key_point_text,
  }));

  return {
    story_id,
    detail_category: storyResult.data.story_detail_category,
    detail_chunks,
    trust_summary,
    key_figure,
    sources,
    timeline,
    suggested_questions,
    analytic_panels,
    detail_key_points,
  };
}
