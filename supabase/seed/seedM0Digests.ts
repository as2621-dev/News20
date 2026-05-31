/**
 * Seed script (Phase 1b SP3) — load the 5 M0 digests into hosted Supabase.
 *
 * Reads (all verified to exist):
 *   - story content from `prototype/News20 Prototype/data.js` (assigns
 *     `window.NEWS20_DATA = { STORIES, SEGMENTS, ... }`)
 *   - word-timed captions from `agents/m0/output/captions/digest-{1..5}.captions.json`
 *   - audio from `agents/m0/output/audio/digest-{1..5}.mp3`
 *   - posters from `assets/m0/digest-{1..5}/poster.png`
 *
 * Writes (service-role; bypasses RLS):
 *   - storage: digest-audio/digest-{N}.mp3, story-posters/digest-{N}.png
 *   - rows: segments, anchors, stories, story_trust, story_timeline,
 *     detail_chunks, story_sources, suggested_questions, story_qa, story_topics,
 *     story_analytics + detail_key_points + story_trust coverage_mode/reach cols
 *     (Phase 2c fixtures), one current `digests` per story, and
 *     `caption_sentences` (word_tokens built via the Phase-1 `normalizeM0Captions`
 *     so the karaoke matches the reel).
 *
 * Idempotent: storage uploads use upsert; rows upsert on natural unique keys;
 * caption rows are deleted then re-inserted per digest.
 *
 * Mapping: story `s{N}` ↔ caption/audio/poster `digest-{N}` (M0 positional order).
 * Secrets: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY from process.env only.
 * Run: `npm run seed` (requires migrations 0001 + 0002 applied first).
 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { type M0CaptionTrack, normalizeM0Captions } from "@/lib/feed/normalizeM0Captions";
import type { AnchorSpeaker, SegmentKey } from "@/types/feed";

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const AUDIO_BUCKET = "digest-audio";
const POSTER_BUCKET = "story-posters";

/** Anchor lookup rows (reference/supabase-schema.md §anchors). */
const ANCHOR_ROWS = [
  { anchor_id: "ALEX", anchor_display_name: "Alex", gemini_voice_id: "Leda", identity_color_hex: "#6C8CFF", anchor_sort_order: 0 },
  { anchor_id: "JORDAN", anchor_display_name: "Jordan", gemini_voice_id: "Sadaltager", identity_color_hex: "#C792EA", anchor_sort_order: 1 },
] as const;

/**
 * Phase 2c Detail-analytics fixtures (second analytic + 5 key points + coverage
 * mode) for the s1–s5 prototype stories. The prototype `data.js` predates Phase
 * 2c, so these realistic shapes are authored here so the existing Detail UI
 * renders the new tabs against real data without a live pipeline run. Keyed by
 * story id; the `analytic_kind`/`coverage_mode` match each story's segment
 * (geopolitics→market_impact+partisan, sport→stakes+reach, tech→impact+reach,
 * markets→ripple+reach, wildcard→why_it_matters+reach — Decisions #2/#3).
 */
interface Phase2cFixture {
  second_analytic: {
    analytic_kind: "market_impact" | "ripple" | "impact" | "stakes" | "why_it_matters";
    analytic_tab_label: string;
    analytic_headline: string;
    analytic_summary_text: string;
    analytic_rows: {
      analytic_row_label: string;
      analytic_row_value: string | null;
      analytic_row_direction: "up" | "down" | "flat" | null;
      analytic_row_note: string | null;
    }[];
    analytic_is_grounded: boolean;
  };
  coverage_mode: "partisan" | "reach";
  coverage_momentum: string | null;
  coverage_originating_outlet_name: string | null;
  coverage_notable_outlet_names: string[];
  detail_key_points: string[];
}

