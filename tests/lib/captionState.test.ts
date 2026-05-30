import { describe, expect, it } from "vitest";
import { captionStateAtTime } from "@/lib/captions/captionState";
import { type M0CaptionTrack, normalizeM0Captions } from "@/lib/feed/normalizeM0Captions";
import type { CaptionSentence } from "@/types/feed";
// Reason (Rule 9): drive the selector off a REAL M0 caption track, normalized
// the same way the reel does. These tests fail when the *selection logic* is
// wrong (wrong active word, active past speech_end, mis-counted highlight), not
// merely on a type/compile error.
import digest1Raw from "../../agents/m0/output/captions/digest-1.captions.json";
import digest2Raw from "../../agents/m0/output/captions/digest-2.captions.json";

const digest1Track = digest1Raw as M0CaptionTrack;
const digest2Track = digest2Raw as M0CaptionTrack;

const digest1Sentences = normalizeM0Captions(digest1Track, ["ALEX", "JORDAN"]);
const digest1SpeechEndMs = Math.round(digest1Track.speech_end_s * 1000); // 50611
const digest2Sentences = normalizeM0Captions(digest2Track, ["JORDAN", "ALEX"]);
const digest2SpeechEndMs = Math.round(digest2Track.speech_end_s * 1000); // 44930
const digest2AudioDurationMs = Math.round(digest2Track.audio_duration_s * 1000); // 46000

/** Helper: find a rendered word by text in the current sentence's word list. */
function findRenderedWord(words: ReturnType<typeof captionStateAtTime>["words"], wordText: string) {
  return words.find((word) => word.word_text === wordText);
}

describe("captionStateAtTime — invariant (a): the word containing t is active", () => {
  it("marks the word whose [start_ms,end_ms) contains t as active (digest-1 'target' at 2200ms)", () => {
    // "target" spans [2007, 2509)ms in digest-1 sentence 0.
    const state = captionStateAtTime(digest1Sentences, 2200, digest1SpeechEndMs);
    expect(state.current_sentence_index).toBe(0);
    const target = findRenderedWord(state.words, "target");
    expect(target?.timing).toBe("active");
  });

  it("treats the interval as half-open: at t == end_ms the word is already spoken, not active", () => {
    // WHY: [start,end) — exactly at end_ms the word has ended. "The" ends at
    // 287ms; "U.S." starts at 287ms, so at t=287 "U.S." is the active one.
    const state = captionStateAtTime(digest1Sentences, 287, digest1SpeechEndMs);
    expect(findRenderedWord(state.words, "The")?.timing).toBe("spoken");
    expect(findRenderedWord(state.words, "U.S.")?.timing).toBe("active");
  });

  it("at most one word is active at any instant inside the spoken track", () => {
    // WHY: karaoke lights exactly one current word. Sweep the whole track.
    for (let timeMs = 0; timeMs < digest1SpeechEndMs; timeMs += 137) {
      const state = captionStateAtTime(digest1Sentences, timeMs, digest1SpeechEndMs);
      const activeCount = state.words.filter((word) => word.timing === "active").length;
      expect(activeCount).toBeLessThanOrEqual(1);
    }
  });
});

describe("captionStateAtTime — invariant (b): before=spoken, after=dim, gaps=spoken", () => {
  it("marks words ending before t as spoken and words starting after t as dim", () => {
    // At t=2200 (inside "target"), "The" (ended 287) is spoken; "inside"
    // (starts 2509) is dim.
    const state = captionStateAtTime(digest1Sentences, 2200, digest1SpeechEndMs);
    expect(findRenderedWord(state.words, "The")?.timing).toBe("spoken");
    expect(findRenderedWord(state.words, "another")?.timing).toBe("spoken");
    expect(findRenderedWord(state.words, "inside")?.timing).toBe("dim");
    expect(findRenderedWord(state.words, "Iran")?.timing).toBe("dim");
  });

  it("resolves an inter-word gap to spoken (the passed side), never active", () => {
    // WHY: M0 words abut, but if a gap exists between word.end and next.start,
    // a t in that gap must not light a phantom active word. "military" ends at
    // 1147ms, "hit" starts at 1147ms (abutting) — probe just past "military"
    // end but before any later word: at t=1147 "hit" is active and "military"
    // spoken; at a synthetic gap point we still get no active among ended words.
    const state = captionStateAtTime(digest1Sentences, 1147, digest1SpeechEndMs);
    expect(findRenderedWord(state.words, "military")?.timing).toBe("spoken");
    expect(findRenderedWord(state.words, "hit")?.timing).toBe("active");
    // No word can be active whose [start,end) does not contain t.
    for (const word of state.words) {
      if (word.timing === "active") {
        const token = state.current_sentence?.word_tokens.find((t) => t.word_text === word.word_text);
        expect(token).toBeDefined();
        expect(1147).toBeGreaterThanOrEqual(token?.start_ms ?? Number.POSITIVE_INFINITY);
        expect(1147).toBeLessThan(token?.end_ms ?? Number.NEGATIVE_INFINITY);
      }
    }
  });
});

