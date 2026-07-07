/**
 * Playwright onboarding walkthrough on a fresh build (issue #38) — the committed
 * browser lock for the FULL onboarding flow at an iPhone viewport with REAL
 * safe-area insets simulated (CDP `Emulation.setSafeAreaInsetsOverride`, so
 * `env(safe-area-inset-*)` resolves exactly like a Dynamic-Island device):
 *
 *   splash → email sign-in → OTP code → chat interview (multi-select chips →
 *   terminal confirm → story-budget card → YouTube grid → X cluster picker →
 *   YOUR-30 summary) → "Build my 30" → loading → routed to the reel.
 *
 * It also proves, with measurements, the two #38 edge cases:
 *   - TOP-GAP SUSPECT: the safe-area inset must be applied ONCE (OnboardingFlow's
 *     `<main>` env() padding) — the interview progress bar sits at inset + 24px
 *     (`pt-6`), NOT a doubled inset (~inset*2 + fixed). Numbers are asserted and
 *     printed as evidence.
 *   - GATE RULE (2026-06-30): `users.user_onboarded_at` is PATCHed exactly once,
 *     only AFTER "Build my 30" and after the terminal persist writes — never
 *     mid-interview. Every PostgREST call is recorded and the order asserted.
 *
 * Determinism: Supabase auth (`/auth/v1/otp`, `/auth/v1/verify`), PostgREST
 * (`/rest/v1/*`) and the worker turn endpoint (`/api/interview/turn`) are ALL
 * route-intercepted with CORS-complete stubs (docs/solutions: cross-origin stubs
 * without Access-Control-* headers are silently discarded), so the run needs no
 * network, no live DB, and no mailbox.
 *
 * NOT wired into `npm test` (vitest include is tests/lib + tests/seed — the unit
 * lane must not require a browser). Run standalone in the e2e lane; playwright-core
 * is already a devDependency and reuses the ms-playwright chromium cache:
 *
 *   NEXT_PUBLIC_AUTH_TEST_MODE=false PORT=3110 npm run dev &
 *   node tests/e2e/onboardingWalkthrough.e2e.mjs   # exits non-zero on any assertion failure
 *
 * AUTH_TEST_MODE must be OFF (the local .env sets it true): this walkthrough locks
 * the PRODUCTION auth surfaces — "Send magic link" + the 8-digit OTP entry — not the
 * fixed-code test-mode branch. Do NOT set NEXT_PUBLIC_ONBOARDING_SKIP_AUTH either.
 *
 * Per-step screenshots land in .agents/debug/issue-38/ (the review artifact).
 */

import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright-core";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3110";
const SHOT_DIR = new URL("../../.agents/debug/issue-38/", import.meta.url).pathname;
const SUPABASE_STORAGE_KEY = "sb-cerfennlcgureyifraqy-auth-token";

/** Dynamic-Island-class insets (iPhone 15 Pro) for the main pass. */
const ISLAND_INSETS = { top: 59, bottom: 34 };
/** SE-class: small 667px-tall viewport, classic 20px status bar. */
const SE_INSETS = { top: 20, bottom: 0 };

mkdirSync(SHOT_DIR, { recursive: true });

// ---------------------------------------------------------------------------
// Fixtures — catalog rows shaped to the client column projections
// (sources.ts CONTENT_SOURCE_COLUMNS / sourceClusters.ts CLUSTER_* / PERSONALITY_COLUMNS).
// ---------------------------------------------------------------------------

const uuid = (n) => `${String(n).repeat(8)}-${String(n).repeat(4)}-4${String(n).repeat(3)}-8${String(n).repeat(3)}-${String(n).repeat(12)}`;

const contentSource = (n, type, name, tags) => ({
  source_id: uuid(n),
  content_source_type: type,
  external_id: `ext-${n}`,
  source_name: name,
  source_description: `${name} — curated test row`,
  thumbnail_url: null,
  subscriber_count: 100000 * n,
  platform_metadata: {},
  personas: [],
  topic_tags: tags,
  popularity_score: 100 - n,
  is_curated: true,
  last_fetched_at: null,
});

