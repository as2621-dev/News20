/**
 * Deterministic puppeteer regression for issue #37 — the voice sheet's honest
 * connection states, in a real browser through the REAL client chain
 * (BlipReel → AskSheet → AskSheetVoice → useGeminiLive):
 *
 *   1. sheet-open shows CONNECTING (never LISTENING) while the token mint +
 *      WSS setup are in flight, and the session pre-warms at sheet-open;
 *   2. LISTENING appears ONLY after `{setupComplete}` arrives;
 *   3. a token-mint failure lands on a visible error WITH a "Try again" retry
 *      (not an infinite CONNECTING), and retry recovers to a live session.
 *
 * Determinism: Supabase is fully intercepted (seeded session + scripted feed —
 * mirrors tests/e2e/reelSections.e2e.mjs); the worker token mint is intercepted
 * by URL path (host-agnostic); the Gemini Live WSS is replaced by an in-page
 * fake that answers `setup` with `{setupComplete}` after a visible delay —
 * real WebSockets (e.g. Next dev HMR) pass through untouched. Mic comes from
 * Chrome's fake-device flags. No live backend, no API key.
 *
 * NOT wired into `npm test` (needs a browser + a dev server). Run standalone:
 *
 *   npm i --no-save puppeteer   # one-time; intentionally NOT in package.json
 *   PORT=3120 npm run dev &
 *   E2E_BASE_URL=http://localhost:3120 node tests/e2e/voiceSheetStates.e2e.mjs
 */

import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import puppeteer from "puppeteer";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3120";
const SUPABASE_STORAGE_KEY = "sb-cerfennlcgureyifraqy-auth-token";
const SCREENSHOT_DIR = ".agents/debug/issue-37";
const SETUP_DELAY_MS = 1500; // fake WSS: keep CONNECTING observable

