/**
 * Deterministic puppeteer walkthrough for issue #40 — the chat UX contract on
 * both ask surfaces, in a real browser through the REAL client chain
 * (BlipReel → AskSheet → AskSheetType/AskSheetVoice):
 *
 *   A. typed multi-turn: turns render oldest→newest, the thread autoscrolls to
 *      the newest bubble, and history accumulates (never resets per question);
 *   B. long history: the thread is genuinely scrollable, and new content does
 *      NOT yank a user who scrolled up (pin-to-bottom only when at the bottom);
 *   C. failed answer: a network failure keeps the question visible with a
 *      retryable error card (not a wiped thread, not a fake refusal), and
 *      retry recovers the answer;
 *   D. voice: BOTH transcripts roll live as partials and accumulate across
 *      turns; switching voice → typed → voice keeps each surface's own
 *      in-session history (no wipe, no cross-contamination).
 *
 * Determinism: Supabase is fully intercepted (seeded session + scripted feed —
 * mirrors tests/e2e/voiceSheetStates.e2e.mjs); the Q&A question endpoint and
 * the live-token mint are intercepted by URL path; the Gemini Live WSS is an
 * in-page fake that answers `setup` with `{setupComplete}` and exposes a
 * transcript-emitter hook so the test streams scripted input/output
 * transcription deltas. Mic comes from Chrome's fake-device flags. No live
 * backend, no API key.
 *
 * NOT wired into `npm test` (needs a browser + a dev server). Run standalone:
 *
 *   npm i --no-save puppeteer   # one-time; intentionally NOT in package.json
 *   PORT=3130 npm run dev &
 *   E2E_BASE_URL=http://localhost:3130 node tests/e2e/chatUxContract.e2e.mjs
 */