const YOUTUBE_CHANNELS = [
  contentSource(1, "youtube_channel", "Sky Sports Cricket", ["sport"]),
  contentSource(2, "youtube_channel", "MKBHD", ["tech"]),
  contentSource(3, "youtube_channel", "Two Minute Papers", ["ai"]),
  contentSource(4, "youtube_channel", "The Economist", ["geopolitics"]),
  contentSource(5, "youtube_channel", "CNBC", ["business"]),
  contentSource(6, "youtube_channel", "Colin and Samir", ["youtube"]),
];
const X_SOURCES = [contentSource(7, "x_account", "@espncricinfo", ["sport"]), contentSource(8, "x_account", "@FabrizioRomano", ["sport"])];

const PERSONALITY = {
  personality_id: uuid(9),
  display_name: "Harsha Bhogle",
  aliases: ["bhogleharsha"],
  bio: "Cricket commentator",
  photo_url: null,
  youtube_channel_ids: [],
  personas: [],
  topic_tags: ["sport"],
  popularity_score: 95,
  is_curated: true,
};

const CLUSTERS = [
  {
    cluster_id: uuid(3),
    cluster_slug: "sport-cricket-voices",
    cluster_label: "Cricket voices",
    cluster_category: "sport",
    cluster_sort_order: 1,
    is_curated: true,
    cluster_subniche: "cricket",
    cluster_description: "The commentators and insiders who break cricket first.",
  },
  {
    cluster_id: uuid(4),
    cluster_slug: "sport-football-transfers",
    cluster_label: "Football transfer wire",
    cluster_category: "sport",
    cluster_sort_order: 2,
    is_curated: true,
    cluster_subniche: "football",
    cluster_description: "Deadline-day reporters.",
  },
];
const CLUSTER_MEMBERS = [
  { cluster_id: CLUSTERS[0].cluster_id, source_id: X_SOURCES[0].source_id, personality_id: null, member_sort_order: 1 },
  { cluster_id: CLUSTERS[0].cluster_id, source_id: null, personality_id: PERSONALITY.personality_id, member_sort_order: 2 },
  { cluster_id: CLUSTERS[1].cluster_id, source_id: X_SOURCES[1].source_id, personality_id: null, member_sort_order: 1 },
];

const MATCHED_INTEREST = {
  interest_id: uuid(5),
  interest_slug: "sport.cricket.ipl",
  interest_label: "IPL — auctions & transfers",
  depth_level: 3,
  interest_is_active: true,
  interest_parent_id: null,
  interest_search_query: "IPL auction",
};

// Scripted interview turns keyed on accumulated exchanges (same contract as
// tests/e2e/interviewChat.e2e.mjs).
const turnQuestion = (turn_index, question_text, options) => ({
  response_kind: "question",
  turn_index,
  question_text,
  bubbles: [
    ...options.map((bubble_label) => ({ bubble_label, bubble_kind: "option" })),
    { bubble_label: "not really / skip", bubble_kind: "skip" },
    { bubble_label: "something else — type it", bubble_kind: "type_your_own" },
  ],
});
const TURN_SCRIPT = [
  turnQuestion(0, "What do you follow most closely?", ["Sport", "Tech"]),
  turnQuestion(1, "Which sport pulls you in?", ["Cricket", "Football"]),
  {
    response_kind: "terminal",
    turn_index: 2,
    micro_interests: [
      {
        display_label: "IPL — auctions & transfers",
        canonical_slug: "sport.cricket.ipl",
        ladder: ["sport", "sport.cricket"],
        search_anchor_terms: ["IPL auction"],
        strict: false,
      },
    ],
    roots_only_fallback: false,
    mute_terms: [],
    angle_preferences: [],
  },
];

const SESSION_USER = {
  id: "e2e-user-0000-0000-0000-000000000000",
  aud: "authenticated",
  role: "authenticated",
  email: "e2e@example.com",
  app_metadata: { provider: "email" },
  user_metadata: {},
  created_at: "2026-01-01T00:00:00Z",
};
const sessionBody = () => ({
  access_token: "e2e-access-token",
  token_type: "bearer",
  expires_in: 31536000,
  expires_at: Math.floor(Date.now() / 1000) + 31536000,
  refresh_token: "e2e-refresh-token",
  user: SESSION_USER,
});

// ---------------------------------------------------------------------------
// Route stubbing — one recorder per browser context.
// ---------------------------------------------------------------------------

