/**
 * Pure karaoke selector: given the caption track and the audio clock, decide
 * the current sentence + each word's visual state. The reel UI (Sub-phase 3)
 * samples `audioRef.current.currentTime * 1000` each animation frame and renders
 * from this — no timing logic lives in the component.
 *
 * ## Visual-state model (the deliberate design choice — Rule 1)
 *
 * The phase file asked for a single per-word state
 * `'dim' | 'spoken' | 'active' | 'highlight'`, but the ported CSS
 * (`src/app/globals.css`) proves highlight is **not** mutually exclusive with the
 * others: `.caption .w.hl` is yellow regardless of spoken/active, and
 * `.caption .w.hl.active` coexists (the current word stays `.active` even when it
 * is also the keyword). Collapsing them into one enum would lose the fact that a
 * token can be **both** the active word **and** the highlight keyword.
 *
 * So this selector returns **two orthogonal axes per word**:
 *   - `timing`: `'dim' | 'spoken' | 'active'` — the karaoke progress state.
 *   - `is_highlight`: boolean — the one yellow keyword, independent of timing.
 *
 * Sub-phase 3 then maps each token to CSS classes byte-compatibly with the
 * prototype: always `.w`; add `.spoken` or `.active` from `timing`; add `.hl`
 * when `is_highlight`. (`.w.hl.active` falls out naturally.) A convenience
 * {@link WordVisualState.css_class_names} string is also provided so the renderer
 * can spread it directly.
 *
 * ## Hard invariants (encoded in the tests, `tests/lib/captionState.test.ts`)
 * Let `t = current_time_ms`:
 * - (a) The word whose `[start_ms, end_ms)` contains `t` is `active`.
 * - (b) Words ending at or before `t` (in the current or earlier sentences) are
 *   `spoken`; words starting after `t` are `dim`. Inter-word gaps resolve to the
 *   already-passed (`spoken`) side, never to `active`.
 * - (c) No word is `active` once `t >= speech_end_ms` — the track goes fully
 *   spoken (handles M0 digest-2's trailing ambience past `speech_end_s`).
 * - (d) Exactly one `is_highlight` token per sentence (preserved from the track).
 * - (e) The current sentence is the {@link CaptionSentence} whose
 *   `[sentence_start_ms, sentence_end_ms)` contains `t`; in the trailing gap
 *   after the last sentence the last sentence stays current so the label holds.
 */
import type { AnchorSpeaker, CaptionSentence } from "@/types/feed";

/** Karaoke progress state for a single word (orthogonal to highlight). */
export type WordTimingState = "dim" | "spoken" | "active";

/** The resolved visual state of one rendered word. */
export interface WordVisualState {
  /** Verbatim word text (passes through from the token). */
  word_text: string;
  /** Karaoke progress: not-yet (`dim`), already-said (`spoken`), or current (`active`). */
  timing: WordTimingState;
  /** True for the sentence's single `#FACC15` keyword — independent of `timing`. */
  is_highlight: boolean;
  /**
   * Space-joined CSS classes byte-compatible with the prototype caption markup:
   * `"w"`, plus `"spoken"`/`"active"` from {@link timing}, plus `"hl"` when
   * {@link is_highlight}. Spread onto the word span's `className` in Sub-phase 3.
   */
  css_class_names: string;
}

/** The full karaoke state at one instant of the audio clock. */
export interface CaptionStateAtTime {
  /**
   * Index into the passed `captionSentences` of the current sentence, or `-1`
   * before the first sentence starts. Lets Sub-phase 3 render the speaker label
   * without re-deriving.
   */
  current_sentence_index: number;
  /** The current sentence object, or `null` before the first sentence starts. */
  current_sentence: CaptionSentence | null;
  /**
   * The current sentence's speaker, or `null` before the first sentence starts.
   * Sub-phase 3 maps this to the fixed identity colour (ALEX `#6C8CFF`,
   * JORDAN `#C792EA`).
   */
  current_speaker: AnchorSpeaker | null;
  /**
   * Per-word visual state for the current sentence (empty before the first
   * sentence). Only the current sentence is rendered, matching the prototype's
   * one-sentence-at-a-time karaoke; words in it carry `dim`/`spoken`/`active`.
   */
  words: WordVisualState[];
}