import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import puppeteer from "puppeteer";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3130";
const SUPABASE_STORAGE_KEY = "sb-cerfennlcgureyifraqy-auth-token";
const SCREENSHOT_DIR = process.env.E2E_SCREENSHOT_DIR ?? ".agents/debug/issue-40";
// Story.digest_id = stories.story_id (supabaseFeed.ts maps digest_id: row.story_id).
const STORY_DIGEST_ID = "s-1";

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
  args: ["--no-sandbox", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 844 });

  // Seed a non-expired session + the mic-granted flag.
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

  // Fake Gemini Live WSS: answers `setup` with `{setupComplete}` and exposes
  // window.__e2eEmitTranscript so the test streams scripted transcript deltas.
  await page.evaluateOnNewDocument(() => {
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
        window.__e2eLiveSocket = this;
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
            }, 200);
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
    window.__e2eEmitTranscript = (role, text) => {
      const socket = window.__e2eLiveSocket;
      if (!socket || !socket.onmessage) return false;
      const key = role === "user" ? "inputTranscription" : "outputTranscription";
      socket.onmessage({ data: JSON.stringify({ serverContent: { [key]: { text } } }) });
      return true;
    };
    window.WebSocket = new Proxy(RealWebSocket, {
      construct(target, args) {
        const [url] = args;
        if (typeof url === "string" && url.includes("generativelanguage.googleapis.com")) {
          return new FakeGeminiLiveSocket(url);
        }
        return new target(...args);
      },
    });
  });

  // Intercepts: Q&A question endpoint (scripted answers, failure-togglable),
  // token mint, Supabase.
  let questionMode = "ok"; // "ok" | "fail"
  let answerIndex = 0;
  const SCRIPTED_ANSWERS = [
    "Because of the chip race.",
    "Margins are around 53 percent.",
    "Mostly automakers are affected.",
    "Recovered after the retry.",
  ];
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
    if (url.includes("/api/story/") && url.includes("/question")) {
      if (req.method() === "OPTIONS") {
        return req.respond({ status: 204, headers: CORS_HEADERS, body: "" });
      }
      if (questionMode === "fail") {
        return req.abort("failed");
      }
      const answer_text = SCRIPTED_ANSWERS[Math.min(answerIndex, SCRIPTED_ANSWERS.length - 1)];
      answerIndex += 1;
      return respondJson(req, JSON.stringify({ answer_text, answer_citations: [], answer_is_grounded: true }));
    }
    if (url.includes("/api/voice/live-token")) {
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

  // Reason: DOM-dispatched clicks — puppeteer's coordinate click misses the
  // reel's layered buttons; retried until the sheet actually opens.
  const openSheet = async (buttonAriaLabel) => {
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await page.evaluate((label) => {
        document.querySelector(`button[aria-label="${label}"]`)?.click();
      }, buttonAriaLabel);
      await wait(300);
      const isOpen = await page.evaluate(() => document.querySelector(".sheet")?.classList.contains("on") ?? false);
      if (isOpen) return;
    }
    throw new Error(`sheet did not open for "${buttonAriaLabel}" after 10 click attempts`);
  };
  const closeSheet = async () => {
    await page.evaluate(() => {
      document.querySelector('button[aria-label="Close"]')?.click();
    });
    await wait(400);
  };
  const questionBubbleTexts = () =>
    page.evaluate(() => [...document.querySelectorAll(".thread .bub-q")].map((node) => node.textContent));
  const answerBubbleTexts = () =>
    page.evaluate(() =>
      [...document.querySelectorAll(".thread .bub-a:not(.typing)")].map((node) => node.textContent),
    );
  const threadScrollInfo = () =>
    page.evaluate(() => {
      const thread = document.querySelector(".thread");
      if (!thread) return null;
      return { scrollTop: thread.scrollTop, scrollHeight: thread.scrollHeight, clientHeight: thread.clientHeight };
    });
  const submitFollowup = async (question_text) => {
    await page.evaluate((text) => {
      const followupInput = document.querySelector("form.followup input");
      const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      nativeSetter.call(followupInput, text);
      followupInput.dispatchEvent(new Event("input", { bubbles: true }));
      document.querySelector("form.followup")?.requestSubmit();
    }, question_text);
  };

  // ── Scenario A: typed multi-turn, oldest→newest, autoscroll, no reset ──────
  await openSheet("Type a question");
  await page.evaluate(() => {
    document.querySelectorAll("button.sq")[0]?.click();
  });
  await page.waitForFunction(() => document.querySelectorAll(".thread .bub-a:not(.typing)").length === 1, {
    timeout: 8000,
  });
  await submitFollowup("What about its margins?");
  await page.waitForFunction(() => document.querySelectorAll(".thread .bub-a:not(.typing)").length === 2, {
    timeout: 8000,
  });

  assert.deepEqual(
    await questionBubbleTexts(),
    ["What led to this?", "What about its margins?"],
    "questions must accumulate oldest→newest (never reset per question)",
  );
  assert.deepEqual(await answerBubbleTexts(), ["Because of the chip race.", "Margins are around 53 percent."]);
  await page.screenshot({ path: `${SCREENSHOT_DIR}/a1-typed-multi-turn.png` });
  console.log("scenario A (typed multi-turn accumulates oldest→newest): PASS");

  // ── Scenario B: long history scrolls; no yank when the user scrolled up ────
  // Seed a long per-story thread directly into the persisted store, then
  // reopen the sheet (it rehydrates on mount).
  await closeSheet();
  await page.evaluate((digestId) => {
    const longTurns = Array.from({ length: 14 }, (_, index) => ({
      question_text: `Seeded question ${index + 1}?`,
      answer: { answer_text: `Seeded answer ${index + 1}.`, answer_citations: [], answer_is_grounded: true },
    }));
    window.localStorage.setItem(
      "blip-qa-history-v1",
      JSON.stringify({
        [digestId]: { completed_turns: longTurns, draft_question_text: "", updated_at_ms: Date.now() },
      }),
    );
  }, STORY_DIGEST_ID);
  await openSheet("Type a question");
  await page.waitForFunction(() => document.querySelectorAll(".thread .bub-q").length === 14, { timeout: 8000 });

  const longThreadInfo = await threadScrollInfo();
  assert.ok(
    longThreadInfo.scrollHeight > longThreadInfo.clientHeight,
    "a long history must overflow into a scrollable thread",
  );
  assert.ok(
    longThreadInfo.scrollTop + longThreadInfo.clientHeight >= longThreadInfo.scrollHeight - 48,
    "the thread must open pinned to the bottom (newest visible)",
  );
  await page.screenshot({ path: `${SCREENSHOT_DIR}/b1-long-history-pinned.png` });

  // The user scrolls to the top to reread…
  await page.evaluate(() => {
    const thread = document.querySelector(".thread");
    thread.scrollTop = 0;
    thread.dispatchEvent(new Event("scroll"));
  });
  // …a new answer arrives…
  await submitFollowup("One more while scrolled up?");
  await page.waitForFunction(() => document.querySelectorAll(".thread .bub-a:not(.typing)").length === 15, {
    timeout: 8000,
  });
  const scrolledUpInfo = await threadScrollInfo();
  assert.ok(
    scrolledUpInfo.scrollTop < 200,
    `new content must NOT yank a scrolled-up reader to the bottom (scrollTop=${scrolledUpInfo.scrollTop})`,
  );
  await page.screenshot({ path: `${SCREENSHOT_DIR}/b2-scrolled-up-not-yanked.png` });
  console.log("scenario B (long history scrollable; scrolled-up reader not yanked): PASS");

  // ── Scenario C: failed answer → question stays + retryable error → retry ───
  // Re-pin to the bottom first (the new-turn UX), then force a network failure.
  await page.evaluate(() => {
    const thread = document.querySelector(".thread");
    thread.scrollTop = thread.scrollHeight;
    thread.dispatchEvent(new Event("scroll"));
  });
  questionMode = "fail";
  await submitFollowup("Will this fail?");
  await page.waitForSelector(".thread .ask-error", { timeout: 8000 });

  const questionsAfterFailure = await questionBubbleTexts();
  assert.ok(
    questionsAfterFailure.includes("Will this fail?"),
    "the failed question must stay visible in the thread",
  );
  assert.equal(questionsAfterFailure.length, 16, "the prior thread must survive a failed answer (not wiped)");
  assert.equal(
    await page.evaluate(() => document.querySelectorAll(".thread .refusal").length),
    0,
    "a request failure must NOT render as a grounding refusal",
  );
  await page.screenshot({ path: `${SCREENSHOT_DIR}/c1-failed-answer-retryable.png` });

  questionMode = "ok";
  await page.evaluate(() => {
    document.querySelector("button.ask-error-retry")?.click();
  });
  await page.waitForFunction(
    () => [...document.querySelectorAll(".thread .bub-a")].some((n) => n.textContent.includes("Recovered")),
    { timeout: 8000 },
  );
  assert.equal(
    await page.evaluate(() => document.querySelectorAll(".thread .ask-error").length),
    0,
    "retry success must clear the error card",
  );
  await page.screenshot({ path: `${SCREENSHOT_DIR}/c2-retry-recovered.png` });
  console.log("scenario C (failed answer keeps question + retry recovers): PASS");

  // ── Scenario D: voice dual rolling transcripts + surface-switch isolation ──
  await closeSheet();
  await openSheet("Ask with your voice");
  await page.waitForFunction(() => document.querySelector(".vs-state")?.textContent === "LISTENING", {
    timeout: 10000,
  });

  const emit = async (role, text) => {
    const emitted = await page.evaluate((r, t) => window.__e2eEmitTranscript(r, t), role, text);
    assert.ok(emitted, `transcript emit must reach the live socket (${role}: ${text})`);
    await wait(120);
  };
  // Turn 1 — both roles stream as partials.
  await emit("user", "What led");
  await emit("user", " to this?");
  await emit("model", "A chip");
  await emit("model", " shortage.");
  // Turn 2 — the thread accumulates.
  await emit("user", "Who is affected?");
  await emit("model", "Mostly automakers.");

  const voiceThread = await page.evaluate(() => ({
    userTurns: [...document.querySelectorAll(".vthread .bub-q.voiced span:last-child")].map((n) => n.textContent),
    modelTurns: [...document.querySelectorAll(".vthread .bub-a p")].map((n) => n.textContent),
  }));
  assert.deepEqual(
    voiceThread.userTurns,
    ["What led to this?", "Who is affected?"],
    "user partials must roll into full turns and accumulate",
  );
  assert.deepEqual(
    voiceThread.modelTurns,
    ["A chip shortage.", "Mostly automakers."],
    "model partials must roll into full turns and accumulate",
  );
  await page.screenshot({ path: `${SCREENSHOT_DIR}/d1-voice-dual-transcripts.png` });

  // Switch voice → typed → voice: each surface keeps its OWN history.
  await closeSheet();
  await openSheet("Type a question");
  const typedQuestionsAfterSwitch = await questionBubbleTexts();
  // 14 seeded + "One more while scrolled up?" + the retried "Will this fail?"
  // (scenario B's seed REPLACED scenario A's record).
  assert.equal(typedQuestionsAfterSwitch.length, 16, "the typed thread must survive the surface switch");
  assert.ok(
    !typedQuestionsAfterSwitch.some((text) => text.includes("Who is affected?")),
    "voice turns must NOT leak into the typed thread",
  );
  await closeSheet();
  await openSheet("Ask with your voice");
  await wait(400);
  const voiceThreadAfterSwitch = await page.evaluate(() =>
    [...document.querySelectorAll(".vthread .bub-q.voiced span:last-child")].map((n) => n.textContent),
  );
  assert.deepEqual(
    voiceThreadAfterSwitch,
    ["What led to this?", "Who is affected?"],
    "the voice transcript must survive switching to typed and back (in-session)",
  );
  await page.screenshot({ path: `${SCREENSHOT_DIR}/d2-voice-history-after-switch.png` });
  console.log("scenario D (voice dual transcripts + surface-switch isolation): PASS");

  console.log("chat UX contract e2e: PASS");
} finally {
  await browser.close();
}
