/**
 * Deterministic puppeteer regression for the one-scrollback FSR interview chat
 * (slice #18) — the committed browser lock for the user-visible behaviors a jsdom
 * component test can't honestly prove: multi-select chips + a confirm, the whole
 * conversation staying on screen with ZERO screen swaps, and answered turns collapsing
 * into user bubbles, driven in a real browser.
 *
 * Determinism: the worker turn endpoint (`/api/interview/turn`) is request-intercepted
 * and served scripted turns keyed on how many exchanges the client has accumulated, and
 * a non-expired Supabase session is seeded into localStorage so the real client path runs
 * offline. The dev server is started with `NEXT_PUBLIC_ONBOARDING_SKIP_AUTH=true` so the
 * flow opens directly on the interview and the confirm handoff needs no Supabase write.
 *
 * NOT wired into `npm test` (vitest) — it needs a browser + a running dev server, which
 * would make unit CI require Chromium. Run it standalone in the e2e lane:
 *
 *   npm i -D puppeteer   # one-time
 *   NEXT_PUBLIC_ONBOARDING_SKIP_AUTH=true NEXT_PUBLIC_QA_API_BASE_URL="" PORT=3100 npm run dev &
 *   node tests/e2e/interviewChat.e2e.mjs        # exits non-zero on any assertion failure
 *
 * Rule 9 — a puppeteer test that passes on a missing feature is mis-written: each step
 * asserts a concrete user-visible outcome (scrollback intact, collapsed bubble, no swap)
 * and fails loudly if it is absent.
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
  mute_terms: [],
  angle_preferences: [],
};

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
  const userBubbles = () =>
    page.evaluate(() => [...document.querySelectorAll("[data-testid='user-bubble']")].map((n) => n.textContent));

  await page.goto(`${BASE}/onboarding`, { waitUntil: "networkidle2" });
  await wait(1200);

  // 1. Interview mounts as chips + affordances (the tap-through screen is gone).
  assert.ok(await bodyHas("What do you follow most closely?"), "first question should render");
  assert.ok(await bodyHas("not really / skip"), "skip affordance should render on every question");
  // The "type it" affordance is the live composer — its placeholder carries the engine's label.
  const composerPlaceholder = await page.evaluate(
    () => document.querySelector("[data-testid='composer-input']")?.getAttribute("placeholder") ?? "",
  );
  assert.ok(
    composerPlaceholder.includes("something else — type it"),
    `composer should be live with the type-your-own label; got placeholder ${JSON.stringify(composerPlaceholder)}`,
  );

  // 2. MULTI-SELECT two chips, then confirm — the engine gets both on one exchange.
  await clickByText("Sport");
  await clickByText("Tech");
  await clickByText("Done");
  await wait(800);

  // 3. ZERO screen swaps: the answered question AND its collapsed answer stay on screen
  //    alongside the new question — one continuous scrollback.
  assert.ok(await bodyHas("What do you follow most closely?"), "prior question must remain visible (no screen swap)");
  assert.ok(await bodyHas("Which sport pulls you in?"), "next question should render below the first");
  const bubbles = await userBubbles();
  assert.ok(
    bubbles.some((text) => text.includes("Sport") && text.includes("Tech")),
    `the answered turn should collapse to a user bubble with both taps; got ${JSON.stringify(bubbles)}`,
  );

  // 4. Tap to terminal → the confirm card renders INLINE in the scrollback (no swap).
  await clickByText("Cricket");
  await clickByText("Done");
  await wait(800);
  assert.ok(await bodyHas("IPL — auctions & transfers"), "confirm card should show the user-vocabulary label");
  assert.ok(await bodyHas("What do you follow most closely?"), "history must still be scrolled-back on the terminal");

  // 5. Confirm advances OFF the interview (to the next stage).
  await clickByText("Looks good");
  await wait(1500);
  assert.ok(!(await bodyHas("IPL — auctions & transfers")), "confirming should advance past the interview");

  console.log("interview chat e2e: PASS");
} finally {
  await browser.close();
}