/** One PostgREST story embed (stories ⋈ segments ⋈ digests ⋈ captions). */
const storyEmbed = (storyId, headline) => ({
  story_id: storyId,
  story_headline: headline,
  story_segment_slug: "tech",
  story_detail_category: null,
  segments: { segment_label: "Tech", segment_accent_hex: "#22D3EE" },
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

const DAILY_FEED_ROWS = [
  {
    feed_position: 1,
    feed_slot_kind: "interest",
    feed_matched_interest_id: null,
    feed_section_label: null,
    feed_section_interest_id: null,
    feed_fallback_source_level: null,
    matched_interest: null,
    stories: storyEmbed("s-1", "Chip fabs race to 2nm"),
  },
];

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

mkdirSync(SCREENSHOT_DIR, { recursive: true });

const browser = await puppeteer.launch({
  headless: "new",
  args: [
    "--no-sandbox",
    // Fake mic: permission auto-granted + a synthetic input device, so the
    // post-setupComplete mic start succeeds without hardware.
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
  ],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 844 });

  // Seed a non-expired session + the mic-granted flag (skips the permission CTA
  // so the sheet auto-connects at open — the pre-warm path under test).
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
    window.localStorage.setItem("blip-voice-granted", "1");
  }, SUPABASE_STORAGE_KEY);

  // Replace ONLY the Gemini Live WSS with a scripted fake: replies to `setup`
  // with `{setupComplete}` after a visible delay. Everything else (HMR) passes
  // through to the real WebSocket.
  await page.evaluateOnNewDocument((setupDelayMs) => {
    const RealWebSocket = window.WebSocket;
    class FakeGeminiLiveSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      constructor(url) {
        this.url = url;
        this.readyState = 0;
        this.onopen = null;
        this.onmessage = null;
        this.onerror = null;
        this.onclose = null;
        setTimeout(() => {
          this.readyState = 1;
          if (this.onopen) this.onopen();
        }, 50);
      }
      send(raw) {
        try {
          const frame = JSON.parse(raw);
          if (frame.setup) {
            setTimeout(() => {
              if (this.onmessage) this.onmessage({ data: JSON.stringify({ setupComplete: {} }) });
            }, setupDelayMs);
          }
        } catch {
          // realtimeInput audio frames etc. — ignore.
        }
      }
      close() {
        this.readyState = 3;
        if (this.onclose) this.onclose({ code: 1000, reason: "" });
      }
    }
    window.WebSocket = new Proxy(RealWebSocket, {
      construct(target, args) {
        const [url] = args;
        if (typeof url === "string" && url.includes("generativelanguage.googleapis.com")) {
          return new FakeGeminiLiveSocket(url);
        }
        return new target(...args);
      },
    });
  }, SETUP_DELAY_MS);

  // Intercepts: Supabase scripted; the token mint scripted + failure-togglable.
  let mintMode = "ok"; // "ok" | "fail"
  let mintRequestCount = 0;
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
    if (url.includes("/api/voice/live-token")) {
      mintRequestCount += 1;
      if (mintMode === "fail") {
        return respondJson(req, JSON.stringify({ error: "e2e forced mint failure" }), 503);
      }
      return respondJson(req, JSON.stringify({ ephemeral_token_name: "auth_tokens/e2e-token" }));
    }
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
  await page.waitForSelector('[data-story-index="0"]', { timeout: 15000 });
  await wait(500);

  const stateLabel = () => page.evaluate(() => document.querySelector(".vs-state")?.textContent ?? null);
  // Reason: DOM-dispatched clicks — puppeteer's coordinate click misses the
  // reel's layered buttons (gesture layer overlaps the hit target). Retried
  // until the sheet actually opens: right after a dev-server compile the first
  // click can land before React has attached listeners.
  const openVoiceSheet = async () => {
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await page.evaluate(() => {
        document.querySelector('button[aria-label="Ask with your voice"]')?.click();
      });
      await wait(300);
      const isOpen = await page.evaluate(() => document.querySelector(".sheet")?.classList.contains("on") ?? false);
      if (isOpen) return;
    }
    throw new Error("voice sheet did not open after 10 click attempts");
  };
  const closeVoiceSheet = async () => {
    await page.evaluate(() => {
      document.querySelector('button[aria-label="Close"]')?.click();
    });
    await wait(400);
  };

  // ── Scenario A: happy path — CONNECTING first, LISTENING only after setup ──
  const mintCountBeforeOpen = mintRequestCount;
  await openVoiceSheet();
  await page.waitForFunction(
    () => document.querySelector(".vs-state")?.textContent === "CONNECTING",
    { timeout: 5000 },
  );
  assert.equal(await stateLabel(), "CONNECTING", "sheet-open must show CONNECTING before the session is ready");
  await page.screenshot({ path: `${SCREENSHOT_DIR}/a1-connecting.png` });

  // Pre-warm: the mint fired at sheet-open, no tap needed.
  assert.ok(mintRequestCount > mintCountBeforeOpen, "the token mint must fire at sheet-open (pre-warm)");

  // While the fake WSS holds setupComplete back, the label must NOT be LISTENING.
  await wait(SETUP_DELAY_MS / 3);
  assert.equal(await stateLabel(), "CONNECTING", "LISTENING must not appear before setupComplete");

  await page.waitForFunction(
    () => document.querySelector(".vs-state")?.textContent === "LISTENING",
    { timeout: SETUP_DELAY_MS + 5000 },
  );
  await page.screenshot({ path: `${SCREENSHOT_DIR}/a2-listening.png` });
  console.log("scenario A (connecting → listening): PASS");

  await closeVoiceSheet();

  // ── Scenario B: mint failure → visible error + retry → recovery ───────────
  mintMode = "fail";
  await openVoiceSheet();
  await page.waitForSelector("[data-testid='voice-retry']", { timeout: 15000 });
  const errorText = await page.evaluate(() => document.querySelector(".sheet-body")?.textContent ?? "");
  assert.ok(
    errorText.includes("Voice isn't available right now."),
    `mint failure must show the error copy (got: "${errorText}")`,
  );
  assert.equal(await stateLabel(), null, "the orb must not render in the error state");
  await page.screenshot({ path: `${SCREENSHOT_DIR}/b1-error-retry.png` });

  mintMode = "ok";
  await page.click("[data-testid='voice-retry']");
  await page.waitForFunction(
    () => document.querySelector(".vs-state")?.textContent === "CONNECTING",
    { timeout: 5000 },
  );
  await page.waitForFunction(
    () => document.querySelector(".vs-state")?.textContent === "LISTENING",
    { timeout: SETUP_DELAY_MS + 5000 },
  );
  await page.screenshot({ path: `${SCREENSHOT_DIR}/b2-retry-recovered.png` });
  console.log("scenario B (mint failure → error+retry → recovered): PASS");

  console.log("voice sheet states e2e: PASS");
} finally {
  await browser.close();
}