const PHASE_2C_FIXTURES: Record<string, Phase2cFixture> = {
  s1: {
    second_analytic: {
      analytic_kind: "market_impact",
      analytic_tab_label: "MARKET IMPACT",
      analytic_headline: "Oil markets brace as Hormuz tensions escalate",
      analytic_summary_text:
        "New transit rules on a chokepoint carrying ~20% of seaborne crude push energy traders toward a risk premium.",
      analytic_rows: [
        { analytic_row_label: "Hormuz oil share", analytic_row_value: "~20%", analytic_row_direction: null, analytic_row_note: "of global oil transit" },
        { analytic_row_label: "Brent crude", analytic_row_value: null, analytic_row_direction: "up", analytic_row_note: "risk premium building" },
        { analytic_row_label: "Energy equities", analytic_row_value: null, analytic_row_direction: "up", analytic_row_note: "defensive rotation" },
      ],
      analytic_is_grounded: true,
    },
    coverage_mode: "partisan",
    coverage_momentum: null,
    coverage_originating_outlet_name: null,
    coverage_notable_outlet_names: [],
    detail_key_points: [
      "The U.S. struck a second site inside Iran overnight, escalating a weeks-long confrontation.",
      "Trump says a deal to end the fighting is close but is not satisfied with the terms.",
      "Iran issued new transit rules for the Strait of Hormuz in defiance of U.S. warnings.",
      "Roughly a fifth of the world's oil moves through the Hormuz chokepoint.",
      "Analysts are split on whether the strikes make a negotiated deal more or less likely.",
    ],
  },
  s2: {
    second_analytic: {
      analytic_kind: "stakes",
      analytic_tab_label: "STAKES",
      analytic_headline: "A star athlete crosses into baseball ownership",
      analytic_summary_text:
        "Kelce's minority stake signals the growing overlap between marquee players and franchise ownership.",
      analytic_rows: [
        { analytic_row_label: "Stake type", analytic_row_value: null, analytic_row_direction: null, analytic_row_note: "minority equity" },
        { analytic_row_label: "Franchise", analytic_row_value: null, analytic_row_direction: null, analytic_row_note: "Cleveland Guardians" },
      ],
      analytic_is_grounded: true,
    },
    coverage_mode: "reach",
    coverage_momentum: "developing",
    coverage_originating_outlet_name: "ESPN",
    coverage_notable_outlet_names: ["ESPN", "The Athletic", "Cleveland.com"],
    detail_key_points: [
      "Travis Kelce bought a minority stake in MLB's Cleveland Guardians.",
      "The deal adds Kelce to a growing list of athletes turning into team owners.",
      "His stake is a passive minority position, not a controlling interest.",
      "The move deepens Kelce's ties to Ohio sports beyond the NFL.",
      "Athlete-owners are increasingly common across major U.S. leagues.",
    ],
  },
  s3: {
    second_analytic: {
      analytic_kind: "impact",
      analytic_tab_label: "IMPACT",
      analytic_headline: "A superconductivity record falls after 30 years",
      analytic_summary_text:
        "Houston physicists pushed the temperature ceiling for superconductivity, a step toward lossless power.",
      analytic_rows: [
        { analytic_row_label: "Prior record age", analytic_row_value: "30", analytic_row_direction: null, analytic_row_note: "years" },
        { analytic_row_label: "Field", analytic_row_value: null, analytic_row_direction: null, analytic_row_note: "condensed matter physics" },
      ],
      analytic_is_grounded: true,
    },
    coverage_mode: "reach",
    coverage_momentum: "settled",
    coverage_originating_outlet_name: "Nature",
    coverage_notable_outlet_names: ["Nature", "Science", "Ars Technica"],
    detail_key_points: [
      "Houston physicists broke a 30-year superconductivity record.",
      "The result raises the temperature at which a material conducts without resistance.",
      "Higher-temperature superconductors could reduce energy loss in power grids.",
      "The work has been published and is now being scrutinized by peers.",
      "Practical applications remain years away pending replication.",
    ],
  },
  s4: {
    second_analytic: {
      analytic_kind: "ripple",
      analytic_tab_label: "RIPPLE",
      analytic_headline: "A blowout quarter, yet the stock slips",
      analytic_summary_text:
        "Record results met sky-high expectations, and the muted reaction rippled across the chip complex.",
      analytic_rows: [
        { analytic_row_label: "Quarter result", analytic_row_value: null, analytic_row_direction: "up", analytic_row_note: "record revenue" },
        { analytic_row_label: "Share price", analytic_row_value: null, analytic_row_direction: "down", analytic_row_note: "post-earnings" },
        { analytic_row_label: "Chip peers", analytic_row_value: null, analytic_row_direction: "down", analytic_row_note: "sympathy move" },
      ],
      analytic_is_grounded: true,
    },
    coverage_mode: "reach",
    coverage_momentum: "breaking",
    coverage_originating_outlet_name: "Bloomberg",
    coverage_notable_outlet_names: ["Bloomberg", "Reuters", "CNBC", "WSJ"],
    detail_key_points: [
      "Nvidia reported a record quarter that beat expectations.",
      "The stock slipped despite the strong results.",
      "Investors had priced in an even larger beat.",
      "The muted reaction pressured other chip stocks.",
      "Guidance and margins drew as much attention as the headline numbers.",
    ],
  },
  s5: {
    second_analytic: {
      analytic_kind: "why_it_matters",
      analytic_tab_label: "WHY IT MATTERS",
      analytic_headline: "The Pope sharpens the moral case on AI",
      analytic_summary_text:
        "A leading global moral voice frames AI governance as a question of human dignity, not just policy.",
      analytic_rows: [
        { analytic_row_label: "Speaker", analytic_row_value: null, analytic_row_direction: null, analytic_row_note: "Pope Leo XIV" },
        { analytic_row_label: "Theme", analytic_row_value: null, analytic_row_direction: null, analytic_row_note: "human dignity + AI" },
      ],
      analytic_is_grounded: true,
    },
    coverage_mode: "reach",
    coverage_momentum: "developing",
    coverage_originating_outlet_name: "Vatican News",
    coverage_notable_outlet_names: ["Vatican News", "AP", "BBC News"],
    detail_key_points: [
      "Pope Leo XIV issued his strongest warning yet on artificial intelligence.",
      "He framed AI governance as a matter of human dignity.",
      "The remarks add a major moral voice to the AI policy debate.",
      "He urged guardrails that keep technology serving people.",
      "The intervention may influence how policymakers discuss AI ethics.",
    ],
  },
};

