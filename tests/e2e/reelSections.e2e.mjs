/**
 * Deterministic puppeteer regression for FSR slice #8 — reel section rendering
 * (user-vocabulary headers + honest fallback labels), the committed browser lock
 * for what a jsdom component test can't honestly prove: the REAL client chain
 * root gate → getReelFeed → getDailyFeed (PostgREST mapping incl. the
 * matched-interest embed) → BlipReel → ReelStage → ReelSectionHeader, in a real
 * browser.
 *
 * Determinism: every Supabase request is intercepted — a non-expired session is
 * seeded into localStorage, the `users` onboarded probe and the `daily_feeds`
 * read are served scripted payloads (a persona feed with two direct fills, one
 * climbed slot, one legacy row, one followed-source slot, one beyond-bubble
 * slot). No live backend, no auth.
 *
 * NOT wired into `npm test` (vitest) — it needs a browser + a running dev server.
 * Run it standalone in the e2e lane (mirrors tests/e2e/interviewChat.e2e.mjs):
 *
 *   npm i --no-save puppeteer   # one-time; intentionally NOT in package.json
 *   PORT=3120 npm run dev &
 *   E2E_BASE_URL=http://localhost:3120 node tests/e2e/reelSections.e2e.mjs
 *
 * Rule 9 — each step asserts a concrete user-visible outcome (the header text in
 * the user's OWN words with its slot count, the honesty line naming the level
 * actually filled from, the legacy/source rows keeping today's category chip)
 * and fails loudly if it is absent.
 */

import assert from "node:assert/strict";
import puppeteer from "puppeteer";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3120";
const SUPABASE_STORAGE_KEY = "sb-cerfennlcgureyifraqy-auth-token";

/** One PostgREST story embed (stories ⋈ segments ⋈ digests ⋈ captions). */
const storyEmbed = (storyId, headline, segmentSlug, segmentLabel, accentHex) => ({
  story_id: storyId,
  story_headline: headline,
  story_segment_slug: segmentSlug,
  story_detail_category: null,
  segments: { segment_label: segmentLabel, segment_accent_hex: accentHex },
  digests: [
    {
      digest_id: `${storyId}-digest`,
      digest_audio_url: "/fixtures/audio/digest-1.mp3",
      digest_duration_ms: 5000,
      digest_ambient_poster_url: null,
      digest_is_current: true,
      caption_sentences: [
        {
          sentence_index: 0,
          anchor_speaker: "ALEX",
          sentence_text: "Hello world.",
          highlight_keyword: "world.",
          sentence_start_ms: 0,
          sentence_end_ms: 1000,
          word_tokens: [
            { word_text: "Hello", is_highlight: false, start_ms: 0, end_ms: 500 },
            { word_text: "world.", is_highlight: true, start_ms: 500, end_ms: 1000 },
          ],
        },
      ],
    },
  ],
});

/**
 * The persona `daily_feeds` payload: 2 direct IPL fills + 1 climbed IPL slot
 * (level 1, filled from Cricket) + 1 legacy row (no section metadata) + 1
 * followed-source slot + 1 beyond-bubble slot. Category label is "Sport" on the
 * IPL slots ON PURPOSE: if the client derived headers from categories, the
 * header assertion below would read "Sport" and fail.
 */
