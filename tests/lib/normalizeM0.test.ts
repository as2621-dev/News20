import { describe, expect, it } from "vitest";
import { type M0CaptionTrack, normalizeM0Captions } from "@/lib/feed/normalizeM0Captions";
// Reason (Rule 9): assert against the REAL M0 caption artifact, not a hand-made
// fixture. If the normalizer drops/reorders words, mis-converts seconds→ms, or
// loses the one-keyword-per-sentence invariant, these FAIL against ground truth.
import digest1Raw from "../../agents/m0/output/captions/digest-1.captions.json";
import digest3Raw from "../../agents/m0/output/captions/digest-3.captions.json";

const digest1Track = digest1Raw as M0CaptionTrack;
const digest3Track = digest3Raw as M0CaptionTrack;

describe("normalizeM0Captions", () => {
  it("preserves the verbatim word sequence (count + order) flat words[] → sentence tokens", () => {
    // WHY: the karaoke renders every spoken word; re-tokenizing or dropping a
    // word would desync the caption from the audio. The flattened token stream
    // must equal the raw words[] 1:1.
    const sentences = normalizeM0Captions(digest1Track, ["ALEX", "JORDAN"]);
    const flattenedWordTexts = sentences.flatMap((sentence) => sentence.word_tokens.map((token) => token.word_text));
    const rawWordTexts = digest1Track.words.map((word) => word.word);

    expect(flattenedWordTexts).toEqual(rawWordTexts);
    expect(flattenedWordTexts).toHaveLength(digest1Track.words.length); // 130
  });

  it("groups words into exactly sentence_count sentences with contiguous 0-based indices", () => {
    // WHY: a missing/duplicated sentence_index would mis-slice the track and
    // break the current-sentence lookup + speaker alternation.
    const sentences = normalizeM0Captions(digest1Track, ["ALEX", "JORDAN"]);
    expect(sentences).toHaveLength(digest1Track.sentence_count); // 11
    expect(sentences.map((sentence) => sentence.sentence_index)).toEqual(
      Array.from({ length: digest1Track.sentence_count }, (_unused, index) => index),
    );
  });

  it("converts seconds → ms with Math.round and derives sentence windows from first/last word", () => {
    // WHY: the audio clock is ms; an off-by-rounding conversion drifts the
    // karaoke. digest-1 sentence 0: first word "The" start 0.0s, last word
    // "shipping." end 9.463s → [0, 9463]ms; highlight "target" [2007, 2509]ms.
    const sentences = normalizeM0Captions(digest1Track, ["ALEX", "JORDAN"]);
    const firstSentence = sentences[0];

    expect(firstSentence.sentence_start_ms).toBe(0);
    expect(firstSentence.sentence_end_ms).toBe(9463);

    const targetToken = firstSentence.word_tokens.find((token) => token.word_text === "target");
    expect(targetToken).toBeDefined();
    expect(targetToken?.start_ms).toBe(2007);
    expect(targetToken?.end_ms).toBe(2509);
    expect(targetToken?.is_highlight).toBe(true);
  });

  it("emits exactly one highlight token per sentence and matches highlight_keyword to it", () => {
    // WHY: the design law is "exactly one #FACC15 keyword per sentence"; the
    // scalar highlight_keyword the UI may read must equal that token's text.
    const sentences = normalizeM0Captions(digest1Track, ["ALEX", "JORDAN"]);
    for (const sentence of sentences) {
      const highlightTokens = sentence.word_tokens.filter((token) => token.is_highlight);
      expect(highlightTokens).toHaveLength(1);
      expect(sentence.highlight_keyword).toBe(highlightTokens[0].word_text);
    }
  });

  it("handles a sentence whose first word is the highlight (digest-3 sentence 0)", () => {
    // WHY: edge case — the keyword need not be mid-sentence. digest-3 opens with
    // the highlight "Physicists"; the join + keyword must still be correct.
    const sentences = normalizeM0Captions(digest3Track, ["ALEX", "JORDAN"]);
    const firstSentence = sentences[0];
    expect(firstSentence.word_tokens[0].word_text).toBe("Physicists");
    expect(firstSentence.word_tokens[0].is_highlight).toBe(true);
    expect(firstSentence.highlight_keyword).toBe("Physicists");
  });

  it("joins word_texts (with punctuation as-is) into sentence_text", () => {
    // WHY: sentence_text feeds search/QA later; it must be the verbatim join,
    // punctuation preserved (not stripped or re-spaced).
    const sentences = normalizeM0Captions(digest1Track, ["ALEX", "JORDAN"]);
    const firstSentence = sentences[0];
    const expectedText = firstSentence.word_tokens.map((token) => token.word_text).join(" ");
    expect(firstSentence.sentence_text).toBe(expectedText);
    // Spot-check the actual content so a silent join-logic change is caught.
    expect(firstSentence.sentence_text).toContain("The U.S. military hit another target inside Iran");
  });

  it("alternates anchor_speaker by sentence_index % 2 using the story's anchor order", () => {
    // WHY: per-sentence speaker is the documented alternation approximation
    // (M0 has no speaker field). anchors[0] owns even sentences, anchors[1] odd.
    const sentences = normalizeM0Captions(digest1Track, ["ALEX", "JORDAN"]);
    expect(sentences[0].anchor_speaker).toBe("ALEX");
    expect(sentences[1].anchor_speaker).toBe("JORDAN");
    expect(sentences[2].anchor_speaker).toBe("ALEX");

    // Order matters: a flipped anchor pair flips every label.
    const flipped = normalizeM0Captions(digest1Track, ["JORDAN", "ALEX"]);
    expect(flipped[0].anchor_speaker).toBe("JORDAN");
    expect(flipped[1].anchor_speaker).toBe("ALEX");
  });
});