/** One story object as authored in the prototype `data.js`. */
interface PrototypeStory {
  id: string;
  segment: SegmentKey;
  headline: string;
  dek: string;
  outlet?: string;
  time?: string;
  image?: string;
  anchors: [AnchorSpeaker, AnchorSpeaker];
  keyFigure?: { value?: string; label?: string };
  trust?: {
    coverage?: { left?: number; center?: number; right?: number };
    outlet_count?: number;
    blindspot?: string | null;
    timeline?: { when: string; what: string }[];
    opposing_view?: string;
  };
  citations?: string[];
  topics?: string[];
  detail_chunks?: string[];
  suggested_questions?: string[];
  answers?: Record<string, string>;
}

/** The shape `data.js` assigns to `window.NEWS20_DATA`. */
interface News20Data {
  STORIES: PrototypeStory[];
  SEGMENTS: Record<SegmentKey, { label: string; accent: string }>;
}

/** Minimal structured logger to stdout (matches the project's JSON-log convention). */
function log(event_name: string, fields: Record<string, unknown>): void {
  console.log(JSON.stringify({ event_name, ...fields }));
}

/**
 * Load STORIES + SEGMENTS from the prototype `data.js`.
 *
 * `data.js` is a browser global script (not an ES module): it ends with
 * `window.NEWS20_DATA = { STORIES, SEGMENTS, ... }`. We evaluate it in an
 * isolated `Function` scope with a `window` shim and read the assigned global.
 */
async function loadPrototypeData(): Promise<News20Data> {
  const dataPath = path.join(PROJECT_ROOT, "prototype", "News20 Prototype", "data.js");
  const source = await readFile(dataPath, "utf-8");
  // Reason: data.js is a static asset of plain object literals plus one
  // `window.NEWS20_DATA = …` assignment. Provide a window shim, run it, return
  // the captured global. No external/user input is evaluated.
  const evaluate = new Function("window", `${source}\n; return window.NEWS20_DATA;`) as (
    windowShim: { NEWS20_DATA?: News20Data },
  ) => News20Data;
  const windowShim: { NEWS20_DATA?: News20Data } = {};
  const data = evaluate(windowShim);
  if (!data?.STORIES || !data?.SEGMENTS) {
    throw new Error(
      "Could not read STORIES/SEGMENTS from prototype/News20 Prototype/data.js. " +
        "fix_suggestion: confirm the file still assigns window.NEWS20_DATA = { STORIES, SEGMENTS }.",
    );
  }
  return data;
}

