import type { CaptionTrack, CaptionWord } from './manifest';

/**
 * The caption state to render at a given frame: the active word plus the words already
 * revealed in the same sentence (word-by-word reveal builds up the current sentence).
 */
export interface CaptionFrameState {
  /** Words to display now — the current sentence revealed up to and including `activeWord`. */
  visibleWords: CaptionWord[];
  /** The word whose `[start_s, end_s)` contains the current timestamp, or `null` if none. */
  activeWord: CaptionWord | null;
  /** Sentence index currently on screen, or `null` when no word is active. */
  sentenceIndex: number | null;
}

/**
 * Pure mapping from a frame to the caption words on screen at that frame.
 *
 * Converts the frame to seconds via `fps`, finds the word whose half-open interval
 * `[start_s, end_s)` contains that timestamp, and returns that word plus the earlier words
 * of the same sentence (so the caption line reveals word-by-word). Past `speech_end_s`
 * — or before the first word — nothing is shown.
 *
 * @param track - The digest caption track (SP2 shape).
 * @param frame - Current frame index (0-based).
 * @param fps - Frames per second used to convert the frame to seconds.
 * @returns The words to render and which one is active.
 *
 * @example
 * captionWordsAtFrame(track, 60, 30).activeWord?.word; // word covering t = 2.0s
 */
export function captionWordsAtFrame(track: CaptionTrack, frame: number, fps: number): CaptionFrameState {
  const empty: CaptionFrameState = { visibleWords: [], activeWord: null, sentenceIndex: null };

  if (fps <= 0) {
    return empty;
  }

  const timestampSeconds = frame / fps;

  // Reason: captions never extend past end-of-speech (manifest invariant).
  if (timestampSeconds >= track.speech_end_s) {
    return empty;
  }

  // Half-open interval [start_s, end_s) so contiguous words never both match a boundary frame.
  const activeWord =
    track.words.find((word) => timestampSeconds >= word.start_s && timestampSeconds < word.end_s) ?? null;

  if (activeWord === null) {
    return empty;
  }

  // Reveal the current sentence up to (and including) the active word.
  const visibleWords = track.words.filter(
    (word) => word.sentence_index === activeWord.sentence_index && word.start_s <= activeWord.start_s,
  );

  return { visibleWords, activeWord, sentenceIndex: activeWord.sentence_index };
}
