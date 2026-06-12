"use client";

/**
 * KaraokeCaption — the hero of the reel. Renders the CURRENT caption sentence's
 * words lit word-by-word against the real audio clock.
 *
 * **No timing logic lives here.** It receives the already-sampled
 * `current_time_ms` (from {@link useReelAudio}'s rAF sampler of
 * `audio.currentTime`, port-map §3.1) and the story's caption track + speech end,
 * and delegates the entire visual-state decision to the pure
 * {@link captionStateAtTime} selector (SP2). Each word's resolved
 * `css_class_names` (`"w"` + `spoken`/`active` + `hl`) is spread directly onto
 * its span — byte-compatible with the ported `.caption .w.*` CSS in globals.css,
 * so the one `#FACC15` keyword/sentence and the white current word fall out for
 * free. We do NOT restyle captions here.
 *
 * Before the first sentence starts (`current_speaker === null`) nothing is
 * rendered (the prototype shows an empty caption until playback reaches the first
 * word). The `.caption` class supplies Playfair / `font-serif`.
 */
import { useMemo } from "react";
import { captionStateAtTime } from "@/lib/captions/captionState";
import type { CaptionSentence } from "@/types/feed";

export interface KaraokeCaptionProps {
  /** This story's caption track, ordered by `sentence_index` (from `Story.caption_sentences`). */
  captionSentences: CaptionSentence[];
  /** The sampled audio position in ms (`audio.currentTime * 1000`). */
  currentTimeMs: number;
  /**
   * When narration ends, ms (`Story.speech_end_ms` — NOT `audio_duration_ms`;
   * they differ for digest-2). At/after this the track goes fully spoken
   * (no `active` word). See {@link captionStateAtTime} invariant (c).
   */
  speechEndMs: number;
  /**
   * When true (`prefers-reduced-motion`), the per-word colour CSS transition is
   * suppressed via the `reduced-motion` class. globals.css already kills
   * `.caption .w` transitions under the media query; this adds a belt-and-braces
   * hook for when the OS query is mocked off but the user setting is on.
   */
  reduceMotion?: boolean;
}

/**
 * Render the current karaoke sentence. Re-derives the per-word state whenever
 * the sampled clock advances (cheap: one selector call over ≤ ~20 words).
 */
export function KaraokeCaption({
  captionSentences,
  currentTimeMs,
  speechEndMs,
  reduceMotion = false,
}: KaraokeCaptionProps) {
  // Reason: memoize on the clock + track so we only recompute the selector when
  // the sampled time (or the story) changes, not on unrelated parent re-renders.
  const captionState = useMemo(
    () => captionStateAtTime(captionSentences, currentTimeMs, speechEndMs),
    [captionSentences, currentTimeMs, speechEndMs],
  );

  // Before the first sentence: render the empty caption shell (keeps layout
  // stable, mirrors the prototype's blank caption pre-first-word).
  if (captionState.current_speaker === null) {
    return <div className={`caption text-center w-full${reduceMotion ? " reduced-motion" : ""}`} aria-hidden="true" />;
  }

  return (
    <div className={`caption text-center w-full${reduceMotion ? " reduced-motion" : ""}`}>
      {/* Reason: announce the full current sentence to screen readers ONCE, rather
          than 20 individually-classed word spans flickering per audio frame. The
          visible word spans below are aria-hidden so the per-frame class churn is
          never read out. */}
      <span className="sr-only">{captionState.current_sentence?.sentence_text}</span>
      <span aria-hidden="true">
        {captionState.words.map((word, wordIndex) => (
          // Spread the selector's css_class_names verbatim (the visual contract).
          // biome-ignore lint/suspicious/noArrayIndexKey: word order is stable within a sentence and there is no stable id; index is the correct key here.
          <span key={wordIndex}>
            <span className={word.css_class_names}>{word.word_text}</span>
            {/* Reason: the word span is display:inline-block (for the scale pop),
                and trailing whitespace INSIDE an inline-block is trimmed — the
                separating space must live OUTSIDE it to render. */}
            {wordIndex < captionState.words.length - 1 ? " " : null}
          </span>
        ))}
      </span>
    </div>
  );
}
