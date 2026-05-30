/**
 * Normalize the M0 caption JSON on-disk shape into the canonical
 * {@link CaptionSentence}[] feed contract.
 *
 * **The shape gap this bridges.** M0 (`agents/m0/output/captions/digest-N.captions.json`)
 * stores a FLAT `words[]` array with `sentence_index` on each word and times in
 * SECONDS. The reel contract (`reference/supabase-schema.md`'s `caption_sentences`)
 * wants per-sentence rows with a nested `word_tokens[]` array and times in MS.
 * This pure function groups the flat words by `sentence_index`, converts
 * seconds→ms (`Math.round(s * 1000)`), and derives each sentence's scalar fields.
 *
 * **Invariants preserved (verified across all 5 M0 files):** the verbatim word
 * sequence (count + order) is preserved 1:1 — `word_tokens[i].word_text` is the
 * raw `words[].word`, never re-tokenized; exactly one `is_highlight` word per
 * sentence; `sentence_index` is contiguous `0..sentence_count-1`. The data is
 * known-clean, so a sentence without exactly one highlight is LOGGED as a warning
 * (Rule 12 — surface it) but not thrown — normalization still proceeds.
 */
import { logger } from "@/lib/logger";
import type { AnchorSpeaker, CaptionSentence, WordToken } from "@/types/feed";

/** One word in the M0 caption JSON `words[]` array (on-disk shape, times in SECONDS). */
export interface M0CaptionWord {
  /** Verbatim word text, punctuation attached (e.g. `"close."`). */
  word: string;
  /** Word start, seconds. */
  start_s: number;
  /** Word end, seconds. */
  end_s: number;
  /** 0-based sentence this word belongs to. */
  sentence_index: number;
  /** True for the single highlight keyword in its sentence. */
  is_highlight: boolean;
}

/**
 * The full M0 caption JSON file shape
 * (`agents/m0/output/captions/digest-N.captions.json`).
 */
export interface M0CaptionTrack {
  /** Digest identifier, e.g. `"digest-1"`. */
  digest_id: string;
  /** Total audio length, seconds. */
  audio_duration_s: number;
  /** When narration ends, seconds (≤ `audio_duration_s`). */
  speech_end_s: number;
  /** Number of sentences (== distinct `sentence_index` count). */
  sentence_count: number;
  /** Flat, spoken-order word list. */
  words: M0CaptionWord[];
}

/** Convert a seconds timestamp to integer milliseconds. */
function secondsToMs(seconds: number): number {
  return Math.round(seconds * 1000);
}

/**
 * Convert an M0 caption track + the story's anchor pair into canonical caption
 * sentences.
 *
 * @param m0Track - The raw M0 caption JSON (typed to its on-disk shape).
 * @param anchors - The story's two anchor voices, in order; `anchors[i % 2]`
 *   gives sentence `i`'s speaker. Per-sentence speaker is NOT in the M0 JSON, so
 *   this is the documented alternation approximation (phase Open Q1).
 * @returns The caption sentences, ordered by `sentence_index`, ready for the
 *   karaoke selector and the reel UI.
 *
 * @example
 * const sentences = normalizeM0Captions(track, ["ALEX", "JORDAN"]);
 * sentences[0].word_tokens[0].word_text; // "The"  (verbatim, count + order preserved)
 * sentences[0].sentence_start_ms;        // 0
 */
export function normalizeM0Captions(
  m0Track: M0CaptionTrack,
  anchors: readonly [AnchorSpeaker, AnchorSpeaker],
): CaptionSentence[] {
  logger.info("normalize_m0_captions_started", {
    digest_id: m0Track.digest_id,
    sentence_count: m0Track.sentence_count,
    word_count: m0Track.words.length,
  });

  // Reason: group flat words by sentence_index while preserving spoken order.
  // A Map keyed by index keeps insertion order; M0 words are already monotonic
  // non-decreasing in sentence_index, but grouping is order-independent per
  // sentence because we push in array order.
  const wordsBySentenceIndex = new Map<number, M0CaptionWord[]>();
  for (const m0Word of m0Track.words) {
    const bucket = wordsBySentenceIndex.get(m0Word.sentence_index);
    if (bucket) {
      bucket.push(m0Word);
    } else {
      wordsBySentenceIndex.set(m0Word.sentence_index, [m0Word]);
    }
  }

  const sortedSentenceIndices = [...wordsBySentenceIndex.keys()].sort((a, b) => a - b);

  const captionSentences: CaptionSentence[] = sortedSentenceIndices.map((sentenceIndex) => {
    // Non-null: every key in the map came from a pushed word, so the bucket exists.
    const sentenceWords = wordsBySentenceIndex.get(sentenceIndex) as M0CaptionWord[];

    const wordTokens: WordToken[] = sentenceWords.map((m0Word) => ({
      word_text: m0Word.word,
      is_highlight: m0Word.is_highlight,
      start_ms: secondsToMs(m0Word.start_s),
      end_ms: secondsToMs(m0Word.end_s),
    }));

    const highlightTokens = wordTokens.filter((token) => token.is_highlight);
    if (highlightTokens.length !== 1) {
      // Rule 12: surface the anomaly instead of silently picking one. Data is
      // known-clean, so this should never fire; if it does, the caption track
      // upstream changed and the one-keyword-per-sentence contract is at risk.
      logger.warn("normalize_m0_captions_highlight_count_unexpected", {
        digest_id: m0Track.digest_id,
        sentence_index: sentenceIndex,
        highlight_count: highlightTokens.length,
        fix_suggestion:
          "Expected exactly one is_highlight word per sentence. Re-check the M0 caption generator's keyword tagging for this digest.",
      });
    }

    // Reason: keep the keyword legible even in the (logged) anomalous case —
    // fall back to "" rather than crashing the reel render.
    const highlightKeyword = highlightTokens[0]?.word_text ?? "";
    const sentenceText = wordTokens.map((token) => token.word_text).join(" ");

    return {
      sentence_index: sentenceIndex,
      anchor_speaker: anchors[sentenceIndex % 2],
      sentence_text: sentenceText,
      highlight_keyword: highlightKeyword,
      sentence_start_ms: wordTokens[0].start_ms,
      sentence_end_ms: wordTokens[wordTokens.length - 1].end_ms,
      word_tokens: wordTokens,
    };
  });

  logger.info("normalize_m0_captions_completed", {
    digest_id: m0Track.digest_id,
    produced_sentence_count: captionSentences.length,
  });

  return captionSentences;
}
