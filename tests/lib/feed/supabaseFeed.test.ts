import { describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { getFeed } from "@/lib/feed/supabaseFeed";
import type { Story } from "@/types/feed";

/**
 * Zod schema mirroring `src/types/feed.ts` `Story`. WHY: the phase DoD requires
 * the Supabase feed to validate against the Phase-1 contract — this parse FAILS
 * if any DB-row → Story column mapping drifts (a renamed field, a number stored
 * as a string, a missing caption token). It encodes the contract, not just the
 * happy path, so it cannot pass while the business mapping is wrong (Rule 9).
 */
const wordTokenSchema = z.object({
  word_text: z.string(),
  is_highlight: z.boolean(),
  start_ms: z.number(),
  end_ms: z.number(),
});
const captionSentenceSchema = z.object({
  sentence_index: z.number(),
  anchor_speaker: z.enum(["ALEX", "JORDAN"]),
  sentence_text: z.string(),
  highlight_keyword: z.string(),
  sentence_start_ms: z.number(),
  sentence_end_ms: z.number(),
  word_tokens: z.array(wordTokenSchema).min(1),
});
const storySchema = z.object({
  digest_id: z.string(),
  headline: z.string(),
  segment_key: z.enum(["geopolitics", "markets", "tech", "sport", "wildcard"]),
  segment_label: z.string(),
  segment_accent_hex: z.string(),
  anchors: z.tuple([z.enum(["ALEX", "JORDAN"]), z.enum(["ALEX", "JORDAN"])]),
  digest_audio_url: z.string(),
  audio_duration_ms: z.number(),
  speech_end_ms: z.number(),
  poster_url: z.string(),
  caption_sentences: z.array(captionSentenceSchema).min(1),
});

/**
 * Fake Supabase client whose `.from().select().order().returns()` chain resolves
 * to the given rows. Mocks at the client boundary (CLAUDE.md mocking strategy).
 */
function makeFakeClient(result: { data: unknown; error: unknown }) {
  const returns = vi.fn().mockResolvedValue(result);
  const order = vi.fn().mockReturnValue({ returns });
  const select = vi.fn().mockReturnValue({ order });
  const from = vi.fn().mockReturnValue({ select });
  // Reason: the fake only implements the query chain getFeed() uses; `as never`
  // satisfies the SupabaseClient type at this test boundary without a full stub.
  return { client: { from } as never, from, select, order, returns };
}

/** A representative story row as PostgREST returns it (1:1 embeds are arrays). */
const SAMPLE_ROW = {
  story_id: "s1",
  story_headline: 'U.S. strikes Iran again as Trump says a deal is "close"',
  story_segment_slug: "geopolitics",
  segments: [{ segment_label: "Geopolitics", segment_accent_hex: "#EF4444" }],
  digests: [
    {
      digest_id: "11111111-1111-1111-1111-111111111111",
      digest_audio_url: "https://cdn/digest-audio/digest-1.mp3",
      digest_duration_ms: 23420,
      digest_ambient_poster_url: "https://cdn/story-posters/digest-1.png",
      digest_is_current: true,
      caption_sentences: [
        {
          sentence_index: 1,
          anchor_speaker: "JORDAN",
          sentence_text: "Markets reacted fast.",
          highlight_keyword: "fast.",
          sentence_start_ms: 2400,
          sentence_end_ms: 6000,
          word_tokens: [
            { word_text: "Markets", is_highlight: false, start_ms: 2400, end_ms: 3000 },
            { word_text: "reacted", is_highlight: false, start_ms: 3000, end_ms: 3600 },
            { word_text: "fast.", is_highlight: true, start_ms: 3600, end_ms: 6000 },
          ],
        },
        {
          sentence_index: 0,
          anchor_speaker: "ALEX",
          sentence_text: "The U.S. struck Iran.",
          highlight_keyword: "Iran.",
          sentence_start_ms: 0,
          sentence_end_ms: 2400,
          word_tokens: [
            { word_text: "The", is_highlight: false, start_ms: 0, end_ms: 400 },
            { word_text: "U.S.", is_highlight: false, start_ms: 400, end_ms: 1200 },
            { word_text: "struck", is_highlight: false, start_ms: 1200, end_ms: 1800 },
            { word_text: "Iran.", is_highlight: true, start_ms: 1800, end_ms: 2400 },
          ],
        },
      ],
    },
  ],
};

describe("getFeed (supabase source)", () => {
  it("maps DB rows to a Story that validates against the Phase-1 contract", async () => {
    const { client, from, order } = makeFakeClient({ data: [SAMPLE_ROW], error: null });

    const feed: Story[] = await getFeed(client);

    expect(from).toHaveBeenCalledWith("stories");
    expect(order).toHaveBeenCalledWith("story_id", { ascending: true });
    expect(feed).toHaveLength(1);

    // Contract gate: a drift in any column→field mapping breaks this parse.
    const parsed = storySchema.parse(feed[0]);
    expect(parsed.digest_id).toBe("s1");
    expect(parsed.segment_label).toBe("Geopolitics");
    expect(parsed.segment_accent_hex).toBe("#EF4444");
    expect(parsed.digest_audio_url).toBe("https://cdn/digest-audio/digest-1.mp3");
    expect(parsed.poster_url).toBe("https://cdn/story-posters/digest-1.png");
  });

  it("maps the segment when PostgREST returns the many-to-one embed as a single object", async () => {
    // WHY: this is the REAL production shape. `stories.story_segment_slug` is a
    // many-to-one FK, so PostgREST returns `segments` as a single object, NOT an
    // array. A prior bug read it as `segments[0]`, which is `undefined` here and
    // throws "missing its segment". This case fails if the `Array.isArray`
    // normalization in mapStoryRow is reverted to array-only indexing (Rule 9).
    const objectEmbedRow = {
      ...SAMPLE_ROW,
      segments: { segment_label: "Geopolitics", segment_accent_hex: "#EF4444" },
    };
    const { client } = makeFakeClient({ data: [objectEmbedRow], error: null });

    const feed: Story[] = await getFeed(client);

    expect(feed).toHaveLength(1);
    const parsed = storySchema.parse(feed[0]);
    expect(parsed.segment_key).toBe("geopolitics");
    expect(parsed.segment_label).toBe("Geopolitics");
    expect(parsed.segment_accent_hex).toBe("#EF4444");
  });

  it("orders captions by sentence_index and derives the anchor pair + speech_end_ms", async () => {
    // WHY: PostgREST does not guarantee embedded-row order; the karaoke advances
    // in sentence order. Rows are supplied out of order on purpose, so this fails
    // if getFeed stops sorting. anchors + speech_end_ms are derived, not columns.
    const { client } = makeFakeClient({ data: [SAMPLE_ROW], error: null });

    const story = (await getFeed(client))[0];

    expect(story.caption_sentences.map((sentence) => sentence.sentence_index)).toEqual([0, 1]);
    expect(story.caption_sentences[0].sentence_text).toBe("The U.S. struck Iran.");
    expect(story.anchors).toEqual(["ALEX", "JORDAN"]);
    // speech_end_ms = last sentence end (6000), not the digest duration (23420).
    expect(story.speech_end_ms).toBe(6000);
    expect(story.audio_duration_ms).toBe(23420);
  });

  it("throws when a story has no current digest", async () => {
    // WHY: a story with no digest is a broken seed; failing loud beats a silent
    // reel screen with no audio.
    const brokenRow = { ...SAMPLE_ROW, digests: [] };
    const { client } = makeFakeClient({ data: [brokenRow], error: null });

    await expect(getFeed(client)).rejects.toThrow(/no current digest/i);
  });

  it("throws when the query returns an error", async () => {
    const { client } = makeFakeClient({ data: null, error: { message: "permission denied" } });

    await expect(getFeed(client)).rejects.toThrow(/Failed to load feed/i);
  });
});
