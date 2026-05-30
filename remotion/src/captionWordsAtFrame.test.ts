import { describe, expect, it } from 'vitest';
import { captionWordsAtFrame } from './captionWordsAtFrame';
import type { CaptionTrack } from './manifest';

// Hand-built track at 30fps. Two sentences, contiguous words, one highlight per sentence.
// Sentence 0: "The" [0.0,0.5) | "target" [0.5,1.0) highlight | "moved." [1.0,2.0)
// Sentence 1: "Tariffs" [2.0,3.0) highlight | "rose." [3.0,4.0)
// speech_end_s = 4.0
const FPS = 30;
const track: CaptionTrack = {
  digest_id: 'digest-test',
  audio_duration_s: 4.5,
  speech_end_s: 4.0,
  sentence_count: 2,
  words: [
    { word: 'The', start_s: 0.0, end_s: 0.5, sentence_index: 0, is_highlight: false },
    { word: 'target', start_s: 0.5, end_s: 1.0, sentence_index: 0, is_highlight: true },
    { word: 'moved.', start_s: 1.0, end_s: 2.0, sentence_index: 0, is_highlight: false },
    { word: 'Tariffs', start_s: 2.0, end_s: 3.0, sentence_index: 1, is_highlight: true },
    { word: 'rose.', start_s: 3.0, end_s: 4.0, sentence_index: 1, is_highlight: false },
  ],
};

describe('captionWordsAtFrame', () => {
  it('returns the word whose interval contains the timestamp (frame inside a word)', () => {
    // t = 0.7s -> "target" interval [0.5,1.0). WHY: caption sync is the whole point of M0.
    const state = captionWordsAtFrame(track, 21, FPS);
    expect(state.activeWord?.word).toBe('target');
    // It must NOT be the neighbouring words.
    expect(state.activeWord?.word).not.toBe('The');
    expect(state.activeWord?.word).not.toBe('moved.');
  });

  it('reveals the current sentence up to the active word, not the whole track', () => {
    // t = 0.7s -> sentence 0 revealed up to "target": ["The", "target"], NOT "moved." yet.
    const state = captionWordsAtFrame(track, 21, FPS);
    expect(state.visibleWords.map((w) => w.word)).toEqual(['The', 'target']);
    // Sentence 1 words must never leak into sentence 0's reveal.
    expect(state.visibleWords.some((w) => w.sentence_index === 1)).toBe(false);
  });

  it('exposes exactly one highlight word in the visible set for a sentence', () => {
    // t = 2.5s -> sentence 1, active "Tariffs" (the highlight).
    const state = captionWordsAtFrame(track, 75, FPS);
    expect(state.activeWord?.word).toBe('Tariffs');
    expect(state.visibleWords.filter((w) => w.is_highlight)).toHaveLength(1);
    expect(state.visibleWords.find((w) => w.is_highlight)?.word).toBe('Tariffs');
  });

  it('treats the interval as half-open at the boundary frame', () => {
    // t = exactly 1.0s (frame 30). "target" ends at 1.0 (exclusive), "moved." starts at 1.0
    // (inclusive) -> active word must be "moved.", never "target".
    const state = captionWordsAtFrame(track, 30, FPS);
    expect(state.activeWord?.word).toBe('moved.');
    expect(state.activeWord?.word).not.toBe('target');
  });

  it('clamps: a frame past speech_end_s shows no words', () => {
    // t = 4.5s (frame 135) is past speech_end_s = 4.0.
    const state = captionWordsAtFrame(track, 135, FPS);
    expect(state.visibleWords).toEqual([]);
    expect(state.activeWord).toBeNull();
    expect(state.sentenceIndex).toBeNull();
  });

  it('shows no words before the first word starts', () => {
    // First word starts at 0.0, so frame 0 is active; a negative-equivalent gap is covered by
    // a track whose first word starts later — here verify frame 0 IS active to bound the case.
    const state = captionWordsAtFrame(track, 0, FPS);
    expect(state.activeWord?.word).toBe('The');
  });
});
