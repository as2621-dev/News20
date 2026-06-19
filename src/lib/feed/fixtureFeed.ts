/**
 * Fixture feed provider for Phase 1 — returns the 5 real M0 digests in canonical
 * {@link Story} shape, with no backend.
 *
 * **The cross-phase seam.** {@link getFeed} is `async` *on purpose*: Phase 3
 * swaps its body for a Supabase fetch (`stories` ⋈ current `digests` ⋈
 * `segments` + `caption_sentences`) while the signature — and every consumer —
 * stays put. Here the body resolves from **build-time-bundled** data instead:
 * the 5 M0 caption JSONs are `import`ed (so they land in the bundle and Vitest
 * under jsdom needs no `fetch` mock), normalized via
 * {@link normalizeM0Captions}, and paired with story metadata transcribed from
 * the prototype `data.js` (STORIES + SEGMENTS). Audio + poster stay **URL
 * references** under `/fixtures/...` (served from `public/`, loaded by the
 * `<audio>` / `<img>` elements in Sub-phase 3 — not fetched here).
 *
 * Positional mapping: `digest-N` ↔ `STORIES[N-1]` (the M0 build order).
 */

import { type M0CaptionTrack, normalizeM0Captions } from "@/lib/feed/normalizeM0Captions";
import { logger } from "@/lib/logger";
import type { AnchorSpeaker, SegmentKey, Story } from "@/types/feed";

// Build-time bundle of the 5 M0 caption tracks (on-disk M0 shape, times in
// seconds). Imported from the M0 worker output so the fixtures track the real
// generated artifacts; the public/ copies (same bytes) are what the browser
// streams for audio/posters. `resolveJsonModule` types these as the JSON shape;
// we hand them to normalizeM0Captions which is typed to M0CaptionTrack.
import digest1Captions from "../../../agents/m0/output/captions/digest-1.captions.json";
import digest2Captions from "../../../agents/m0/output/captions/digest-2.captions.json";
import digest3Captions from "../../../agents/m0/output/captions/digest-3.captions.json";
import digest4Captions from "../../../agents/m0/output/captions/digest-4.captions.json";
import digest5Captions from "../../../agents/m0/output/captions/digest-5.captions.json";

/**
 * Per-digest story metadata transcribed from the prototype `data.js`
 * (STORIES[N-1]) + SEGMENTS. The prototype's own `captions` array is NOT used —
 * the real M0 caption JSON is the karaoke source (its sentence structure differs
 * from the prototype mock). Only headline / segment / anchors carry over.
 */
interface FixtureStoryMeta {
  /** Matches the M0 caption `digest_id` and the fixture URL stem. */
  digest_id: string;
  /** `stories.story_headline` (verbatim from `data.js`). */
  headline: string;
  /** `stories.story_segment_slug`. */
  segment_key: SegmentKey;
  /** Anchor pair, in `data.js` order; `anchors[i % 2]` = sentence i's speaker. */
  anchors: [AnchorSpeaker, AnchorSpeaker];
}

/**
 * Segment slug → display label. The 8 picker roots; labels EQUAL the onboarding
 * chips (`src/lib/feedBuckets.ts` `DESIGN_BUCKETS`) so the reel chip matches.
 */
const SEGMENT_LABELS: Record<SegmentKey, string> = {
  ai: "AI",
  geopolitics: "Geopolitics",
  business: "Business",
  environment: "Environment",
  politics: "Politics",
  tech: "Tech",
  sport: "Sport",
  arts: "Arts",
};

/** Segment slug → accent hex. Equals the onboarding chips (`DESIGN_BUCKETS`). */
const SEGMENT_ACCENT_HEX: Record<SegmentKey, string> = {
  ai: "#3B82F6",
  geopolitics: "#EF4444",
  business: "#22C55E",
  environment: "#34D399",
  politics: "#A78BFA",
  tech: "#22D3EE",
  sport: "#F59E0B",
  arts: "#E8B7BC",
};

/**
 * Segment slug → Detail category, so fixture stories render the right panel
 * template. The Detail templates still key off the legacy bucket names
 * (`detail_templates.py` / `detailTemplates.ts`), so the new roots map onto the
 * nearest legacy template (`business→markets`, `arts→culture`, the split
 * geopolitics/politics/environment→world). Fixtures have no breaking signal.
 */
