import { describe, expect, it } from "vitest";
import { type M0CaptionTrack, normalizeM0Captions } from "@/lib/feed/normalizeM0Captions";
// Reason (Rule 9): assert against the REAL M0 caption artifacts the seed reads —
// not hand-made fixtures. The seed (`supabase/seed/seedM0Digests.ts`) builds each
// `caption_sentences.word_tokens` row by running THIS `normalizeM0Captions` over
// these exact JSON files, then inserts the result verbatim. So validating the
// normalizer's output against the source JSON validates the seed's caption
// mapping deterministically, with no live-DB dependency.
import digest1Raw from "../../agents/m0/output/captions/digest-1.captions.json";
import digest2Raw from "../../agents/m0/output/captions/digest-2.captions.json";
import digest3Raw from "../../agents/m0/output/captions/digest-3.captions.json";
import digest4Raw from "../../agents/m0/output/captions/digest-4.captions.json";
import digest5Raw from "../../agents/m0/output/captions/digest-5.captions.json";
import type { AnchorSpeaker } from "@/types/feed";

/**
 * Each M0 caption track paired with the story anchor order the seed uses for it
 * (`s{N}.anchors` from `prototype/News20 Prototype/data.js`, M0 positional
 * mapping `s{N}` ↔ `digest-{N}`). The anchor pair does not affect the word
 * sequence — it only sets per-sentence speaker — but we pass the real pair so the
 * normalize call is identical to the seed's.
 */
const M0_TRACKS: { digestKey: string; track: M0CaptionTrack; anchors: [AnchorSpeaker, AnchorSpeaker] }[] = [
  { digestKey: "digest-1", track: digest1Raw as M0CaptionTrack, anchors: ["ALEX", "JORDAN"] },
  { digestKey: "digest-2", track: digest2Raw as M0CaptionTrack, anchors: ["JORDAN", "ALEX"] },
  { digestKey: "digest-3", track: digest3Raw as M0CaptionTrack, anchors: ["ALEX", "JORDAN"] },
  { digestKey: "digest-4", track: digest4Raw as M0CaptionTrack, anchors: ["JORDAN", "ALEX"] },
  { digestKey: "digest-5", track: digest5Raw as M0CaptionTrack, anchors: ["ALEX", "JORDAN"] },
];

describe("seed caption mapping — verbatim word-sequence fidelity (the karaoke contract)", () => {
  for (const { digestKey, track, anchors } of M0_TRACKS) {
    it(`${digestKey}: flattened seeded word_tokens reconstruct the source words[] 1:1 (count + order + text)`, () => {
      // WHY: the seed inserts exactly these word_tokens into caption_sentences,
      // and the karaoke renderer lights one spoken word at a time against the
      // audio clock. If the seed's transform dropped, re-tokenized, or reordered
      // any word, the on-screen caption would desync from the narration. The only
      // safe invariant is: flatten the seeded sentence tokens in sentence order,
      // and you get back the source `words[]` verbatim. This fails the moment that
      // mapping drifts.
      const seededSentences = normalizeM0Captions(track, anchors);
      const flattenedSeededWords = seededSentences.flatMap((sentence) =>
        sentence.word_tokens.map((token) => token.word_text),
      );
      const sourceWords = track.words.map((word) => word.word);

      expect(flattenedSeededWords).toHaveLength(track.words.length);
      expect(flattenedSeededWords).toEqual(sourceWords);
    });

    it(`${digestKey}: preserves exactly one highlight word per caption sentence, matching the source tagging`, () => {
      // WHY: the design law is exactly one #FACC15 keyword per sentence; the seed
      // must carry M0's `is_highlight` tagging through unchanged. We assert both
      // the count (==1 per sentence) and that the highlighted token equals the
      // word M0 tagged for that sentence — so a seed that lost or re-picked the
      // highlight fails here.
      const seededSentences = normalizeM0Captions(track, anchors);

      for (const sentence of seededSentences) {
        const highlightTokens = sentence.word_tokens.filter((token) => token.is_highlight);
        expect(highlightTokens).toHaveLength(1);
        expect(sentence.highlight_keyword).toBe(highlightTokens[0].word_text);

        // Cross-check against the source JSON: the seeded highlight must be the
        // same word M0 tagged in this sentence (verbatim, not a re-derivation).
        const sourceHighlightWords = track.words
          .filter((word) => word.sentence_index === sentence.sentence_index && word.is_highlight)
          .map((word) => word.word);
        expect(sourceHighlightWords).toEqual([highlightTokens[0].word_text]);
      }
    });

    it(`${digestKey}: every seeded word token carries non-empty start_ms/end_ms timings`, () => {
      // WHY: the seed inserts these tokens into word_tokens JSONB and the reel's
      // current-word lookup needs each token's [start_ms, end_ms) window. A token
      // without integer timings would break karaoke advance.
      const seededSentences = normalizeM0Captions(track, anchors);

      for (const sentence of seededSentences) {
        expect(sentence.word_tokens.length).toBeGreaterThan(0);
        for (const token of sentence.word_tokens) {
          expect(Number.isInteger(token.start_ms)).toBe(true);
          expect(Number.isInteger(token.end_ms)).toBe(true);
          expect(token.end_ms).toBeGreaterThanOrEqual(token.start_ms);
        }
      }
    });
  }

  it("seeds at least 6 caption sentences for every one of the 5 M0 digests (reel DoD floor)", () => {
    // WHY: the SP3 DoD requires each story to have ≥6 caption_sentences; this
    // guards against a future caption track shrinking below the reel's floor.
    for (const { digestKey, track, anchors } of M0_TRACKS) {
      const seededSentences = normalizeM0Captions(track, anchors);
      expect(seededSentences.length, `${digestKey} sentence count`).toBeGreaterThanOrEqual(6);
      // The produced sentence count must equal M0's declared sentence_count.
      expect(seededSentences.length, `${digestKey} sentence_count match`).toBe(track.sentence_count);
    }
  });
});
