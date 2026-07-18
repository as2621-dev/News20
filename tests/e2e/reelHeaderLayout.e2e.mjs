/**
 * Deterministic puppeteer regression for RC6 — the reel header's section chip and
 * headline must occupy SEPARATE lines. The committed browser lock for what a jsdom
 * test cannot honestly prove: real layout. jsdom has no layout engine, so a unit
 * test can only assert class names — and a class name survives the exact bug this
 * guards (chip `inline-flex` + headline button `inline-block` concatenating into
 * one run-on line, "Wildcard Language Magazine").
 *
 * Every assertion is on GEOMETRY (bounding-box coordinates), never on a selector's
 * presence, so it fails for the right reason if the layout regresses to inline.
 *
 * Determinism: mirrors tests/e2e/reelSections.e2e.mjs — a non-expired session is
 * seeded into localStorage and every Supabase request is intercepted with scripted
 * payloads (CORS headers on every stub, including the OPTIONS preflight, or Chrome
 * silently discards them). No live backend, no auth.
 *
 * NOT wired into `npm test` (vitest) — it needs a browser + a running dev server.
 * Run it standalone in the e2e lane:
 *
 *   npm i --no-save puppeteer   # one-time; intentionally NOT in package.json
 *   PORT=3121 npm run dev &
 *   E2E_BASE_URL=http://localhost:3121 node tests/e2e/reelHeaderLayout.e2e.mjs
 *
 * Rule 9 — this encodes WHY the layout matters: a reader must be able to tell the
 * category chip from the headline. If they share a line the header reads as one
 * garbled sentence, which is the product defect RC6 names.
 */

import assert from "node:assert/strict";
import puppeteer from "puppeteer";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3121";
const SUPABASE_STORAGE_KEY = "sb-cerfennlcgureyifraqy-auth-token";

/**
 * A headline long enough to wrap to multiple lines in the 390px-wide reel column.
 * The edge case under test: wrapping must happen INSIDE the headline's own block,
 * never by pulling the chip inline with the first wrapped line.
 */
const LONG_HEADLINE =
  "Regulators open a sweeping inquiry into semiconductor export controls as allied governments weigh their own restrictions";

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

/** Slot 0 = short headline (happy path). Slot 1 = long wrapping headline (edge case). */
const DAILY_FEED_ROWS = [
  {
    feed_position: 1,
    feed_slot_kind: "interest",
    feed_matched_interest_id: "int-ipl",
    feed_section_label: "IPL",
    feed_section_interest_id: "int-ipl",
    feed_fallback_source_level: 0,
    matched_interest: { interest_label: "IPL" },
    stories: storyEmbed("s-short", "IPL auction shocker", "sport", "Sport", "#F59E0B"),
  },
  {
    feed_position: 2,
    feed_slot_kind: "interest",
    feed_matched_interest_id: null,
    feed_section_label: null,
    feed_section_interest_id: null,
    feed_fallback_source_level: null,
    matched_interest: null,
    stories: storyEmbed("s-long", LONG_HEADLINE, "tech", "Tech", "#22D3EE"),
  },
];

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox"] });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 844 });

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
      return respondJson(req, JSON.stringify({ user_onboarded_at: "2026-01-01T00:00:00Z" }));
    }
    if (url.includes("/rest/v1/daily_feeds")) {
      return respondJson(req, JSON.stringify(DAILY_FEED_ROWS));
    }
    if (url.includes("/rest/v1/")) {
      return respondJson(req, "[]");
    }
    return req.respond({ status: 500, contentType: "application/json", headers: CORS_HEADERS, body: "{}" });
  });

  await page.goto(`${BASE}/`, { waitUntil: "networkidle2" });
  await page.waitForSelector('[data-story-index="1"]', { timeout: 15000 });
  await wait(500);

  /**
   * Measure one slot's header geometry: the chip's box, the headline's box, and the
   * headline's single-line height (from its computed line-height) so wrapping can be
   * detected without hard-coding pixel values.
   */
  const headerGeometry = (storyIndex) =>
    page.evaluate((index) => {
      const slot = document.querySelector(`[data-story-index="${index}"]`);
      const chipEl = slot?.querySelector(".seg-chip");
      const headlineEl = slot?.querySelector(".headline");
      if (!chipEl || !headlineEl) {
        return null;
      }
      const chipRect = chipEl.getBoundingClientRect();
      const headlineRect = headlineEl.getBoundingClientRect();
      return {
        chip: { bottom: chipRect.bottom, left: chipRect.left },
        headline: { top: headlineRect.top, left: headlineRect.left, height: headlineRect.height },
        // Unitless `line-height: 1.12` — Blink resolves getComputedStyle to px, which is
        // what the wrap threshold below compares against. Chrome-only, as puppeteer is.
        headlineLineHeight: Number.parseFloat(window.getComputedStyle(headlineEl).lineHeight),
      };
    }, storyIndex);

  /**
   * The core invariant: the headline's box starts at or below the chip's box bottom,
   * i.e. their vertical extents do not overlap. Inline concatenation puts them on a
   * shared baseline, so the boxes overlap and this fails.
   */
  const assertStacked = (geo, label) => {
    assert.ok(geo !== null, `${label}: reel header should render both a chip and a headline`);
    assert.ok(
      geo.headline.top >= geo.chip.bottom,
      `${label}: headline top (${geo.headline.top}) must be at or below chip bottom (${geo.chip.bottom}) — ` +
        "chip and headline are sharing a line, so the header reads as one run-on sentence",
    );
  };

  // 1. Happy path — short headline sits on its own line below the chip.
  const short = await headerGeometry(0);
  assertStacked(short, "short headline");
  assert.ok(
    short.headline.left <= short.chip.left,
    `short headline: headline should start at the header's left edge (${short.headline.left}), not be ` +
      `indented past the chip (${short.chip.left}) as inline flow would do`,
  );

  // 2. Edge case — a long headline wraps inside its OWN block, chip stays above it.
  const long = await headerGeometry(1);
  assertStacked(long, "long headline");
  assert.ok(
    long.headline.height > long.headlineLineHeight * 1.5,
    `long headline: expected the headline to wrap to multiple lines (height ${long.headline.height} vs ` +
      `line-height ${long.headlineLineHeight}) — the wrap edge case is not actually being exercised`,
  );

  console.log("reel header layout e2e: PASS");
} finally {
  await browser.close();
}
