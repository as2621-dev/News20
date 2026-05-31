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
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type {
  BiasLean,
  DetailChunk,
  KeyFigure,
  StoryDetail,
  StorySource,
  SuggestedQuestion,
  TimelineEvent,
  TrustSummary,
} from "@/types/detail";

/** `detail_chunks` columns read for the Playfair body. */
const DETAIL_CHUNK_SELECT = "chunk_index,chunk_text";
/** `story_trust` columns read for the COVERAGE strip (1:1 with the story). */
const STORY_TRUST_SELECT =
  "coverage_left_count,coverage_center_count,coverage_right_count," +
  "coverage_outlet_count,blindspot_lean,opposing_view_text";
/** `story_timeline` columns read for the "HOW IT DEVELOPED" drawer. */
const STORY_TIMELINE_SELECT = "timeline_event_index,timeline_when_label,timeline_what_text";
/** `story_sources` columns read for the sources / citation chips. */
const STORY_SOURCE_SELECT =
  "source_outlet_name,source_bias_lean,source_article_url,source_published_utc,source_is_citation";
/** `suggested_questions` columns read for the Q&A chips. */
const SUGGESTED_QUESTION_SELECT = "question_index,question_text";
/** `stories` key-figure columns for the Detail key-figure card. */
const STORY_KEY_FIGURE_SELECT = "story_key_figure_value,story_key_figure_label";

/** Raw `detail_chunks` row. */
interface DetailChunkRow {
  chunk_index: number;
  chunk_text: string;
}

/** Raw `story_trust` row (1:1; nullable blindspot/opposing-view). */
interface StoryTrustRow {
  coverage_left_count: number;
  coverage_center_count: number;
  coverage_right_count: number;
  coverage_outlet_count: number;
  blindspot_lean: BiasLean | null;
  opposing_view_text: string | null;
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

/** Raw `stories` key-figure projection. */
interface StoryKeyFigureRow {
  story_key_figure_value: string | null;
  story_key_figure_label: string | null;
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
  const [chunksResult, trustResult, timelineResult, sourcesResult, questionsResult, storyResult] = await Promise.all([
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

  return {
    story_id,
    detail_chunks,
    trust_summary,
    key_figure,
    sources,
    timeline,
    suggested_questions,
  };
}