/** Build a service-role Supabase client; throws if env is missing. */
function buildServiceClient(): SupabaseClient {
  const supabaseUrl = process.env.SUPABASE_URL;
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceRoleKey) {
    throw new Error(
      "Missing env: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required. " +
        "fix_suggestion: set both in .env before running `npm run seed`.",
    );
  }
  return createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

/** Upload one file to a public bucket (upsert) and return its public URL. */
async function uploadMedia(
  client: SupabaseClient,
  bucket: string,
  storage_path: string,
  local_path: string,
  content_type: string,
): Promise<string> {
  const bytes = await readFile(local_path);
  const { error } = await client.storage.from(bucket).upload(storage_path, bytes, {
    contentType: content_type,
    upsert: true,
  });
  if (error) {
    throw new Error(
      `Failed to upload ${bucket}/${storage_path}: ${error.message}. ` +
        "fix_suggestion: confirm the bucket exists (migration 0002) and the service-role key is valid.",
    );
  }
  return client.storage.from(bucket).getPublicUrl(storage_path).data.publicUrl;
}

/** Upsert helper that throws with context on failure. */
async function upsertRows(
  client: SupabaseClient,
  table: string,
  rows: Record<string, unknown>[],
  onConflict: string,
): Promise<void> {
  if (rows.length === 0) {
    return;
  }
  const { error } = await client.from(table).upsert(rows, { onConflict });
  if (error) {
    throw new Error(`Failed to upsert ${table}: ${error.message}. fix_suggestion: confirm migration 0001 applied.`);
  }
}

/** Seed the lookup tables shared across all stories (segments, anchors). */
async function seedLookups(client: SupabaseClient, segments: News20Data["SEGMENTS"]): Promise<void> {
  const segmentRows = (Object.keys(segments) as SegmentKey[]).map((slug, index) => ({
    segment_slug: slug,
    segment_label: segments[slug].label,
    segment_accent_hex: segments[slug].accent,
    segment_sort_order: index,
  }));
  await upsertRows(client, "segments", segmentRows, "segment_slug");
  await upsertRows(client, "anchors", [...ANCHOR_ROWS], "anchor_id");
}

/** Seed the ordered/array child rows of a story (timeline, chunks, sources, Q&A, topics). */
async function seedStoryChildren(client: SupabaseClient, story: PrototypeStory): Promise<void> {
  const timelineRows = (story.trust?.timeline ?? []).map((event, index) => ({
    timeline_story_id: story.id,
    timeline_event_index: index,
    timeline_when_label: event.when,
    timeline_what_text: event.what,
  }));
  await upsertRows(client, "story_timeline", timelineRows, "timeline_story_id,timeline_event_index");

  const chunkRows = (story.detail_chunks ?? []).map((text, index) => ({
    detail_story_id: story.id,
    chunk_index: index,
    chunk_text: text,
  }));
  await upsertRows(client, "detail_chunks", chunkRows, "detail_story_id,chunk_index");

  const sourceRows = (story.citations ?? []).map((outletName) => ({
    source_story_id: story.id,
    source_outlet_name: outletName,
    source_is_citation: true,
  }));
  await upsertRows(client, "story_sources", sourceRows, "source_story_id,source_outlet_name");

  const questionRows = (story.suggested_questions ?? []).map((text, index) => ({
    question_story_id: story.id,
    question_index: index,
    question_text: text,
  }));
  await upsertRows(client, "suggested_questions", questionRows, "question_story_id,question_index");

  const qaRows = Object.entries(story.answers ?? {}).map(([question, answer]) => ({
    qa_story_id: story.id,
    qa_question_text: question,
    qa_answer_text: answer,
    qa_is_grounded: true,
    qa_source_kind: "canned",
  }));
  await upsertRows(client, "story_qa", qaRows, "qa_story_id,qa_question_text");

  const topicRows = (story.topics ?? []).map((keyword) => ({
    topic_story_id: story.id,
    topic_keyword: keyword,
  }));
  await upsertRows(client, "story_topics", topicRows, "topic_story_id,topic_keyword");

  // Phase 2c: the 1:1 second-analytic tab + 5 at-a-glance key points (fixtures).
  const fixture = PHASE_2C_FIXTURES[story.id];
  if (fixture) {
    await upsertRows(
      client,
      "story_analytics",
      [
        {
          analytic_story_id: story.id,
          analytic_kind: fixture.second_analytic.analytic_kind,
          analytic_tab_label: fixture.second_analytic.analytic_tab_label,
          analytic_headline: fixture.second_analytic.analytic_headline,
          analytic_summary_text: fixture.second_analytic.analytic_summary_text,
          analytic_rows: fixture.second_analytic.analytic_rows,
          analytic_is_grounded: fixture.second_analytic.analytic_is_grounded,
        },
      ],
      "analytic_story_id",
    );

    const keyPointRows = fixture.detail_key_points.map((text, index) => ({
      key_point_story_id: story.id,
      key_point_index: index,
      key_point_text: text,
    }));
    await upsertRows(client, "detail_key_points", keyPointRows, "key_point_story_id,key_point_index");
  }
}

