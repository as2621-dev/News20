/**
 * Deterministic puppeteer regression for issue #9 — the "Rebuild my feed" entry
 * point in Settings. The committed browser lock for what a jsdom component test
 * can't honestly prove: the REAL client chain root gate → reel → wordmark →
 * Settings tab → RebuildFeedFlow → InterviewChat (fresh start) → terminal
 * confirm → replace-persist requests, in a real browser.
 *
 * Determinism: every Supabase request is intercepted (session seeded into
 * localStorage; users probe → onboarded; daily_feeds → one scripted row; the
 * mint RPC, profile upsert, stale DELETE and traits upsert are stubbed and
 * CAPTURED so the replace semantics are asserted at the wire), and the worker
 * turn endpoint is served scripted turns keyed on conversation length. No live
 * backend, no auth.
 *
 * NOT wired into `npm test` (vitest) — it needs a browser + a running dev server.
 * Run it standalone in the e2e lane (mirrors tests/e2e/reelSections.e2e.mjs):
 *
 *   npm i --no-save puppeteer   # one-time; intentionally NOT in package.json
 *   PORT=3130 npm run dev &
 *   E2E_BASE_URL=http://localhost:3130 node tests/e2e/rebuildFeed.e2e.mjs
 *
 * Rule 9 — each step asserts a concrete user-visible outcome (the Settings row,
 * the FIRST interview question with no resume prompt, the confirm list, the
 * "Profile rebuilt" done state) plus the wire-level replace contract (upsert of
 * the new row, a user-scoped not-in DELETE of stale rows, and NO write to
 * users.user_onboarded_at) and fails loudly if any is absent.
 */

import assert from "node:assert/strict";
import puppeteer from "puppeteer";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3130";
const SUPABASE_STORAGE_KEY = "sb-cerfennlcgureyifraqy-auth-token";
const MINTED_LEAF_ID = "11111111-1111-1111-1111-111111111111";