const DAILY_FEED_ROWS = [
  {
    feed_position: 1,
    feed_slot_kind: "interest",
    feed_matched_interest_id: "int-ipl",
    feed_section_label: "IPL",
    feed_section_interest_id: "int-ipl",
    feed_fallback_source_level: 0,
    matched_interest: { interest_label: "IPL" },
    stories: storyEmbed("s-ipl-1", "IPL auction shocker", "sport", "Sport", "#F59E0B"),
  },
  {
    feed_position: 2,
    feed_slot_kind: "interest",
    feed_matched_interest_id: "int-ipl",
    feed_section_label: "IPL",
    feed_section_interest_id: "int-ipl",
    feed_fallback_source_level: 0,
    matched_interest: { interest_label: "IPL" },
    stories: storyEmbed("s-ipl-2", "Franchise trades a captain", "sport", "Sport", "#F59E0B"),
  },
  {
    feed_position: 3,
    feed_slot_kind: "interest",
    feed_matched_interest_id: "int-cricket",
    feed_section_label: "IPL",
    feed_section_interest_id: "int-ipl",
    feed_fallback_source_level: 1,
    matched_interest: { interest_label: "Cricket" },
    stories: storyEmbed("s-climbed", "Test series turns on day five", "sport", "Sport", "#F59E0B"),
  },
  {
    feed_position: 4,
    feed_slot_kind: "interest",
    feed_matched_interest_id: null,
    feed_section_label: null,
    feed_section_interest_id: null,
    feed_fallback_source_level: null,
    matched_interest: null,
    stories: storyEmbed("s-legacy", "Chip fabs race to 2nm", "tech", "Tech", "#22D3EE"),
  },
  {
    feed_position: 5,
    feed_slot_kind: "source",
    feed_matched_interest_id: null,
    feed_section_label: null,
    feed_section_interest_id: null,
    feed_fallback_source_level: 0,
    matched_interest: null,
    stories: storyEmbed("s-source", "A creator's deep dive", "arts", "Arts", "#E8B7BC"),
  },
  {
    feed_position: 6,
    feed_slot_kind: "interest",
    feed_matched_interest_id: null,
    feed_section_label: "Beyond your bubble",
    feed_section_interest_id: null,
    feed_fallback_source_level: 0,
    matched_interest: null,
    stories: storyEmbed("s-bb", "A story from outside your lanes", "environment", "Environment", "#34D399"),
  },
];

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox"] });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 844 });

  // Seed a non-expired session so getCurrentSession() resolves offline.
  await page.evaluateOnNewDocument((storageKey) => {
    window.localStorage.setItem(
      storageKey,
      JSON.stringify({
        access_token: "e2e-access-token",
        refresh_token: "e2e-refresh-token",
        token_type: "bearer",
        expires_in: 31536000,
        expires_at: Math.floor(Date.now() / 1000) + 31536000,
        user: { id: "e2e-user", aud: "authenticated", role: "authenticated", email: "e2e@example.com" },
      }),
    );
  }, SUPABASE_STORAGE_KEY);

  // Intercept every Supabase call: onboarded users probe → onboarded; daily_feeds →
  // the persona rows; any other PostgREST read → empty (follows hydration etc.).
  // The Supabase host is cross-origin from localhost, so every stubbed response —
  // including the OPTIONS preflight — must carry permissive CORS headers or the
  // browser discards it and the reel never loads.
  const CORS_HEADERS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-allow-methods": "*",
  };
  const respondJson = (req, body) =>
    req.respond({ status: 200, contentType: "application/json", headers: CORS_HEADERS, body });
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    if (!url.includes(".supabase.co/")) {
      return req.continue();
    }
    if (req.method() === "OPTIONS") {
      return req.respond({ status: 204, headers: CORS_HEADERS, body: "" });
    }
    if (url.includes("/rest/v1/users")) {
      // maybeSingle() sends Accept: application/vnd.pgrst.object+json → one object.
      return respondJson(req, JSON.stringify({ user_onboarded_at: "2026-01-01T00:00:00Z" }));
    }
    if (url.includes("/rest/v1/daily_feeds")) {
      return respondJson(req, JSON.stringify(DAILY_FEED_ROWS));
    }
    if (url.includes("/rest/v1/")) {
      return respondJson(req, "[]");
    }
    // Auth endpoints shouldn't be hit (session is local + non-expired); refuse loudly.
    return req.respond({ status: 500, contentType: "application/json", headers: CORS_HEADERS, body: "{}" });
  });

  await page.goto(`${BASE}/`, { waitUntil: "networkidle2" });
  // The gate resolves client-side, then the feed loads; poll for the reel stages.
  await page.waitForSelector('[data-story-index="5"]', { timeout: 15000 });
  await wait(500);

  /** Read one slot's chip text + whether its honesty line is present. */
  const slotChrome = (storyIndex) =>
    page.evaluate((index) => {
      const slot = document.querySelector(`[data-story-index="${index}"]`);
      return {
        chip: slot?.querySelector(".seg-chip")?.textContent?.trim() ?? null,
        fallback: slot?.querySelector('[data-testid="section-fallback"]')?.textContent ?? null,
      };
    }, storyIndex);

  // 1. Direct fills: the header is the USER'S label + slot count, not the category.
  const direct = await slotChrome(0);
  assert.equal(direct.chip, "IPL — 3", "direct fill should render the user-vocabulary header with slot count");
  assert.equal(direct.fallback, null, "a direct fill must carry NO fallback label");

  // 2. The climbed slot carries the honesty line naming the level actually filled from.
  const climbed = await slotChrome(2);
  assert.equal(climbed.chip, "IPL — 3", "climbed slot stays inside its section header");
  assert.equal(
    climbed.fallback,
    "Nothing new in IPL today — here's Cricket",
    "climbed slot must render the honest fallback label",
  );

  // 3. Legacy row (no metadata): today's category chip, no header invention, no crash.
  const legacy = await slotChrome(3);
  assert.equal(legacy.chip, "Tech", "legacy row should keep the plain category label");
  assert.equal(legacy.fallback, null, "legacy row must carry no fallback label");

  // 4. Followed-source slot: existing lead-slot treatment untouched.
  const sourceSlot = await slotChrome(4);
  assert.equal(sourceSlot.chip, "Arts", "source slot should keep its existing category treatment");

  // 5. Beyond-bubble renders under its reserved label with its slot count.
  const beyond = await slotChrome(5);
  assert.equal(beyond.chip, "Beyond your bubble — 1", "beyond-bubble should render its reserved section header");

  console.log("reel sections e2e: PASS");
} finally {
  await browser.close();
}
