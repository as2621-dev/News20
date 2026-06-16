/**
 * Cross-phase feed contract for the audio-first karaoke reel (blip / "News20").
 *
 * **Why this file is the seam.** Phase 1 renders these shapes from bundled M0
 * fixtures (`src/lib/feed/fixtureFeed.ts`); Phase 3 swaps only the feed-provider
 * implementation for a Supabase fetch. Everything downstream — the reel UI, the
 * karaoke selector — depends on THIS contract, not on the fixture loader. So the
 * field names here are deliberately aligned to the production Postgres schema
 * (`reference/supabase-schema.md`): a `Story` ≈ a `stories` row joined to its
 * current `digests` row + `segments` accent; a `CaptionSentence` ≈ a
 * `caption_sentences` row; a `WordToken` ≈ one element of that row's
 * `word_tokens` JSONB array. Verbose, entity-prefixed names per CLAUDE.md.
 *
 * Field provenance (column → field):
 * - `caption_sentences.word_tokens[]` → {@link WordToken}
 * - `caption_sentences.{sentence_index, anchor_speaker, sentence_text,
 *    highlight_keyword, sentence_start_ms, sentence_end_ms}` → {@link CaptionSentence}
 * - `stories.{story_id, story_headline}` + `segments.{segment_slug,
 *    segment_label, segment_accent_hex}` + `digests.{digest_audio_url,
 *    digest_duration_ms}` + the ambient poster → {@link Story}
 */

/**
 * The two AI anchor voices. Mirrors `data.js` `story.anchors[]` and the
 * `anchor_speaker` Postgres enum (`reference/supabase-schema.md` §1). The reel
 * alternates the speaker label per caption sentence; identity colours
 * (ALEX `#6C8CFF` / JORDAN `#C792EA`) are applied by the UI in Sub-phase 3.
 */
export type AnchorSpeaker = "ALEX" | "JORDAN";

/**
 * The five fixed editorial segment slugs. Mirrors the `segment_slug` Postgres
 * enum and `data.js` `SEGMENTS` keys exactly.
 */
export type SegmentKey = "geopolitics" | "markets" | "tech" | "sport" | "wildcard";

/**
 * One karaoke word token — the atom the caption renderer lights word-by-word.
 *
 * Maps to one element of `caption_sentences.word_tokens` JSONB
 * (`reference/supabase-schema.md:152`, the authoritative shape). `start_ms` /
 * `end_ms` are relative to the digest audio clock; the current word is the token
 * whose `[start_ms, end_ms)` half-open interval contains the audio's
 * `currentTime` in ms.
 *
 * @example
 * { word_text: "target", is_highlight: true, start_ms: 2007, end_ms: 2509 }
 */
export interface WordToken {
  /** The verbatim word text, punctuation attached as spoken (e.g. `"close."`). */
  word_text: string;
  /** True for the single yellow (`#FACC15`) keyword in the sentence. */
  is_highlight: boolean;
  /** Word start, ms, relative to the digest audio clock. */
  start_ms: number;
  /** Word end, ms, relative to the digest audio clock (half-open upper bound). */
  end_ms: number;
}

/**
 * One karaoke caption sentence — a `caption_sentences` row.
 *
 * Exactly one of `word_tokens` has `is_highlight === true`, and its `word_text`
 * equals {@link highlight_keyword}. Sentences are ordered by `sentence_index`
 * (0-based, contiguous) and their `[sentence_start_ms, sentence_end_ms)` windows
 * tile the spoken audio in order.
 */
export interface CaptionSentence {
  /** 0-based order within the digest (contiguous, monotonic). */
  sentence_index: number;
  /** Which anchor voice speaks this sentence (`story.anchors[sentence_index % 2]`). */
  anchor_speaker: AnchorSpeaker;
  /** Full plaintext sentence (word_texts joined by spaces). */
  sentence_text: string;
  /** The single `#FACC15` keyword for this sentence (== the highlight token's `word_text`). */
  highlight_keyword: string;
  /** Sentence start, ms (first word's `start_ms`). */
  sentence_start_ms: number;
  /** Sentence end, ms (last word's `end_ms`, half-open upper bound). */
  sentence_end_ms: number;
  /** The karaoke word tokens, in spoken order (count + order preserved verbatim from M0). */
  word_tokens: WordToken[];
}