describe("captionStateAtTime — invariant (c): nothing active at or past speech_end_ms", () => {
  it("marks every word spoken at exactly speech_end_ms (digest-1)", () => {
    const state = captionStateAtTime(digest1Sentences, digest1SpeechEndMs, digest1SpeechEndMs);
    expect(state.words.length).toBeGreaterThan(0);
    expect(state.words.every((word) => word.timing === "spoken")).toBe(true);
  });

  it("keeps the track fully spoken through digest-2's trailing ambience (speech_end < duration)", () => {
    // WHY: digest-2 has audio_duration_ms 46000 > speech_end_ms 44930 — the
    // audio runs ~1.07s past the last spoken word. In that tail NO word may be
    // active; the last sentence stays current so the speaker label holds.
    expect(digest2AudioDurationMs).toBeGreaterThan(digest2SpeechEndMs);
    for (let timeMs = digest2SpeechEndMs; timeMs <= digest2AudioDurationMs; timeMs += 100) {
      const state = captionStateAtTime(digest2Sentences, timeMs, digest2SpeechEndMs);
      const activeCount = state.words.filter((word) => word.timing === "active").length;
      expect(activeCount).toBe(0);
    }
  });

  it("never marks any word active at or beyond speech_end across the whole tail (digest-1 + digest-2)", () => {
    for (const [sentences, speechEndMs, ceilingMs] of [
      [digest1Sentences, digest1SpeechEndMs, digest1SpeechEndMs + 3000] as const,
      [digest2Sentences, digest2SpeechEndMs, digest2AudioDurationMs] as const,
    ]) {
      for (let timeMs = speechEndMs; timeMs <= ceilingMs; timeMs += 113) {
        const state = captionStateAtTime(sentences, timeMs, speechEndMs);
        expect(state.words.some((word) => word.timing === "active")).toBe(false);
      }
    }
  });
});

describe("captionStateAtTime — invariant (d): exactly one highlight per current sentence", () => {
  it("exposes exactly one highlight word in every current sentence across the track", () => {
    // Sweep so every sentence is exercised as the current one.
    for (let timeMs = 0; timeMs <= digest1SpeechEndMs; timeMs += 200) {
      const state = captionStateAtTime(digest1Sentences, timeMs, digest1SpeechEndMs);
      const highlightCount = state.words.filter((word) => word.is_highlight).length;
      expect(highlightCount).toBe(1);
    }
  });

  it("keeps highlight independent of timing — a word can be both active and highlight", () => {
    // WHY: the CSS .w.hl.active coexists; collapsing into one enum would lose
    // this. "target" at 2200ms is the active word AND the highlight keyword.
    const state = captionStateAtTime(digest1Sentences, 2200, digest1SpeechEndMs);
    const target = findRenderedWord(state.words, "target");
    expect(target?.timing).toBe("active");
    expect(target?.is_highlight).toBe(true);
    expect(target?.css_class_names).toBe("w active hl");
  });

  it("renders the highlight as .hl even when not the active word", () => {
    // Before "target" is reached (t=100ms), it is dim-but-highlight → "w hl".
    const state = captionStateAtTime(digest1Sentences, 100, digest1SpeechEndMs);
    const target = findRenderedWord(state.words, "target");
    expect(target?.timing).toBe("dim");
    expect(target?.is_highlight).toBe(true);
    expect(target?.css_class_names).toBe("w hl");
  });
});

describe("captionStateAtTime — invariant (e): current sentence + speaker exposure", () => {
  it("returns the sentence whose [start,end) contains t and its alternating speaker", () => {
    // digest-1 sentence 1 spans [9463, 14839)ms; anchors ["ALEX","JORDAN"] →
    // odd sentence index 1 = JORDAN.
    const state = captionStateAtTime(digest1Sentences, 10000, digest1SpeechEndMs);
    expect(state.current_sentence_index).toBe(1);
    expect(state.current_speaker).toBe("JORDAN");
    expect(state.current_sentence?.sentence_index).toBe(1);
  });

  it("returns no current sentence before the first sentence starts", () => {
    // The track's first word starts at 0ms, so test a synthetic earlier track.
    const synthetic: CaptionSentence[] = [
      {
        sentence_index: 0,
        anchor_speaker: "ALEX",
        sentence_text: "Hello world",
        highlight_keyword: "world",
        sentence_start_ms: 500,
        sentence_end_ms: 1500,
        word_tokens: [
          { word_text: "Hello", is_highlight: false, start_ms: 500, end_ms: 1000 },
          { word_text: "world", is_highlight: true, start_ms: 1000, end_ms: 1500 },
        ],
      },
    ];
    const state = captionStateAtTime(synthetic, 0, 1500);
    expect(state.current_sentence_index).toBe(-1);
    expect(state.current_sentence).toBeNull();
    expect(state.current_speaker).toBeNull();
    expect(state.words).toHaveLength(0);
  });

  it("keeps the last sentence current in the post-speech tail so the label persists", () => {
    // digest-2 has a trailing tail; at the final ms the last sentence is still
    // the current one (speaker label must not blank out).
    const lastIndex = digest2Sentences.length - 1;
    const state = captionStateAtTime(digest2Sentences, digest2AudioDurationMs, digest2SpeechEndMs);
    expect(state.current_sentence_index).toBe(lastIndex);
    expect(state.current_speaker).toBe(digest2Sentences[lastIndex].anchor_speaker);
  });
});