const SEGMENT_DETAIL_CATEGORY: Record<SegmentKey, string> = {
  ai: "tech",
  geopolitics: "world",
  business: "markets",
  environment: "world",
  politics: "world",
  tech: "tech",
  sport: "sport",
  arts: "culture",
};

/** Story metadata for the 5 M0 digests, positionally `digest-N` ↔ STORIES[N-1]. */
const FIXTURE_STORY_META: readonly FixtureStoryMeta[] = [
  {
    digest_id: "digest-1",
    headline: 'U.S. strikes Iran again as Trump says a deal is "close"',
    segment_key: "geopolitics",
    anchors: ["ALEX", "JORDAN"],
  },
  {
    digest_id: "digest-2",
    headline: "Travis Kelce buys a minority stake in the Cleveland Guardians",
    segment_key: "sport",
    anchors: ["JORDAN", "ALEX"],
  },
  {
    digest_id: "digest-3",
    headline: "Houston physicists break a 30-year superconductivity record",
    segment_key: "tech",
    anchors: ["ALEX", "JORDAN"],
  },
  {
    digest_id: "digest-4",
    headline: "Nvidia's blowout quarter — yet the stock slips",
    segment_key: "business",
    anchors: ["JORDAN", "ALEX"],
  },
  {
    digest_id: "digest-5",
    headline: "Pope Leo XIV issues his strongest warning yet on AI",
    segment_key: "arts",
    anchors: ["ALEX", "JORDAN"],
  },
] as const;

/** Bundled caption tracks keyed by digest id (same order as the metadata). */
const FIXTURE_CAPTION_TRACKS: Record<string, M0CaptionTrack> = {
  "digest-1": digest1Captions as M0CaptionTrack,
  "digest-2": digest2Captions as M0CaptionTrack,
  "digest-3": digest3Captions as M0CaptionTrack,
  "digest-4": digest4Captions as M0CaptionTrack,
  "digest-5": digest5Captions as M0CaptionTrack,
};

/** Convert a seconds timestamp to integer milliseconds. */
function secondsToMs(seconds: number): number {
  return Math.round(seconds * 1000);
}

/** Build one canonical {@link Story} from its metadata + bundled caption track. */
function buildFixtureStory(meta: FixtureStoryMeta): Story {
  const m0Track = FIXTURE_CAPTION_TRACKS[meta.digest_id];
  const captionSentences = normalizeM0Captions(m0Track, meta.anchors);

  return {
    digest_id: meta.digest_id,
    headline: meta.headline,
    segment_key: meta.segment_key,
    story_detail_category: SEGMENT_DETAIL_CATEGORY[meta.segment_key],
    segment_label: SEGMENT_LABELS[meta.segment_key],
    segment_accent_hex: SEGMENT_ACCENT_HEX[meta.segment_key],
    anchors: meta.anchors,
    digest_audio_url: `/fixtures/audio/${meta.digest_id}.mp3`,
    audio_duration_ms: secondsToMs(m0Track.audio_duration_s),
    speech_end_ms: secondsToMs(m0Track.speech_end_s),
    poster_url: `/fixtures/posters/${meta.digest_id}.png`,
    caption_sentences: captionSentences,
    // Reason: mark the first fixture as a source slot so dev mode (fixtures feed)
    // can QA the followed-source chip without a backend; the rest are normal
    // interest slots. phase-SP1 removed the breaking tier.
    feed_slot_kind: meta.digest_id === "digest-1" ? "source" : "interest",
  };
}

/**
 * Return the reel feed — the 5 M0 stories in canonical {@link Story} shape.
 *
 * `async` to match the production signature (Phase 3 replaces the body with a
 * Supabase query); here it resolves synchronously from bundled fixtures.
 *
 * @returns The 5 stories, in M0 build order (`digest-1`...`digest-5`).
 *
 * @example
 * const feed = await getFeed();
 * feed.length;                       // 5
 * feed[0].headline;                  // 'U.S. strikes Iran again ...'
 * feed[0].caption_sentences[0].word_tokens[0].word_text; // "The"
 */
export async function getFeed(): Promise<Story[]> {
  logger.info("get_feed_started", { feed_source: "m0_fixtures" });

  const stories = FIXTURE_STORY_META.map(buildFixtureStory);

  logger.info("get_feed_completed", {
    feed_source: "m0_fixtures",
    story_count: stories.length,
  });

  return stories;
}