const CORS_HEADERS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "*",
  "access-control-expose-headers": "*",
};
const json = (body, status = 200) => ({
  status,
  headers: { ...CORS_HEADERS, "content-type": "application/json" },
  body: JSON.stringify(body),
});

/**
 * Install auth/PostgREST/worker stubs on a context. Returns the call log:
 * every /rest/v1 call as { method, table, body, mark } in arrival order, where
 * `mark` is whatever label the test had set via log.setMark() at the time.
 */
async function installRoutes(context) {
  const log = { calls: [], mark: "start", setMark(mark) { this.mark = mark; } };
  // Stateful gate: once the flow PATCHes user_onboarded_at, later users reads must
  // reflect it — otherwise the root gate (correctly) bounces a "never-onboarded"
  // user back to /onboarding and the routed-to-reel wait becomes racy.
  let stampedOnboardedAt = null;

  await context.route(
    (url) => url.href.includes("supabase.co") || url.pathname.includes("/api/interview/turn"),
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const method = request.method();

      if (method === "OPTIONS") {
        return route.fulfill({ status: 204, headers: CORS_HEADERS });
      }

      // Worker interview turns — scripted on accumulated exchange count.
      if (url.pathname.endsWith("/api/interview/turn")) {
        let exchangeCount = 0;
        try {
          exchangeCount = (JSON.parse(request.postData() ?? "{}").conversation_state ?? []).length;
        } catch {
          exchangeCount = 0;
        }
        const turn = TURN_SCRIPT[Math.min(exchangeCount, TURN_SCRIPT.length - 1)];
        return route.fulfill(json(turn));
      }

      // GoTrue auth endpoints.
      if (url.pathname.includes("/auth/v1/otp")) {
        return route.fulfill(json({}));
      }
      if (url.pathname.includes("/auth/v1/verify") || url.pathname.includes("/auth/v1/token")) {
        return route.fulfill(json(sessionBody()));
      }
      if (url.pathname.includes("/auth/v1/user")) {
        return route.fulfill(json(SESSION_USER));
      }
      if (url.pathname.includes("/auth/v1/")) {
        return route.fulfill(json({}));
      }

      // PostgREST.
      const restMatch = url.pathname.match(/\/rest\/v1\/(rpc\/)?([^/]+)/);
      if (!restMatch) {
        return route.fulfill(json([]));
      }
      const table = `${restMatch[1] ?? ""}${restMatch[2]}`;
      let requestBody = null;
      try {
        requestBody = request.postData() ? JSON.parse(request.postData()) : null;
      } catch {
        requestBody = request.postData();
      }
      log.calls.push({ method, table, body: requestBody, mark: log.mark });
      if (method === "PATCH" && table === "users" && requestBody && "user_onboarded_at" in requestBody) {
        stampedOnboardedAt = requestBody.user_onboarded_at;
      }

      if (table.startsWith("rpc/")) {
        // mint_interest_ladder returns the minted leaf interest_id (scalar).
        return route.fulfill(json(MATCHED_INTEREST.interest_id));
      }
      if (method === "GET") {
        if (table === "users") {
          // .maybeSingle() — single JSON OBJECT body, not a one-row array.
          return route.fulfill(json({ user_id: SESSION_USER.id, user_onboarded_at: stampedOnboardedAt }));
        }
        if (table === "interests") {
          return route.fulfill(json([MATCHED_INTEREST]));
        }
        if (table === "content_sources") {
          const typeFilter = url.searchParams.get("content_source_type") ?? "";
          if (typeFilter.includes("youtube_channel")) {
            return route.fulfill(json(YOUTUBE_CHANNELS));
          }
          if (typeFilter.includes("x_account")) {
            return route.fulfill(json(X_SOURCES));
          }
          return route.fulfill(json([...YOUTUBE_CHANNELS, ...X_SOURCES]));
        }
        if (table === "source_clusters") {
          return route.fulfill(json(CLUSTERS));
        }
        if (table === "source_cluster_members") {
          return route.fulfill(json(CLUSTER_MEMBERS));
        }
        if (table === "personalities") {
          return route.fulfill(json([PERSONALITY]));
        }
        return route.fulfill(json([]));
      }
      // Writes (POST/PATCH/DELETE): honour return=representation, else 204.
      const prefer = request.headers().prefer ?? "";
      if (prefer.includes("return=representation")) {
        const rows = requestBody === null ? [] : Array.isArray(requestBody) ? requestBody : [requestBody];
        return route.fulfill(json(rows, method === "POST" ? 201 : 200));
      }
      return route.fulfill({ status: 204, headers: CORS_HEADERS });
    },
  );

  return log;
}

