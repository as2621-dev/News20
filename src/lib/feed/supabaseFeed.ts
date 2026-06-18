/**
 * Supabase-backed feed source (Phase 1b SP4) for the audio-first karaoke reel.
 *
 * The drop-in sibling of `fixtureFeed.ts`: identical `getFeed(): Promise<Story[]>`
 * contract (`src/types/feed.ts`), different data source. Queries
 * `stories` ⋈ `segments` ⋈ current `digests` ⋈ `caption_sentences` in a single
 * PostgREST round-trip and maps rows into the canonical {@link Story} shape, so
 * Phase 1c swaps the fixture provider for this one with zero reel changes.
 *
 * Contract reconciliation (Rule 7, phase Open Q3):
 * - `src/types/feed.ts` is authoritative (the already-shipped Phase 1 seam) and
 *   agrees with `reference/supabase-schema.md` on every stored column.
 * - `reference/api-contracts.md` describes a different, older `Story` (mp4 + bias);
 *   it is superseded by `src/types/feed.ts` for the reel. Chosen, not blended.
 * - Two `Story` fields have NO direct column and are DERIVED exactly as
 *   `fixtureFeed.ts` derives them: `anchors` (from the caption speakers, in
 *   `sentence_index` order) and `speech_end_ms` (the last caption's
 *   `sentence_end_ms` — the moment narration ends). Documented, not drift.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { FEED_TOTAL } from "@/lib/reel/feedBriefing";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { AnchorSpeaker, CaptionSentence, SegmentKey, Story, WordToken } from "@/types/feed";

/**
 * PostgREST embedded select: a story with its segment accent, its current digest,
 * and that digest's caption sentences (with the word_tokens JSONB array).
 */
const FEED_SELECT =
  "story_id,story_headline,story_segment_slug,story_detail_category," +
  "segments(segment_label,segment_accent_hex)," +
  "digests!inner(digest_id,digest_audio_url,digest_duration_ms,digest_ambient_poster_url,digest_is_current," +
  "caption_sentences(sentence_index,anchor_speaker,sentence_text,highlight_keyword,sentence_start_ms,sentence_end_ms,word_tokens))";

/** Segment sub-row. PostgREST returns a many-to-one embed as a single object. */
interface SegmentRow {
  segment_label: string;
  segment_accent_hex: string;
}

/** Caption sub-row as stored; `word_tokens` is the JSONB array. */
interface CaptionRow {
  sentence_index: number;
  anchor_speaker: AnchorSpeaker;
  sentence_text: string;
  highlight_keyword: string;
  sentence_start_ms: number;
  sentence_end_ms: number;
  word_tokens: WordToken[];
}

/** Digest sub-row with embedded caption sentences. */
interface DigestRow {
  digest_id: string;
  digest_audio_url: string;
  digest_duration_ms: number;
  digest_ambient_poster_url: string | null;
  digest_is_current: boolean;
  caption_sentences: CaptionRow[];
}

/**
 * One story row with embedded segment + current digest(s).
 *
 * `segments` is a MANY-TO-ONE embed: PostgREST returns it as a single object
 * (the `stories.story_segment_slug` FK resolves to exactly one `segments` row).
 * Older code expected an array; we accept either to stay robust to PostgREST
 * relationship detection and to the array form the offline test mock supplies.
 * `digests` is a ONE-TO-MANY embed and is always an array.
 */
interface StoryRow {
  story_id: string;
  story_headline: string;
  story_segment_slug: SegmentKey;
  story_detail_category: string | null;
  segments: SegmentRow | SegmentRow[] | null;
  digests: DigestRow[];
}

/**
 * Derive the ordered anchor pair from the caption sentences.
 *
 * The reel alternates `anchors[sentence_index % 2]`, so sentence 0 gives
 * `anchors[0]` and the first sentence whose speaker differs gives `anchors[1]`.
 */
function deriveAnchors(captions: CaptionSentence[]): [AnchorSpeaker, AnchorSpeaker] {
  const first = captions[0]?.anchor_speaker ?? "ALEX";
  const second =
    captions.find((caption) => caption.anchor_speaker !== first)?.anchor_speaker ??
    (first === "ALEX" ? "JORDAN" : "ALEX");
  return [first, second];
}

/**
 * Map a DB story row into the canonical {@link Story} shape.
 *
 * @throws If the story is missing its current digest or its segment.
 */