/**
 * The generated audio digest + its caption track — a `digests` row plus its
 * `caption_sentences`. Audio-first: this is audio + a word-timed caption track,
 * **not** an MP4 (`reference/supabase-schema.md` §digests).
 */
export interface Digest {
  /** Digest identifier (`digests.digest_id`; M0 fixture: `"digest-1"`...). */
  digest_id: string;
  /** Storage URL of the anchor-duo TTS narration (`digests.digest_audio_url`). */
  digest_audio_url: string;
  /** Total audio length, ms (`digests.digest_duration_ms`) — drives the progress bar. */
  audio_duration_ms: number;
  /**
   * When narration ends, ms. Usually equals {@link audio_duration_ms}; can be
   * shorter when the audio carries trailing ambience (M0 digest-2). No word is
   * `active` at or past this time — the karaoke goes fully spoken.
   */
  speech_end_ms: number;
  /** The word-timed karaoke caption track, ordered by `sentence_index`. */
  caption_sentences: CaptionSentence[];
}

/**
 * Day-one / partial-feed metadata for the reel (Phase 7b SP3).
 *
 * `getReelFeed` returns this alongside the resolved {@link Story}[] so the reel can
 * decide whether to show the "Showing you the past 24 hours — n/30" first-run
 * banner. `allocated_count` is the number of rows actually resolved (not a stored
 * count); `is_partial` is derived from it vs {@link feed_total}; `is_first_run`
 * comes from SP2's per-date `localStorage` flag (`firstRunFlagKey(feed_date)`).
 */
export interface ReelFeedMeta {
  /** Number of stories actually resolved for the feed (the live row count). */
  allocated_count: number;
  /** The finite-briefing target (`FEED_TOTAL`, currently 30). */
  feed_total: number;
  /** True when fewer than `feed_total` stories resolved (`allocated_count < feed_total`). */
  is_partial: boolean;
  /** True when this is the user's day-one first-run feed for the shown `feed_date`. */
  is_first_run: boolean;
}

/**
 * The reel feed plus its {@link ReelFeedMeta} — the return shape of
 * `getReelFeed` (Phase 7b SP3). Bundling rows + meta keeps the single seam the
 * reel imports while exposing the partial/first-run signals the banner needs.
 */
export interface ReelFeedResult {
  /** The stories to play, in feed order. */
  stories: Story[];
  /** Partial / first-run metadata for the day-one banner. */
  meta: ReelFeedMeta;
}

/**
 * One reel story in canonical shape — a `stories` row joined to its current
 * `digests` row and its `segments` accent. This is what `getFeed()` returns and
 * what the reel UI renders.
 */
export interface Story {
  /** Story identifier (`stories.story_id`; prototype slugs `"s1"`...`"s5"`). */
  digest_id: string;
  /** The story headline (`stories.story_headline`). */
  headline: string;
  /** Segment slug (`stories.story_segment_slug`). */
  segment_key: SegmentKey;
  /**
   * The story's Detail-page category (`stories.story_detail_category`; one of the 9
   * {@link import("@/lib/detailTemplates").DetailCategory} buckets), or `null` for a
   * pre-migration story. Drives which panel template the Detail page renders. The
   * Detail page reads `DETAIL_TEMPLATES[story_detail_category]` (`detailTemplates.ts`).
   */
  story_detail_category: string | null;
  /** Human-readable segment label, e.g. `"Geopolitics"` (`segments.segment_label`). */
  segment_label: string;
  /** Per-story accent hex, e.g. `"#EF4444"` (`segments.segment_accent_hex`) → `--accent`. */
  segment_accent_hex: string;
  /** The two anchor voices for this story, in order (`stories`→`data.js` `anchors[]`). */
  anchors: [AnchorSpeaker, AnchorSpeaker];
  /** Storage URL of the anchor-duo TTS narration (`digests.digest_audio_url`). */
  digest_audio_url: string;
  /** Total audio length, ms (`digests.digest_duration_ms`). */
  audio_duration_ms: number;
  /** When narration ends, ms (≤ {@link audio_duration_ms}; see {@link Digest.speech_end_ms}). */
  speech_end_ms: number;
  /** Ambient drifting poster wash source (`digests.digest_ambient_poster_url`). */
  poster_url: string;
  /** The word-timed karaoke caption track, ordered by `sentence_index`. */
  caption_sentences: CaptionSentence[];
}