/** All users PATCHes that stamp user_onboarded_at — the gate-rule probe. */
const stampWrites = (log) =>
  log.calls.filter((call) => call.method === "PATCH" && call.table === "users" && call.body && "user_onboarded_at" in call.body);

// ---------------------------------------------------------------------------
// Shared walk helpers.
// ---------------------------------------------------------------------------

const shoot = (page, name) => page.screenshot({ path: `${SHOT_DIR}${name}.png`, fullPage: false });

/** Assert an element (by locator) is fully inside the viewport and below the top inset. */
async function assertClearOfInset(page, locator, label, insetTop) {
  const box = await locator.boundingBox();
  assert.ok(box, `${label} should be visible`);
  assert.ok(
    box.y >= insetTop - 1,
    `${label} must sit below the ${insetTop}px top inset (got y=${box.y.toFixed(1)})`,
  );
  const viewport = page.viewportSize();
  assert.ok(box.x >= -1 && box.x + box.width <= viewport.width + 1, `${label} must not clip horizontally`);
  return box;
}

/** Walk the interview from Q0 through the terminal confirm card. */
async function walkInterviewToTerminal(page) {
  await page.getByText("What do you follow most closely?").waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "Sport", exact: true }).click();
  await page.getByRole("button", { name: "Tech", exact: true }).click();
  await page.locator("[data-testid='confirm-turn']").click();
  await page.getByText("Which sport pulls you in?").waitFor();
  // One continuous scrollback — the answered turn stays and collapses to a user bubble.
  await page.getByText("What do you follow most closely?").waitFor();
  assert.ok(
    (await page.locator("[data-testid='user-bubble']").allTextContents()).some(
      (text) => text.includes("Sport") && text.includes("Tech"),
    ),
    "multi-select answer should collapse into one user bubble",
  );
  await page.getByRole("button", { name: "Cricket", exact: true }).click();
  await page.locator("[data-testid='confirm-turn']").click();
  await page.getByText("IPL — auctions & transfers").waitFor();
}

/** Walk terminal confirm → budget → YouTube grid → X clusters → summary. Returns picker boxes. */
async function walkClosingArc(page, { shotPrefix = null } = {}) {
  const screenshots = shotPrefix !== null;
  await page.locator("[data-testid='confirm-terminal']").click();
  await page.locator("[data-testid='budget-card']").waitFor();
  assert.equal((await page.locator("[data-testid='budget-value-news']").textContent())?.trim(), "20");
  assert.equal((await page.locator("[data-testid='budget-value-youtube']").textContent())?.trim(), "7");
  assert.equal((await page.locator("[data-testid='budget-value-x']").textContent())?.trim(), "3");
  // Total pinned at 30 — with all slots allocated the increments are disabled.
  assert.equal((await page.locator("[data-testid='budget-total']").textContent())?.replace(/\s/g, ""), "30/30");
  assert.ok(
    await page.locator("[data-testid='budget-inc-news']").isDisabled(),
    "increment must be disabled while the 30-slot pool is full (the pinned total)",
  );
  if (screenshots) {
    await shoot(page, `${shotPrefix}06-budget-card`);
  }
  await page.locator("[data-testid='budget-review']").click();

  // YouTube grid — renders INSIDE the chat scroll container.
  await page.locator("[data-testid='youtube-grid']").waitFor({ timeout: 15000 });
  const tileCount = await page.locator("[data-testid='youtube-tile']").count();
  assert.ok(tileCount >= 4, `youtube grid should render tiles from the catalog (got ${tileCount})`);
  const scrollBox = await page.locator("[data-testid='chat-scroll']").boundingBox();
  const gridBox = await page.locator("[data-testid='youtube-grid']").boundingBox();
  assert.ok(
    gridBox.x >= scrollBox.x - 1 && gridBox.x + gridBox.width <= scrollBox.x + scrollBox.width + 1,
    "youtube grid must render inside the chat scroll, not overflow it",
  );
  await page.locator("[data-testid='youtube-tile']").first().click();
  await page.locator("[data-testid='supply-expectation']").waitFor();
  if (screenshots) {
    await shoot(page, `${shotPrefix}07-youtube-grid`);
  }
  await page.locator("[data-testid='youtube-confirm']").click();

  // X clusters.
  await page.locator("[data-testid='x-cluster-picker']").waitFor();
  await page.locator("[data-testid='cluster-card']").first().waitFor({ timeout: 15000 });
  await page.locator("[data-testid='cluster-toggle']").first().click();
  if (screenshots) {
    await shoot(page, `${shotPrefix}08-x-clusters`);
  }
  await page.locator("[data-testid='x-cluster-confirm']").click();

  // YOUR-30 summary reflects the picks.
  await page.locator("[data-testid='your-30-summary']").waitFor();
  assert.ok(
    (await page.locator("[data-testid='summary-youtube-picks']").textContent())?.includes("1 channel"),
    "summary should count the picked channel",
  );
  assert.ok(
    (await page.locator("[data-testid='summary-x-picks']").textContent())?.includes("1 cluster"),
    "summary should count the followed cluster",
  );
  if (screenshots) {
    await shoot(page, `${shotPrefix}09-summary`);
  }
}