/** Build the CSS class string for a word from its two visual axes. */
function buildCssClassNames(timing: WordTimingState, isHighlight: boolean): string {
  const classNames = ["w"];
  if (timing === "spoken") {
    classNames.push("spoken");
  } else if (timing === "active") {
    classNames.push("active");
  }
  if (isHighlight) {
    classNames.push("hl");
  }
  return classNames.join(" ");
}

/**
 * Find the current sentence index for time `t`.
 *
 * Returns the sentence whose `[sentence_start_ms, sentence_end_ms)` contains `t`.
 * Before the first sentence → `-1`. In a gap between/after sentences, the most
 * recent sentence that has started stays current (so the speaker label persists
 * through silences and the trailing tail).
 */
function findCurrentSentenceIndex(captionSentences: CaptionSentence[], currentTimeMs: number): number {
  if (captionSentences.length === 0 || currentTimeMs < captionSentences[0].sentence_start_ms) {
    return -1;
  }
  // Reason: linear scan is fine (sentences ≤ ~11). Pick the last sentence whose
  // start is at or before t; that is the half-open owner, and also the right
  // "sticky" answer for inter-sentence gaps and the post-speech tail.
  let currentIndex = 0;
  for (let index = 0; index < captionSentences.length; index += 1) {
    if (captionSentences[index].sentence_start_ms <= currentTimeMs) {
      currentIndex = index;
    } else {
      break;
    }
  }
  return currentIndex;
}

/**
 * Resolve the karaoke state at a given moment of the audio clock.
 *
 * @param captionSentences - The digest's caption track, ordered by `sentence_index`.
 * @param currentTimeMs - The audio's `currentTime` in ms (`audio.currentTime * 1000`).
 * @param speechEndMs - When narration ends, ms (`Story.speech_end_ms`). At or past
 *   this time no word is `active` (invariant c) — the track is fully spoken. Pass
 *   the story's `speech_end_ms`, which can be earlier than the audio duration.
 * @returns The current sentence, its speaker, and per-word visual states.
 *
 * @example
 * // digest-1, t = 2200ms is inside "target" [2007,2509) → that word is active + highlight.
 * const state = captionStateAtTime(sentences, 2200, 50611);
 * state.current_sentence_index;                       // 0
 * state.current_speaker;                              // "ALEX"
 * const target = state.words.find(w => w.word_text === "target");
 * target?.timing;        // "active"
 * target?.is_highlight;  // true
 * target?.css_class_names; // "w active hl"
 */
export function captionStateAtTime(
  captionSentences: CaptionSentence[],
  currentTimeMs: number,
  speechEndMs: number,
): CaptionStateAtTime {
  const currentSentenceIndex = findCurrentSentenceIndex(captionSentences, currentTimeMs);

  if (currentSentenceIndex === -1) {
    return {
      current_sentence_index: -1,
      current_sentence: null,
      current_speaker: null,
      words: [],
    };
  }

  const currentSentence = captionSentences[currentSentenceIndex];

  // Reason (invariant c): once narration has ended, every word is `spoken` —
  // never `active`. This also covers the trailing-ambience tail (digest-2),
  // where the audio keeps running past speech_end_ms with no spoken word.
  const isFullySpoken = currentTimeMs >= speechEndMs;

  const words: WordVisualState[] = currentSentence.word_tokens.map((token) => {
    let timing: WordTimingState;
    if (isFullySpoken) {
      timing = "spoken";
    } else if (currentTimeMs < token.start_ms) {
      // Not reached yet.
      timing = "dim";
    } else if (currentTimeMs < token.end_ms) {
      // [start_ms, end_ms) contains t → this is the current word (invariant a).
      timing = "active";
    } else {
      // Ended at or before t → already said. Inter-word gaps land here (invariant b).
      timing = "spoken";
    }
    return {
      word_text: token.word_text,
      timing,
      is_highlight: token.is_highlight,
      css_class_names: buildCssClassNames(timing, token.is_highlight),
    };
  });

  return {
    current_sentence_index: currentSentenceIndex,
    current_sentence: currentSentence,
    current_speaker: currentSentence.anchor_speaker,
    words,
  };
}