/** Mint the current digest and its word-timed caption sentences. */
async function seedDigestAndCaptions(
  client: SupabaseClient,
  story: PrototypeStory,
  digestKey: string,
  audioUrl: string,
  posterUrl: string,
): Promise<void> {
  const trackPath = path.join(PROJECT_ROOT, "agents", "m0", "output", "captions", `${digestKey}.captions.json`);
  const m0Track = JSON.parse(await readFile(trackPath, "utf-8")) as M0CaptionTrack;
  const captionSentences = normalizeM0Captions(m0Track, story.anchors);

  // Reason: `uq_digests_current_per_story` is a PARTIAL unique index
  // (`... where digest_is_current`). PostgREST's `onConflict` cannot target a
  // partial index, so we do an explicit idempotent upsert: look up the existing
  // current digest for this story, then UPDATE it in place or INSERT a new one.
  // M0 has exactly one current digest per story, so this is deterministic.
  const digestValues = {
    digest_story_id: story.id,
    digest_audio_url: audioUrl,
    digest_duration_ms: Math.round(m0Track.audio_duration_s * 1000),
    digest_ambient_poster_url: posterUrl,
    digest_is_current: true,
  };
  const { data: existingDigest, error: existingError } = await client
    .from("digests")
    .select("digest_id")
    .eq("digest_story_id", story.id)
    .eq("digest_is_current", true)
    .maybeSingle();
  if (existingError) {
    throw new Error(
      `Failed to look up current digest for ${story.id}: ${existingError.message}. ` +
        "fix_suggestion: confirm migration 0001 applied and the digests table exists.",
    );
  }

  let digestId: string;
  if (existingDigest) {
    const { error: updateError } = await client
      .from("digests")
      .update(digestValues)
      .eq("digest_id", existingDigest.digest_id);
    if (updateError) {
      throw new Error(
        `Failed to update digest for ${story.id}: ${updateError.message}. ` +
          "fix_suggestion: confirm migration 0001 applied and the digests columns match.",
      );
    }
    digestId = existingDigest.digest_id as string;
  } else {
    const { data: insertedDigest, error: insertError } = await client
      .from("digests")
      .insert(digestValues)
      .select("digest_id")
      .single();
    if (insertError || !insertedDigest) {
      throw new Error(
        `Failed to insert digest for ${story.id}: ${insertError?.message ?? "no row"}. ` +
          "fix_suggestion: confirm migration 0001 applied and the digests columns match.",
      );
    }
    digestId = insertedDigest.digest_id as string;
  }

  // Replace caption rows for this digest (idempotency).
  const { error: clearError } = await client.from("caption_sentences").delete().eq("caption_digest_id", digestId);
  if (clearError) {
    throw new Error(`Failed to clear caption_sentences for ${story.id}: ${clearError.message}`);
  }

  const captionRows = captionSentences.map((sentence) => ({
    caption_digest_id: digestId,
    caption_story_id: story.id,
    sentence_index: sentence.sentence_index,
    anchor_speaker: sentence.anchor_speaker,
    sentence_text: sentence.sentence_text,
    highlight_keyword: sentence.highlight_keyword,
    sentence_start_ms: sentence.sentence_start_ms,
    sentence_end_ms: sentence.sentence_end_ms,
    word_tokens: sentence.word_tokens,
  }));
  const { error: captionError } = await client.from("caption_sentences").insert(captionRows);
  if (captionError) {
    throw new Error(`Failed to insert caption_sentences for ${story.id}: ${captionError.message}`);
  }
}