// ---------------------------------------------------------------------------
// Pass 1 — iPhone 15-Pro-class viewport, Dynamic-Island insets, FULL flow.
// ---------------------------------------------------------------------------

async function islandPass(browser) {
  const context = await browser.newContext({
    viewport: { width: 393, height: 852 },
    deviceScaleFactor: 3,
    isMobile: true,
    hasTouch: true,
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  });
  const log = await installRoutes(context);
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("Emulation.setSafeAreaInsetsOverride", { insets: ISLAND_INSETS });

  // 1. Splash.
  log.setMark("splash");
  await page.goto(`${BASE}/onboarding`, { waitUntil: "networkidle", timeout: 60000 });
  const getStarted = page.getByRole("button", { name: "Get started" });
  await getStarted.waitFor({ timeout: 30000 });
  await assertClearOfInset(page, page.getByText("30 stories. 30 minutes. Caught up."), "splash headline", ISLAND_INSETS.top);
  await assertClearOfInset(page, getStarted, "Get started CTA", ISLAND_INSETS.top);
  await shoot(page, "01-splash");

  // 2. Email sign-in.
  log.setMark("email");
  await getStarted.click();
  await page.getByText("Sign in to blip").waitFor();
  await assertClearOfInset(page, page.getByText("Sign in to blip"), "email heading", ISLAND_INSETS.top);
  await page.getByLabel("Email address").fill("e2e@example.com");
  await shoot(page, "02-email");
  await page.getByRole("button", { name: "Send magic link" }).click();

  // 3. OTP code entry (wait_session).
  log.setMark("otp");
  await page.getByText("Check your inbox").waitFor();
  const codeInput = page.getByPlaceholder("8-digit code");
  await codeInput.fill("12345678");
  await shoot(page, "03-otp");
  await page.getByRole("button", { name: "Sign in with code" }).click();

  // 4. Interview mounts (session established via stubbed /auth/v1/verify).
  log.setMark("interview");
  await page.getByText("What do you follow most closely?").waitFor({ timeout: 30000 });

  // TOP-GAP MEASUREMENT (#38 suspect): the inset must be applied exactly once.
  const mainPaddingTop = await page.evaluate(() => getComputedStyle(document.querySelector("main")).paddingTop);
  assert.equal(
    mainPaddingTop,
    `${ISLAND_INSETS.top}px`,
    `onboarding <main> must pad by the safe-area inset exactly once (got ${mainPaddingTop})`,
  );
  const barBox = await page.locator("main div[aria-hidden='true']").first().locator("div").first().boundingBox();
  assert.ok(barBox, "interview progress bar should render");
  const expectedBarTop = ISLAND_INSETS.top + 24; // env() once + pt-6
  assert.ok(
    Math.abs(barBox.y - expectedBarTop) <= 4,
    `progress bar should sit at inset+24px=${expectedBarTop}px — a doubled inset would be ~${ISLAND_INSETS.top * 2 + 24}px (measured ${barBox.y.toFixed(1)}px)`,
  );
  console.log(
    `top-gap evidence: main paddingTop=${mainPaddingTop}, progress bar y=${barBox.y.toFixed(1)}px (expected ${expectedBarTop}px, doubled-inset failure would be ${ISLAND_INSETS.top * 2 + 24}px) — NOT REPRODUCIBLE`,
  );
  await shoot(page, "04-interview-q0");

  // 5. Chips → terminal.
  await walkInterviewToTerminal(page);
  await shoot(page, "05-interview-terminal");

  // 6-9. Closing arc with screenshots; the gate must NOT be stamped anywhere inside it.
  await walkClosingArc(page, { shotPrefix: "" });
  assert.equal(stampWrites(log).length, 0, "user_onboarded_at must NOT be stamped before Build my 30");

  // 10. Build my 30 → persist → stamp at the TRUE flow end → routed to the reel.
  log.setMark("build-my-30");
  await page.locator("[data-testid='build-my-30']").click();
  await page.waitForFunction(() => window.location.pathname === "/", null, { timeout: 30000 });
  await shoot(page, "10-after-complete");

  const stamps = stampWrites(log);
  assert.equal(stamps.length, 1, `user_onboarded_at must be stamped exactly once at flow end (got ${stamps.length})`);
  assert.equal(stamps[0].mark, "build-my-30", "the stamp must happen only after the Build-my-30 confirm");
  const stampIndex = log.calls.indexOf(stamps[0]);
  const persistWriteIndexes = log.calls
    .map((call, index) => ({ call, index }))
    .filter(
      ({ call }) =>
        call.mark === "build-my-30" && call.method !== "GET" && call.table !== "users" && !call.table.startsWith("rpc/"),
    )
    .map(({ index }) => index);
  assert.ok(persistWriteIndexes.length > 0, "terminal persist should write profile rows before the stamp");
  assert.ok(
    persistWriteIndexes.every((index) => index < stampIndex),
    "every terminal persist write must land BEFORE the user_onboarded_at stamp",
  );
  console.log(
    `gate evidence: ${persistWriteIndexes.length} persist writes then 1 stamp (call #${stampIndex}, mark=${stamps[0].mark})`,
  );

  await context.close();
}