function mapStoryRow(row: StoryRow): Story {
  // PostgREST returns a many-to-one embed as an object; the offline test mock
  // supplies an array. Normalize both to the single related segment row.
  const segment = Array.isArray(row.segments) ? row.segments[0] : row.segments;
  // digests!inner already filters to the current digest, but be explicit.
  const digest = row.digests.find((candidate) => candidate.digest_is_current) ?? row.digests[0];
  if (!segment) {
    throw new Error(`Story "${row.story_id}" is missing its segment. fix_suggestion: re-run the seed.`);
  }
  if (!digest) {
    throw new Error(`Story "${row.story_id}" has no current digest. fix_suggestion: re-run the seed.`);
  }

  const captionSentences: CaptionSentence[] = [...digest.caption_sentences]
    .sort((a, b) => a.sentence_index - b.sentence_index)
    .map((caption) => ({
      sentence_index: caption.sentence_index,
      anchor_speaker: caption.anchor_speaker,
      sentence_text: caption.sentence_text,
      highlight_keyword: caption.highlight_keyword,
      sentence_start_ms: caption.sentence_start_ms,
      sentence_end_ms: caption.sentence_end_ms,
      word_tokens: caption.word_tokens,
    }));

  const lastSentence = captionSentences[captionSentences.length - 1];
  const speechEndMs = lastSentence ? lastSentence.sentence_end_ms : digest.digest_duration_ms;

  return {
    digest_id: row.story_id,
    headline: row.story_headline,
    segment_key: row.story_segment_slug,
    story_detail_category: row.story_detail_category,
    segment_label: segment.segment_label,
    segment_accent_hex: segment.segment_accent_hex,
    anchors: deriveAnchors(captionSentences),
    digest_audio_url: digest.digest_audio_url,
    audio_duration_ms: digest.digest_duration_ms,
    speech_end_ms: speechEndMs,
    poster_url: digest.digest_ambient_poster_url ?? "",
    caption_sentences: captionSentences,
  };
}

/**
 * Fetch the reel feed from Supabase as canonical {@link Story}[].
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the
 *   shared browser anon client.
 * @returns The stories, ordered by `story_id` (M0 build order `s1`...`s5`).
 * @throws If the query fails or a story is missing its current digest/segment.
 *
 * @example
 * const feed = await getFeed();
 * feed[0].caption_sentences[0].word_tokens[0].word_text;
 */
export async function getFeed(client: SupabaseClient = getSupabaseBrowserClient()): Promise<Story[]> {
  const { data, error } = await client
    .from("stories")
    .select(FEED_SELECT)
    .order("story_id", { ascending: true })
    .returns<StoryRow[]>();

  if (error) {
    throw new Error(
      `Failed to load feed from Supabase: ${error.message}. ` +
        "fix_suggestion: confirm migrations applied, RLS allows anon SELECT, and the seed ran.",
    );
  }

  // Reason: the briefing is finite by contract — never surface more than the
  // 30-story cap even if the table carries extra rows.
  return (data ?? []).slice(0, FEED_TOTAL).map(mapStoryRow);
}

/** A `daily_feeds` row with its embedded story (the same {@link StoryRow} shape). */
interface DailyFeedRow {
  feed_position: number;
  /** The slot tier that placed this story: `source` (followed source) or `interest`
   *  (category slot). phase-SP1 removed the `breaking` tier. */
  feed_slot_kind: string | null;
  stories: StoryRow | StoryRow[];
}

/**
 * Fetch a single user's personalized feed for one day (Phase 4b SP1).
 *
 * Reads `daily_feeds` (the per-user, ranked + allocated ~30-slot window written
 * by the daily pipeline) for `(feed_user_id, feed_date)`, embedding each slot's
 * story with the SAME `stories ⋈ segments ⋈ digests ⋈ captions` shape `getFeed`
 * uses, ordered by `feed_position`. Returns `[]` when the user has no rows for
 * that date (the selector then falls back to the global seeded feed) — the RLS
 * `daily_feeds_owner_select` policy scopes every row to the authed user.
 *
 * @param userId - The authed `users.user_id` (= `auth.uid()`).
 * @param feedDate - The `feed_date` to read (ISO `YYYY-MM-DD`).
 * @param client - Optional Supabase client (injected in tests).
 * @returns The user's stories ordered by `feed_position` ( `[]` when unallocated).
 * @throws If the query itself fails (not when it is merely empty).
 *
 * @example
 * const feed = await getDailyFeed(session.user.id, "2026-06-06");
 */
export async function getDailyFeed(
  userId: string,
  feedDate: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<Story[]> {
  const { data, error } = await client
    .from("daily_feeds")
    .select(`feed_position,feed_slot_kind,stories!inner(${FEED_SELECT})`)
    .eq("feed_user_id", userId)
    .eq("feed_date", feedDate)
    .order("feed_position", { ascending: true })
    .returns<DailyFeedRow[]>();

  if (error) {
    throw new Error(
      `Failed to load daily feed from Supabase: ${error.message}. ` +
        "fix_suggestion: confirm daily_feeds is populated and RLS allows the authed SELECT.",
    );
  }

  // Reason: same finite-briefing cap as getFeed — the allocator writes ~30
  // slots, but the UI contract is AT MOST 30 stories.
  return (data ?? []).slice(0, FEED_TOTAL).map((row) => ({
    ...mapStoryRow(Array.isArray(row.stories) ? row.stories[0] : row.stories),
    // Reason: carry the slot tier so the reel chip can mark a followed-source slot;
    // a "source" row is a source slot, any other value (incl. null / a legacy
    // "breaking" enum row) is a normal interest slot (phase-SP1 removed breaking).
    feed_slot_kind: row.feed_slot_kind === "source" ? "source" : "interest",
  }));
}
