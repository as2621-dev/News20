/**
 * Deterministic puppeteer regression for the FSR interview chat stage (slice #4) —
 * the committed browser lock for the user-visible behaviors a jsdom component test
 * can't honestly prove: the REAL tap-through with navigation from the interview to
 * the sources stage, driven in a real browser.
 *
 * Determinism: the worker turn endpoint (`/api/interview/turn`) is request-intercepted
 * and served scripted turns, and a non-expired Supabase session is seeded into
 * localStorage so the real client code path runs offline (no live worker, no auth).
 * The dev server is started with `NEXT_PUBLIC_ONBOARDING_SKIP_AUTH=true` so the flow
 * opens directly on the interview and the confirm handoff needs no Supabase write.
 *
 * NOT wired into `npm test` (vitest) — it needs a browser + a running dev server, which
 * would make unit CI require Chromium. Run it standalone in the e2e lane:
 *
 *   npm i -D puppeteer   # one-time; puppeteer is intentionally NOT in package.json yet
 *   NEXT_PUBLIC_ONBOARDING_SKIP_AUTH=true NEXT_PUBLIC_QA_API_BASE_URL="" PORT=3100 npm run dev &
 *   node tests/e2e/interviewChat.e2e.mjs        # exits non-zero on any assertion failure
 *
 * Rule 9 — a puppeteer test that passes on a missing feature is mis-written: each step
 * asserts a concrete user-visible outcome (question → next question → confirm label →
 * off the interview) and fails loudly if it is absent.
 */

import assert from "node:assert/strict";
import puppeteer from "puppeteer";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3100";
const SUPABASE_STORAGE_KEY = "sb-cerfennlcgureyifraqy-auth-token";

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
const Q1 = question(1, "Which sport pulls you in?", ["Cricket", "Football"]);
const TERMINAL = {
  response_kind: "terminal",
  turn_index: 2,
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

  // Serve scripted turns keyed on how many exchanges the client has accumulated.
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    if (!req.url().includes("/api/interview/turn")) {
      return req.continue();
    }
    let count = 0;
    try {
      count = (JSON.parse(req.postData() || "{}").conversation_state || []).length;
    } catch {
      count = 0;
    }
    const turn = count === 0 ? Q0 : count === 1 ? Q1 : TERMINAL;
    return req.respond({ status: 200, contentType: "application/json", body: JSON.stringify(turn) });
  });

  const clickByText = async (text) => {
    const clicked = await page.evaluate((label) => {
      const button = [...document.querySelectorAll("button")].find((candidate) =>
        candidate.textContent?.includes(label),
      );
      if (button) {
        button.click();
      }
      return Boolean(button);
    }, text);
    assert.ok(clicked, `expected a button containing "${text}"`);
  };
  const bodyHas = (text) => page.evaluate((needle) => document.body.textContent.includes(needle), text);

  await page.goto(`${BASE}/onboarding`, { waitUntil: "networkidle2" });
  await wait(1200);

  // 1. Interview mounts (picker replaced): question + skip + type-your-own affordances.
  assert.ok(await bodyHas("What do you follow most closely?"), "first question should render");
  assert.ok(await bodyHas("not really / skip"), "skip affordance should render on every question");
  assert.ok(await bodyHas("something else — type it"), "type-your-own affordance should render");

  // 2. Tap through to the next question.
  await clickByText("Sport");
  await wait(700);
  assert.ok(await bodyHas("Which sport pulls you in?"), "second question should render after a tap");

  // 3. Tap to terminal → confirm screen shows the interest in the user's own words.
  await clickByText("Cricket");
  await wait(700);
  assert.ok(await bodyHas("IPL — auctions & transfers"), "confirm screen should show the user-vocabulary label");

  // 4. Confirm advances OFF the interview (to the sources stage).
  await clickByText("Looks good");
  await wait(1500);
  assert.ok(!(await bodyHas("IPL — auctions & transfers")), "confirming should advance past the interview");

  console.log("interview chat e2e: PASS");
} finally {
  await browser.close();
}