// ---------------------------------------------------------------------------
// Pass 2 — SE-class viewport (375×667): pickers must fit the chat scroll
// without horizontal overflow. A pre-seeded session skips the auth screens
// (already locked by pass 1) and goes splash → interview directly.
// ---------------------------------------------------------------------------

async function sePass(browser) {
  const context = await browser.newContext({
    viewport: { width: 375, height: 667 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const log = await installRoutes(context);
  await context.addInitScript(
    ([storageKey, session]) => {
      window.localStorage.setItem(storageKey, JSON.stringify(session));
    },
    [SUPABASE_STORAGE_KEY, sessionBody()],
  );
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("Emulation.setSafeAreaInsetsOverride", { insets: SE_INSETS });

  log.setMark("se-pass");
  await page.goto(`${BASE}/onboarding`, { waitUntil: "networkidle", timeout: 60000 });
  await page.getByRole("button", { name: "Get started" }).click();
  await walkInterviewToTerminal(page);
  await walkClosingArc(page, { shotPrefix: "se-" });

  // No horizontal overflow anywhere on the small viewport.
  const overflow = await page.evaluate(() => {
    const root = document.scrollingElement;
    const chatScroll = document.querySelector("[data-testid='chat-scroll']");
    return {
      rootScrollWidth: root.scrollWidth,
      innerWidth: window.innerWidth,
      chatScrollWidth: chatScroll ? chatScroll.scrollWidth : 0,
      chatClientWidth: chatScroll ? chatScroll.clientWidth : 0,
    };
  });
  assert.ok(
    overflow.rootScrollWidth <= overflow.innerWidth + 1,
    `SE viewport must not scroll horizontally (scrollWidth=${overflow.rootScrollWidth} > innerWidth=${overflow.innerWidth})`,
  );
  await shoot(page, "11-se-summary");
  console.log(
    `SE evidence: rootScrollWidth=${overflow.rootScrollWidth} innerWidth=${overflow.innerWidth} chatScroll=${overflow.chatScrollWidth}/${overflow.chatClientWidth}`,
  );

  await context.close();
}

// ---------------------------------------------------------------------------

const browser = await chromium.launch({ headless: true });
try {
  await islandPass(browser);
  await sePass(browser);
  console.log("onboarding walkthrough e2e: PASS");
} finally {
  await browser.close();
}