/** One PostgREST story embed (stories ⋈ segments ⋈ digests ⋈ captions) — minimal. */
const STORY_EMBED = {
  story_id: "s-1",
  story_headline: "A story to render the reel",
  story_segment_slug: "sport",
  story_detail_category: null,
  segments: { segment_label: "Sport", segment_accent_hex: "#F59E0B" },
  digests: [
    {
      digest_id: "s-1-digest",
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
};

const DAILY_FEED_ROWS = [
  {
    feed_position: 1,
    feed_slot_kind: "interest",
    feed_matched_interest_id: null,
    feed_section_label: null,
    feed_section_interest_id: null,
    feed_fallback_source_level: null,
    matched_interest: null,
    stories: STORY_EMBED,
  },
];

const question = (turn_index, question_text, options) => ({
  response_kind: "question",
  turn_index,
  question_text,
  bubbles: [
    ...options.map((bubble_label) => ({ bubble_label, bubble_kind: "option" })),
    { bubble_label: "not really / skip", bubble_kind: "skip" },
    { bubble_label: "something else — type it", bubble_kind: "type_your_own" },
  ],
});

const Q0 = question(0, "What do you follow most closely?", ["Sport", "Tech"]);
const TERMINAL = {
  response_kind: "terminal",
  turn_index: 1,
  micro_interests: [
    {
      display_label: "IPL — auctions & transfers",
      canonical_slug: "sport.cricket.ipl",
      ladder: ["sport", "sport.cricket"],
      search_anchor_terms: ["IPL auction", "player transfer"],
      strict: false,
    },
  ],
  roots_only_fallback: false,
};

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox"] });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 844 });

  // Seed a stale ONBOARDING transcript too: the rebuild entry must force a fresh
  // start (no "Pick up where you left off?" prompt) — a regression here surfaces below.
  await page.evaluateOnNewDocument(
    (storageKey, transcriptJson) => {
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
      window.localStorage.setItem("n20-interview-session", transcriptJson);
    },
    SUPABASE_STORAGE_KEY,
    JSON.stringify({
      version: 1,
      conversation_state: [
        { question_text: "stale?", bubbles_offered: ["Old"], bubbles_tapped: ["Old"], free_text_entered: null },
      ],
      saved_at_ms: Date.now(),
    }),
  );

  // Captured write traffic — the wire-level replace contract is asserted on these.
  const profileWrites = []; // { method, url, body }
  let usersWriteSeen = false;

  const CORS_HEADERS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-allow-methods": "*",
  };
  const respondJson = (req, body, status = 200) =>
    req.respond({ status, contentType: "application/json", headers: CORS_HEADERS, body });

  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    // Worker turn endpoint (host-agnostic): scripted turns keyed on conversation length.
    if (url.includes("/api/interview/turn")) {
      const conversation = JSON.parse(req.postData() ?? "{}").conversation_state ?? [];
      return respondJson(req, JSON.stringify(conversation.length === 0 ? Q0 : TERMINAL));
    }
    if (!url.includes(".supabase.co/")) {
      return req.continue();
    }
    if (req.method() === "OPTIONS") {
      return req.respond({ status: 204, headers: CORS_HEADERS, body: "" });
    }
    if (url.includes("/rest/v1/rpc/mint_interest_ladder")) {
      return respondJson(req, JSON.stringify(MINTED_LEAF_ID));
    }
    if (url.includes("/rest/v1/user_interest_profile")) {
      profileWrites.push({ method: req.method(), url, body: req.postData() ?? "" });
      return respondJson(req, "[]", req.method() === "DELETE" ? 200 : 201);
    }
    if (url.includes("/rest/v1/user_interest_traits")) {
      return respondJson(req, "[]", 201);
    }
    if (url.includes("/rest/v1/users")) {
      if (req.method() !== "GET" && req.method() !== "HEAD") {
        usersWriteSeen = true; // user_onboarded_at must stay untouched — flagged below.
        return respondJson(req, "[]");
      }
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

  /** Click the first button whose text contains `needle` (fails loudly if absent). */
  const clickByText = async (needle) => {
    const clicked = await page.evaluate((text) => {
      const button = Array.from(document.querySelectorAll("button")).find((b) => b.textContent?.includes(text));
      if (!button) {
        return false;
      }
      button.click();
      return true;
    }, needle);
    assert.ok(clicked, `button containing "${needle}" not found`);
  };

  /** Poll until the page body contains `needle` (or fail after ~10s). */
  const waitForText = async (needle) => {
    await page.waitForFunction((text) => document.body.textContent?.includes(text), { timeout: 10000 }, needle);
  };

  // 1. Signed-in, onboarded user lands on the reel.
  await page.goto(`${BASE}/`, { waitUntil: "networkidle2" });
  await page.waitForSelector('[data-story-index="0"]', { timeout: 15000 });

  // 2. The wordmark opens the library at Settings; the rebuild row is there.
  //    (Programmatic click: the TapToStart audio-unlock overlay sits above the
  //    wordmark until first tap, so a coordinate click would hit the overlay.)
  await page.evaluate(() => {
    document.querySelector('button[aria-label="Open account"]')?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await waitForText("Rebuild my feed");

  // 3. Launch: the interview starts FRESH — first question, and NO resume prompt
  //    despite the seeded stale transcript.
  await clickByText("Rebuild my feed");
  await waitForText("What do you follow most closely?");
  const bodyText = await page.evaluate(() => document.body.textContent ?? "");
  assert.ok(!bodyText.includes("Pick up where you left off?"), "rebuild must never offer the stale-transcript resume");
  assert.equal(profileWrites.length, 0, "launching the rebuild must persist nothing");

  // 4. Answer → terminal confirm in the user's own words → confirm.
  await clickByText("Sport");
  await waitForText("IPL — auctions & transfers");
  await clickByText("Looks good");

  // 5. Done state: the profile was replaced and the user is told what happens next.
  await waitForText("Profile rebuilt");
  await wait(200);

  // 6. Wire-level replace contract (fails if old and new profiles blend):
  //    a) the new row was upserted with the minted leaf id;
  const upsert = profileWrites.find((w) => w.method === "POST");
  assert.ok(upsert, "the new profile row must be upserted");
  assert.ok(upsert.body.includes(MINTED_LEAF_ID), "the upserted row must carry the minted leaf id");
  //    b) stale rows were deleted, scoped to THIS user, keeping the new set;
  const staleDelete = profileWrites.find((w) => w.method === "DELETE");
  assert.ok(staleDelete, "stale profile rows must be deleted (replace, not blend)");
  assert.ok(staleDelete.url.includes("profile_user_id=eq.e2e-user"), "the stale delete must be scoped to the user");
  assert.ok(
    decodeURIComponent(staleDelete.url).includes(`profile_interest_id=not.in.(${MINTED_LEAF_ID})`),
    "the stale delete must keep the newly confirmed rows",
  );
  //    c) the upsert landed BEFORE the delete (no zero-row window);
  assert.ok(
    profileWrites.findIndex((w) => w.method === "POST") < profileWrites.findIndex((w) => w.method === "DELETE"),
    "new rows must land before stale rows are deleted",
  );
  //    d) first-run side effects did NOT re-fire (user_onboarded_at untouched).
  assert.equal(usersWriteSeen, false, "the rebuild must never write to users (user_onboarded_at stays set)");

  // 7. Done returns to Settings (the flow overlay closes).
  await clickByText("Done");
  await waitForText("Rebuild my feed");

  console.log("rebuild feed e2e: PASS");
} finally {
  await browser.close();
}