/** Seed one story: content rows + media uploads + digest + captions. */
async function seedStory(client: SupabaseClient, story: PrototypeStory, ordinal: number): Promise<void> {
  const digestKey = `digest-${ordinal}`;
  log("seeding_story_started", { story_id: story.id, digest_key: digestKey });

  // 1. Media uploads → public URLs.
  const audioUrl = await uploadMedia(
    client,
    AUDIO_BUCKET,
    `${digestKey}.mp3`,
    path.join(PROJECT_ROOT, "agents", "m0", "output", "audio", `${digestKey}.mp3`),
    "audio/mpeg",
  );
  const posterUrl = await uploadMedia(
    client,
    POSTER_BUCKET,
    `${digestKey}.png`,
    path.join(PROJECT_ROOT, "assets", "m0", digestKey, "poster.png"),
    "image/png",
  );

  // 2. stories row.
  await upsertRows(
    client,
    "stories",
    [
      {
        story_id: story.id,
        story_segment_slug: story.segment,
        story_headline: story.headline,
        story_dek: story.dek,
        story_primary_outlet_name: story.outlet ?? null,
        story_ambient_poster_url: posterUrl,
        story_published_label: story.time ?? null,
        story_key_figure_value: story.keyFigure?.value ?? null,
        story_key_figure_label: story.keyFigure?.label ?? null,
        story_outlet_count: story.trust?.outlet_count ?? 0,
        story_blindspot_lean: story.trust?.blindspot ?? null,
      },
    ],
    "story_id",
  );

  // 3. story_trust (1:1) — Phase 2c adds the adaptive coverage_mode + reach cols.
  if (story.trust) {
    const fixture = PHASE_2C_FIXTURES[story.id];
    await upsertRows(
      client,
      "story_trust",
      [
        {
          trust_story_id: story.id,
          coverage_left_count: story.trust.coverage?.left ?? 0,
          coverage_center_count: story.trust.coverage?.center ?? 0,
          coverage_right_count: story.trust.coverage?.right ?? 0,
          coverage_outlet_count: story.trust.outlet_count ?? 0,
          blindspot_lean: story.trust.blindspot ?? null,
          opposing_view_text: story.trust.opposing_view ?? null,
          // Phase 2c: default partisan when no fixture (preserves prior behaviour).
          coverage_mode: fixture?.coverage_mode ?? "partisan",
          coverage_momentum: fixture?.coverage_momentum ?? null,
          coverage_originating_outlet_name: fixture?.coverage_originating_outlet_name ?? null,
          coverage_notable_outlet_names: fixture?.coverage_notable_outlet_names ?? [],
        },
      ],
      "trust_story_id",
    );
  }

  await seedStoryChildren(client, story);
  await seedDigestAndCaptions(client, story, digestKey, audioUrl, posterUrl);

  log("seeding_story_completed", { story_id: story.id });
}

/** Entry point: seed lookups, then every story sequentially in M0 order. */
async function main(): Promise<void> {
  log("seed_started", { project_root: PROJECT_ROOT });
  const client = buildServiceClient();
  const { STORIES, SEGMENTS } = await loadPrototypeData();

  await seedLookups(client, SEGMENTS);
  // Sequential, 1-indexed: story `s{N}` ↔ `digest-{N}`.
  for (let index = 0; index < STORIES.length; index += 1) {
    await seedStory(client, STORIES[index], index + 1);
  }

  log("seed_completed", { total_stories: STORIES.length });
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  log("seed_failed", {
    error_message: message,
    fix_suggestion: "Check SUPABASE env vars, that migrations 0001+0002 are applied, and that source fixture paths exist.",
  });
  process.exit(1);
});
