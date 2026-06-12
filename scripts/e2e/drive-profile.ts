/**
 * Go-live E2E — drive ONE test profile through the full user journey in headless Chrome.
 *
 * Run: `npx tsx scripts/e2e/drive-profile.ts --profile profile-a-tech-ai`
 * Flags:
 *   --profile <name>        which scripts/e2e/profiles.json profile to drive (required)
 *   --expect-personalized   second pass: assert the personalized daily_feeds path (no fallback)
 *   --steps a,b,c           run only these journey steps (default: the full first-pass list)
 *   --headed                visible Chrome (debugging)
 *   --base-url <url>        app origin (default http://localhost:3000)
 *
 * Mechanics:
 *   - Spawns a dedicated Chrome with fake-mic flags (`--use-fake-ui-for-media-stream`
 *     `--use-fake-device-for-media-stream`) + its own user-data-dir + CDP port, then
 *     connects via playwright-core `connectOverCDP`.
 *   - Auth: signs in Node-side with the seeded password (`signInWithPassword`) and
 *     injects the session into `localStorage["sb-<ref>-auth-token"]` before page load —
 *     no magic-link email. The onboarding UI is then driven FOR REAL (picker clicks,
 *     source swipes, build-30) so every persistence path is exercised.
 *   - Telemetry: console (the app's structured logger events are the oracles), page
 *     errors, failed requests, HTTP >= 400 responses, and raw CDP WebSocket frames
 *     (the Gemini Live handshake).
 *   - Each step failure captures a screenshot + console/network dump into
 *     `.agents/e2e/state/<profile>/`, marks dependents blocked, and continues.
 *     Results land in `.agents/e2e/state/<profile>-result.json`; exit 0 = all passed.
 */

import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { createClient } from "@supabase/supabase-js";
import { type Browser, type BrowserContext, chromium, type CDPSession, type Page } from "playwright-core";
import { E2E_STATE_DIR, loadDotEnv, REPO_ROOT, requireEnv } from "./env";

// ── Types ─────────────────────────────────────────────────────────────────────

interface E2eProfileDefinition {
  profile_name: string;
  email: string;
  cdp_port: number;
  picker_selections: string[][];
  picker_custom_topics: Array<{ path: string[]; value: string }>;
  source_follows_per_screen: number;
  build_30: { mode: "save" | "skip"; boost_bucket?: string };
  text_question: string;
}

interface SeededTestUser {
  profile_name: string;
  email: string;
  password: string;
  user_id: string;
}

type StepStatus = "pass" | "fail" | "blocked" | "skipped";

interface StepResult {
  step_name: string;
  status: StepStatus;
  evidence: string;
  artifacts: string[];
  duration_ms: number;
}

interface ConsoleRecord {
  message_type: string;
  message_text: string;
}

interface WsRecord {
  ws_url: string;
  sent_frames: string[];
  received_frames: string[];
  is_closed: boolean;
}

// ── Bucket display names (mirror of src/lib/feedBuckets.ts DESIGN_BUCKETS) ────
const BUCKET_DISPLAY_NAMES: Record<string, string> = {
  breaking: "Breaking News",
  world: "World & Politics",
  markets: "Markets",
  tech: "Tech & Science",
  sport: "Sport",
  culture: "Culture",
  youtube: "YouTube",
  x: "X",
  podcasts: "Podcasts",
};

const CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const FIRST_PASS_STEPS = [
  "onboarding_splash",
  "picker",
  "sources",
  "build_30",
  "reel_loads",
  "reel_playback",
  "article_layer",
  "text_qa",
  "voice_live",
] as const;

/** Steps blocked when a prerequisite fails (transitively applied). */
const STEP_DEPENDENCIES: Record<string, string[]> = {
  picker: ["onboarding_splash"],
  sources: ["picker"],
  build_30: ["sources"],
  reel_loads: ["build_30"],
  reel_playback: ["reel_loads"],
  article_layer: ["reel_playback"],
  text_qa: ["reel_playback"],
  voice_live: ["reel_playback"],
  personalized_feed: [],
};

// ── Small helpers ─────────────────────────────────────────────────────────────

function parseArgs(): {
  profile_name: string;
  expect_personalized: boolean;
  steps: string[] | null;
  headed: boolean;
  base_url: string;
} {
  const argv = process.argv.slice(2);
  const getFlagValue = (flag: string): string | null => {
    const flagIndex = argv.indexOf(flag);
    return flagIndex >= 0 && argv[flagIndex + 1] ? argv[flagIndex + 1] : null;
  };
  const profileName = getFlagValue("--profile");
  if (!profileName) {
    throw new Error("--profile <name> is required (see scripts/e2e/profiles.json)");
  }
  const stepsValue = getFlagValue("--steps");
  return {
    profile_name: profileName,
    expect_personalized: argv.includes("--expect-personalized"),
    steps: stepsValue ? stepsValue.split(",").map((step) => step.trim()) : null,
    headed: argv.includes("--headed"),
    base_url: getFlagValue("--base-url") ?? "http://localhost:3000",
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Poll a predicate until truthy or timeout (the driver's generic wait). */
async function pollUntil<T>(probe: () => Promise<T | null | false>, timeoutMs: number, label: string): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown = null;
  while (Date.now() < deadline) {
    try {
      const value = await probe();
      if (value) {
        return value;
      }
    } catch (error) {
      lastError = error;
    }
    await sleep(250);
  }
  throw new Error(`timeout (${timeoutMs}ms) waiting for: ${label}${lastError ? ` (last error: ${String(lastError)})` : ""}`);
}

// ── The driver ────────────────────────────────────────────────────────────────

class ProfileDriver {
  readonly profile: E2eProfileDefinition;
  readonly testUser: SeededTestUser;
  readonly baseUrl: string;
  readonly artifactsDir: string;

  chromeProcess: ChildProcess | null = null;
  browser: Browser | null = null;
  context: BrowserContext | null = null;
  page: Page | null = null;
  cdpSession: CDPSession | null = null;

  consoleRecords: ConsoleRecord[] = [];
  pageErrors: string[] = [];
  failedRequests: Array<{ request_url: string; failure_text: string }> = [];
  errorResponses: Array<{ response_url: string; status: number }> = [];
  responseLog: Array<{ response_url: string; status: number }> = [];
  wsRecords = new Map<string, WsRecord>();

  constructor(profile: E2eProfileDefinition, testUser: SeededTestUser, baseUrl: string) {
    this.profile = profile;
    this.testUser = testUser;
    this.baseUrl = baseUrl;
    this.artifactsDir = path.join(E2E_STATE_DIR, profile.profile_name);
    mkdirSync(this.artifactsDir, { recursive: true });
  }

  // ── Boot ──
  async boot(headed: boolean): Promise<void> {
    if (!existsSync(CHROME_BINARY)) {
      throw new Error(`Chrome binary not found at ${CHROME_BINARY}`);
    }
    // The Chrome profile dir must live OUTSIDE the repo: Chrome writes to it
    // continuously (prefs/cache journals), and an in-repo dir triggers an endless
    // Next.js Fast Refresh rebuild storm during the run (remounts pause the reel
    // audio and flood the console oracle).
    const userDataDir = path.join(os.tmpdir(), `news20-e2e-chrome-${this.profile.profile_name}`);
    // Hermetic runs: a reused user-data-dir carries last run's localStorage (e.g. the
    // source-onboarding-complete flag), which routes a freshly re-seeded user straight
    // to the reel and skips the source deck. Wipe it so browser state matches DB state.
    rmSync(userDataDir, { recursive: true, force: true });
    mkdirSync(userDataDir, { recursive: true });
    const chromeArgs = [
      `--remote-debugging-port=${this.profile.cdp_port}`,
      `--user-data-dir=${userDataDir}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      "--autoplay-policy=no-user-gesture-required",
      // Reason: headless Chrome's HTTP/3 (QUIC) connection to Supabase storage
      // stalls ~50% of runs (digest.mp3 fetch hangs at readyState 0 forever, so the
      // reel clock never starts). Verified: same fetch is 200-in-~1s with QUIC off.
      "--disable-quic",
      "--window-size=430,932",
    ];
    if (!headed) {
      chromeArgs.unshift("--headless=new");
    }
    this.chromeProcess = spawn(CHROME_BINARY, [...chromeArgs, "about:blank"], { stdio: "ignore" });

    this.browser = await pollUntil(
      async () => {
        try {
          return await chromium.connectOverCDP(`http://127.0.0.1:${this.profile.cdp_port}`);
        } catch {
          return null;
        }
      },
      15000,
      "Chrome CDP endpoint",
    );

    this.context = this.browser.contexts()[0] ?? (await this.browser.newContext());

    // Session injection + voice grant, applied to every new document on our origin
    // BEFORE app scripts run — supabase-js reads this exact key on init.
    const session = await this.mintSession();
    const projectRef = new URL(requireEnv("NEXT_PUBLIC_SUPABASE_URL")).hostname.split(".")[0];
    const appOrigin = new URL(this.baseUrl).origin;
    await this.context.addInitScript(
      ({ storage_key, session_json, app_origin, voice_key }) => {
        if (window.location.origin !== app_origin) {
          return;
        }
        if (!window.localStorage.getItem(storage_key)) {
          window.localStorage.setItem(storage_key, session_json);
        }
        window.localStorage.setItem(voice_key, "1");
      },
      {
        storage_key: `sb-${projectRef}-auth-token`,
        session_json: JSON.stringify(session),
        app_origin: appOrigin,
        voice_key: "blip-voice-granted",
      },
    );

    this.page = this.context.pages()[0] ?? (await this.context.newPage());
    this.page.setDefaultTimeout(30000);
    this.wireTelemetry();
    await this.wireCdpWebSockets();
  }

  /** Node-side password sign-in → a full Session object for localStorage injection. */
  async mintSession(): Promise<object> {
    const anonClient = createClient(requireEnv("NEXT_PUBLIC_SUPABASE_URL"), requireEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY"), {
      auth: { persistSession: false, autoRefreshToken: false },
    });
    const { data, error } = await anonClient.auth.signInWithPassword({
      email: this.testUser.email,
      password: this.testUser.password,
    });
    if (error || !data.session) {
      throw new Error(`signInWithPassword(${this.profile.profile_name}) failed: ${error?.message ?? "no session"}`);
    }
    return data.session;
  }

  wireTelemetry(): void {
    const page = this.mustPage();
    page.on("console", (message) => {
      this.consoleRecords.push({ message_type: message.type(), message_text: message.text() });
    });
    page.on("pageerror", (error) => {
      this.pageErrors.push(error.message);
    });
    page.on("requestfailed", (request) => {
      this.failedRequests.push({ request_url: request.url(), failure_text: request.failure()?.errorText ?? "unknown" });
    });
    page.on("response", (response) => {
      this.responseLog.push({ response_url: response.url(), status: response.status() });
      if (response.status() >= 400) {
        this.errorResponses.push({ response_url: response.url(), status: response.status() });
      }
    });
  }

  async wireCdpWebSockets(): Promise<void> {
    const cdp = await this.mustContext().newCDPSession(this.mustPage());
    this.cdpSession = cdp;
    await cdp.send("Network.enable");
    const urlByRequestId = new Map<string, string>();
    cdp.on("Network.webSocketCreated", (event: { requestId: string; url: string }) => {
      urlByRequestId.set(event.requestId, event.url);
      this.wsRecords.set(event.requestId, { ws_url: event.url, sent_frames: [], received_frames: [], is_closed: false });
    });
    cdp.on("Network.webSocketFrameSent", (event: { requestId: string; response: { payloadData: string } }) => {
      this.wsRecords.get(event.requestId)?.sent_frames.push(event.response.payloadData.slice(0, 4000));
    });
    cdp.on(
      "Network.webSocketFrameReceived",
      (event: { requestId: string; response: { opcode: number; payloadData: string } }) => {
        // Reason: CDP base64-encodes BINARY frames (opcode 2), and Gemini Live
        // sends its JSON over the binary channel (useGeminiLive gotcha 6) — decode
        // to UTF-8 text or string matches like "setupComplete" can never hit.
        const framePayloadText =
          event.response.opcode === 2
            ? Buffer.from(event.response.payloadData, "base64").toString("utf8")
            : event.response.payloadData;
        this.wsRecords.get(event.requestId)?.received_frames.push(framePayloadText.slice(0, 4000));
      },
    );
    cdp.on("Network.webSocketClosed", (event: { requestId: string }) => {
      const record = this.wsRecords.get(event.requestId);
      if (record) {
        record.is_closed = true;
      }
    });
  }

  mustPage(): Page {
    if (!this.page) {
      throw new Error("driver page not booted");
    }
    return this.page;
  }

  mustContext(): BrowserContext {
    if (!this.context) {
      throw new Error("driver context not booted");
    }
    return this.context;
  }

  // ── Oracles ──
  /** Wait until a structured logger event (by name) appears in the console stream. */
  async waitForConsoleEvent(eventName: string, timeoutMs: number): Promise<string> {
    return pollUntil(
      async () => this.consoleRecords.find((record) => record.message_text.includes(eventName))?.message_text ?? null,
      timeoutMs,
      `console event ${eventName}`,
    );
  }

  hasConsoleEvent(eventName: string): boolean {
    return this.consoleRecords.some((record) => record.message_text.includes(eventName));
  }

  async screenshot(label: string): Promise<string> {
    const shotPath = path.join(this.artifactsDir, `${label}.png`);
    try {
      await this.mustPage().screenshot({ path: shotPath });
    } catch {
      return "";
    }
    return shotPath;
  }

  dumpTelemetry(label: string): string[] {
    const consolePath = path.join(this.artifactsDir, `${label}-console.json`);
    const networkPath = path.join(this.artifactsDir, `${label}-network.json`);
    const websocketPath = path.join(this.artifactsDir, `${label}-websockets.json`);
    writeFileSync(consolePath, JSON.stringify({ console: this.consoleRecords.slice(-200), page_errors: this.pageErrors }, null, 2));
    writeFileSync(
      networkPath,
      JSON.stringify({ failed_requests: this.failedRequests, error_responses: this.errorResponses }, null, 2),
    );
    writeFileSync(
      websocketPath,
      JSON.stringify(
        Array.from(this.wsRecords.values()).map((record) => ({
          ws_url: record.ws_url.slice(0, 160),
          is_closed: record.is_closed,
          sent_frame_count: record.sent_frames.length,
          received_frame_count: record.received_frames.length,
          sent_frames_head: record.sent_frames.slice(0, 5).map((frame) => frame.slice(0, 500)),
          received_frames_head: record.received_frames.slice(0, 10).map((frame) => frame.slice(0, 500)),
        })),
        null,
        2,
      ),
    );
    return [consolePath, networkPath, websocketPath];
  }

  // ── DOM helpers ──
  /** Click the first visible button whose textContent contains `text`. */
  async clickButtonByText(text: string, timeoutMs = 15000): Promise<void> {
    const page = this.mustPage();
    await pollUntil(
      async () =>
        page.evaluate((needle) => {
          const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>("button"));
          const target = buttons.find((button) => (button.textContent ?? "").includes(needle) && !button.disabled);
          if (!target) {
            return false;
          }
          target.click();
          return true;
        }, text),
      timeoutMs,
      `button with text "${text}"`,
    );
  }

  async clickByAriaLabel(label: string, timeoutMs = 15000): Promise<void> {
    const page = this.mustPage();
    await pollUntil(
      async () =>
        page.evaluate((needle) => {
          const target = document.querySelector<HTMLElement>(`[aria-label="${needle}"]`);
          if (!target) {
            return false;
          }
          target.click();
          return true;
        }, label),
      timeoutMs,
      `[aria-label="${label}"]`,
    );
  }

  async waitForSelector(selector: string, timeoutMs = 20000): Promise<void> {
    const page = this.mustPage();
    await pollUntil(
      async () => page.evaluate((sel) => document.querySelector(sel) !== null, selector),
      timeoutMs,
      `selector ${selector}`,
    );
  }

  /**
   * Walk a TopicTree label path: every element but the last is a branch (clicking its
   * `.tlabel` expands it), the last is clicked as-is (leaf `.tlabel` click = select).
   * Scoped descent: each label is resolved INSIDE the previously matched `.tnode`,
   * so duplicate set names ("Topics", "Companies") resolve correctly.
   */
  async walkTopicTreePath(labelPath: string[]): Promise<void> {
    const page = this.mustPage();
    for (let depth = 0; depth < labelPath.length; depth += 1) {
      const isLast = depth === labelPath.length - 1;
      const partialPath = labelPath.slice(0, depth + 1);
      const didClick = await pollUntil(
        async () =>
          page.evaluate(
            ({ walk_path, click_last }) => {
              let scope: ParentNode = document;
              let node: Element | null = null;
              for (const label of walk_path) {
                const candidates: Element[] = Array.from(scope.querySelectorAll(".tnode"));
                node =
                  candidates.find((candidate: Element) => {
                    const labelButton = candidate.querySelector(":scope > .trow .tlabel");
                    return labelButton?.textContent?.trim() === label;
                  }) ?? null;
                if (!node) {
                  return false;
                }
                scope = node;
              }
              if (!node) {
                return false;
              }
              const targetLabel = node.querySelector(":scope > .trow .tlabel") as HTMLElement | null;
              if (!targetLabel) {
                return false;
              }
              if (click_last) {
                targetLabel.click(); // leaf select (or branch toggle for branch-select paths)
                return true;
              }
              // Branch: click to expand only when not already open.
              const isOpen = node.querySelector(":scope > .tchildren") !== null;
              if (!isOpen) {
                targetLabel.click();
              }
              return true;
            },
            { walk_path: partialPath, click_last: isLast },
          ),
        15000,
        `topic tree path ${partialPath.join(" > ")}`,
      );
      if (!didClick) {
        throw new Error(`topic tree path not found: ${partialPath.join(" > ")}`);
      }
      await sleep(200);
    }
  }

  /** Type a custom topic into the add-chip input under an expanded branch path. */
  async addCustomTopic(branchPath: string[], value: string): Promise<void> {
    await this.walkTopicTreePath(branchPath.slice(0, -1).concat(branchPath.slice(-1))); // expand all the way
    const page = this.mustPage();
    // The final path element is a BRANCH here (walk clicked its label = toggled it
    // when it is a leafless set... it is a branch, so the click expanded it). Find
    // its addchip input and commit with Enter.
    await pollUntil(
      async () =>
        page.evaluate(
          ({ walk_path, custom_value }) => {
            let scope: ParentNode = document;
            for (const label of walk_path) {
              const candidates: Element[] = Array.from(scope.querySelectorAll(".tnode"));
              const node =
                candidates.find(
                  (candidate: Element) => candidate.querySelector(":scope > .trow .tlabel")?.textContent?.trim() === label,
                ) ?? null;
              if (!node) {
                return false;
              }
              scope = node;
            }
            const input = (scope as Element).querySelector(".addchip input") as HTMLInputElement | null;
            if (!input) {
              return false;
            }
            const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
            nativeSetter?.call(input, custom_value);
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
            return true;
          },
          { walk_path: branchPath, custom_value: value },
        ),
      15000,
      `custom topic input under ${branchPath.join(" > ")}`,
    );
  }

  // ── Journey steps ──

  async stepOnboardingSplash(): Promise<string> {
    const page = this.mustPage();
    await page.goto(`${this.baseUrl}/onboarding`, { waitUntil: "domcontentloaded" });
    await this.clickButtonByText("Get started", 30000);
    // Phase-0 session-skip: signed-in user lands straight on the picker.
    await this.waitForSelector(".tree-view", 20000);
    await this.screenshot("01-picker");
    return "splash rendered; Get started routed a signed-in user straight to the picker (no email step)";
  }

  async stepPicker(): Promise<string> {
    for (const selectionPath of this.profile.picker_selections) {
      await this.walkTopicTreePath(selectionPath);
    }
    for (const custom of this.profile.picker_custom_topics) {
      await this.addCustomTopic(custom.path, custom.value);
    }
    await this.screenshot("02-picker-selected");
    await this.clickButtonByText("Done →");
    const completedEvent = await this.waitForConsoleEvent("onboarding_completed", 30000);
    return `picker selections persisted: ${completedEvent.slice(0, 300)}`;
  }

  async stepSources(): Promise<string> {
    const page = this.mustPage();
    // The curtain auto-reveals on a scripted timeline; wait for the deck chrome.
    await this.waitForSelector(".sw-top", 40000);
    await this.screenshot("03-sources-deck");

    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      const deckState = await page.evaluate(() => {
        const seeBriefing = document.querySelector<HTMLElement>('[data-action="see-briefing"]');
        if (seeBriefing) {
          return "final-done";
        }
        if (document.querySelector('[data-source-swipe-card="lead"]')) {
          return "cards";
        }
        return "waiting";
      });
      if (deckState === "final-done") {
        await page.evaluate(() => document.querySelector<HTMLElement>('[data-action="see-briefing"]')?.click());
        const totalEvent = await this.waitForConsoleEvent("source_onboarding_completed", 15000);
        return `source swipe completed: ${totalEvent.slice(0, 200)}`;
      }
      if (deckState === "cards") {
        for (let followIndex = 0; followIndex < this.profile.source_follows_per_screen; followIndex += 1) {
          const hasLead = await page.evaluate(() => document.querySelector('[data-source-swipe-card="lead"]') !== null);
          if (!hasLead) {
            break;
          }
          await page.evaluate(() => document.querySelector<HTMLElement>('[data-action="follow"]')?.click());
          await sleep(600); // fly-off animation + optimistic persist
        }
        // Skip the rest of this set; sets 1–3 auto-advance after the handoff dwell.
        await page.evaluate(() => document.querySelector<HTMLElement>(".sw-skipall")?.click());
        await sleep(2300);
      } else {
        await sleep(500);
      }
    }
    throw new Error("source swipe did not reach the final done screen within 120s");
  }

  async stepBuild30(): Promise<string> {
    const page = this.mustPage();
    await this.waitForSelector(".a-scroll", 20000);
    await this.screenshot("04-build-30");
    if (this.profile.build_30.mode === "skip") {
      await this.clickButtonByText("do this later");
      await this.waitForConsoleEvent("build_your_30_skipped", 15000);
      return "build-30 skipped (balanced-default allocator path)";
    }
    // Fill the budget to exactly 30 by incrementing the boost bucket's stepper.
    const boostName = BUCKET_DISPLAY_NAMES[this.profile.build_30.boost_bucket ?? ""] ?? null;
    for (let safety = 0; safety < 40; safety += 1) {
      const slotsLeft = await page.evaluate(() => {
        const label = document.querySelector("#blbl")?.textContent ?? "";
        const match = label.match(/(\d+)\s*left/);
        return match ? Number(match[1]) : 0;
      });
      if (slotsLeft === 0) {
        break;
      }
      await page.evaluate((bucketName) => {
        const byAria = bucketName
          ? document.querySelector<HTMLElement>(`button[aria-label="More ${bucketName}"]`)
          : null;
        const fallback = document.querySelector<HTMLElement>('.seg .stepper button[data-d="1"]');
        (byAria ?? fallback)?.click();
      }, boostName);
      await sleep(120);
    }
    await this.clickButtonByText("Save this order →");
    const savedEvent = await this.waitForConsoleEvent("build_your_30_save_completed", 30000);
    return `allocation saved: ${savedEvent.slice(0, 200)}`;
  }

  async stepReelLoads(expectPersonalized: boolean): Promise<string> {
    // After build-30 the flow routes to "/"; tolerate either an in-flow route push or
    // a fresh navigation (second pass).
    const page = this.mustPage();
    if (!page.url().startsWith(this.baseUrl) || page.url().includes("/onboarding")) {
      // Must end up ON the app origin and OFF /onboarding (an `about:blank` start —
      // e.g. a --steps subset run — needs the explicit goto, not just "not onboarding").
      await pollUntil(
        async () => page.url().startsWith(this.baseUrl) && !page.url().includes("/onboarding"),
        20000,
        "route away from /onboarding",
      ).catch(async () => {
        await page.goto(`${this.baseUrl}/`, { waitUntil: "domcontentloaded" });
      });
    }
    await this.waitForSelector(".reel", 40000);
    await this.screenshot("05-reel");
    const fellBack = this.hasConsoleEvent("reel_feed_fallback_global");
    if (expectPersonalized && fellBack) {
      throw new Error("expected the personalized daily_feeds path but saw reel_feed_fallback_global");
    }
    if (!expectPersonalized && !fellBack) {
      // First pass for a fresh user MUST fall back (no daily_feeds row yet) — a
      // personalized hit here means stale state leaked between runs.
      throw new Error("expected reel_feed_fallback_global for a fresh user but the event never fired");
    }
    return expectPersonalized
      ? "reel loaded from the user's personalized daily_feeds (no global fallback)"
      : "reel loaded with the documented fresh-user fallback to the global feed";
  }

  /** Fail-loud helper: media-element states + every digest-audio network entry seen. */
  async describeAudioState(): Promise<string> {
    const elementStates = await this.mustPage()
      .evaluate(() =>
        Array.from(document.querySelectorAll("audio"))
          .map((audio) => ({
            is_paused: audio.paused,
            current_time: audio.currentTime,
            ready_state: audio.readyState,
            network_state: audio.networkState,
            media_error_code: audio.error?.code ?? null,
            current_src_tail: audio.currentSrc.slice(-60),
          }))
          .filter((diag) => diag.current_src_tail !== "" || !diag.is_paused),
      )
      .catch(() => "unavailable" as const);
    const audioResponses = this.responseLog
      .filter((response) => response.response_url.includes("digest-audio"))
      .map((response) => ({ status: response.status, url_tail: response.response_url.slice(-60) }));
    return JSON.stringify({ elements: elementStates, digest_audio_responses: audioResponses }).slice(0, 1200);
  }

  async stepReelPlayback(): Promise<string> {
    const page = this.mustPage();
    await this.clickByAriaLabel("Tap to start your briefing", 30000);
    await this.waitForConsoleEvent("reel_audio_unlocked", 10000);
    // Phase 1: an <audio> element must be unpaused (play() accepted). Distinct from
    // the clock probe — an unpaused element can sit at currentTime 0 while the mp3
    // is still buffering from Supabase storage, which must not read as "no playback".
    await pollUntil(
      async () => page.evaluate(() => Array.from(document.querySelectorAll("audio")).some((audio) => !audio.paused)),
      20000,
      "an unpaused <audio> element",
    ).catch(async (error) => {
      throw new Error(`${String(error)}; audio diagnostics: ${await this.describeAudioState()}`);
    });
    // Phase 2: the audio CLOCK must actually advance (buffer cold-start can take a
    // while in headless, so poll rather than sampling a fixed 2.5s window).
    const probeAudioClock = (): Promise<number | null> =>
      page.evaluate(() => {
        const audios = Array.from(document.querySelectorAll("audio"));
        const active = audios.find((audio) => !audio.paused);
        return active ? active.currentTime : null;
      });
    const firstSample = await pollUntil(
      async () => {
        const clock = await probeAudioClock();
        return clock !== null && clock > 0 ? clock : null;
      },
      45000,
      "the active <audio> clock to start advancing (currentTime > 0)",
    ).catch(async (error) => {
      throw new Error(`${String(error)}; audio diagnostics: ${await this.describeAudioState()}`);
    });
    const secondSample = await pollUntil(
      async () => {
        const clock = await probeAudioClock();
        return clock !== null && clock > firstSample ? clock : null;
      },
      15000,
      `the audio clock to advance past ${firstSample.toFixed(2)}s`,
    );
    // Karaoke caption renders and its word highlighting tracks the clock.
    const captionBefore = await page.evaluate(() => document.querySelector(".cap-wrap .caption")?.innerHTML ?? "");
    if (!captionBefore.trim()) {
      throw new Error("karaoke caption (.cap-wrap .caption) is empty during playback");
    }
    await sleep(3000);
    const captionAfter = await page.evaluate(() => document.querySelector(".cap-wrap .caption")?.innerHTML ?? "");
    if (captionAfter === captionBefore) {
      throw new Error("karaoke caption did not change over 3s of playback (word sync broken)");
    }
    await this.screenshot("06-playback");
    // Advance two stories via the snap container.
    for (let advance = 1; advance <= 2; advance += 1) {
      await page.evaluate((targetIndex) => {
        const container = document.querySelector<HTMLElement>(".snap-y");
        container?.scrollTo({ top: container.clientHeight * targetIndex, behavior: "instant" as ScrollBehavior });
      }, advance);
      await sleep(1500);
    }
    const visibleIndex = await page.evaluate(() => {
      const container = document.querySelector<HTMLElement>(".snap-y");
      if (!container) {
        return -1;
      }
      return Math.round(container.scrollTop / container.clientHeight);
    });
    if (visibleIndex < 2) {
      throw new Error(`story advance failed (visible index ${visibleIndex} after 2 scrolls)`);
    }
    await this.screenshot("07-story-3");
    return `audio clock advanced ${firstSample.toFixed(1)}s → ${secondSample.toFixed(1)}s; captions rendered; advanced to story index ${visibleIndex}`;
  }

  async stepArticleLayer(): Promise<string> {
    const page = this.mustPage();
    // Open the full article by tapping the active story's headline (the
    // headline IS the tap target — the explicit .tap-cue hint was removed).
    await pollUntil(
      async () =>
        page.evaluate(() => {
          const headlineButtons = Array.from(
            document.querySelectorAll<HTMLElement>('[aria-label="Open the full article"]'),
          );
          const visible = headlineButtons.find((button) => button.getBoundingClientRect().height > 0);
          if (!visible) {
            return false;
          }
          visible.click();
          return true;
        }),
      15000,
      'visible [aria-label="Open the full article"]',
    );
    try {
      await this.waitForSelector(".layer-article.on", 15000);
      await this.waitForConsoleEvent("article_layer_fetch_succeeded", 30000);
      await this.screenshot("08-article");
    } finally {
      // Always close the layer (pass OR fail): a failed detail fetch must not leave
      // `.layer-article.on` covering the reel and contaminate the independent
      // text-QA / voice-live steps that follow.
      await page
        .evaluate(() => document.querySelector<HTMLElement>('[aria-label="Back to reel"]')?.click())
        .catch(() => undefined);
    }
    await pollUntil(
      async () => page.evaluate(() => document.querySelector(".layer-article.on") === null),
      10000,
      "article layer closed",
    );
    return "article layer opened, body fetched + rendered, closed back to the reel";
  }

  async stepTextQa(): Promise<string> {
    const page = this.mustPage();
    await this.clickByAriaLabel("Type a question");
    await this.waitForSelector('input[aria-label="Ask a question about this story"]', 15000);
    await page.evaluate((question) => {
      const input = document.querySelector<HTMLInputElement>('input[aria-label="Ask a question about this story"]');
      if (!input) {
        return;
      }
      const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
      nativeSetter?.call(input, question);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }, this.profile.text_question);
    await this.clickByAriaLabel("Send question");
    const answered = await pollUntil(
      async () =>
        page.evaluate(() => {
          if (document.querySelector(".bub-a")) {
            return "grounded";
          }
          if (document.querySelector(".refusal")) {
            return "refusal";
          }
          return false;
        }),
      90000,
      "Q&A answer or refusal bubble",
    );
    // The answer bubble shell renders (typing dots) BEFORE the worker responds, so
    // the bubble poll can resolve while the POST is still in flight — poll for the
    // response instead of asserting it synchronously.
    const qaResponse = await pollUntil(
      async () => this.responseLog.find((response) => response.response_url.includes("/question")) ?? null,
      60000,
      "POST /api/story/{id}/question response",
    );
    if (qaResponse.status !== 200) {
      throw new Error(`Q&A endpoint did not return 200 (got ${qaResponse.status})`);
    }
    await this.screenshot("09-text-qa");
    // Close the sheet to restore the reel for the voice step.
    await page.evaluate(() => document.querySelector<HTMLElement>(".sheet-scrim.on")?.click());
    await sleep(500);
    return `text Q&A returned 200 with a ${answered} bubble (grounding endpoint live)`;
  }

  async stepVoiceLive(): Promise<string> {
    const page = this.mustPage();
    // The ask sheet can open (and tear down) an EARLIER voice WS during the text-QA
    // step — snapshot existing WS records so we only bind to one created by THIS click.
    const preexistingWsRequestIds = new Set(this.wsRecords.keys());
    await this.clickByAriaLabel("Ask with your voice");
    // Token mint (the worker is the only minting path).
    await pollUntil(
      async () => this.responseLog.find((response) => response.response_url.includes("/api/voice/live-token") && response.status === 200) ?? null,
      30000,
      "voice live-token mint (HTTP 200)",
    );
    // Gemini Live constrained WS with the ephemeral token — NEW records only.
    const wsRecord = await pollUntil(
      async () =>
        Array.from(this.wsRecords.entries()).find(
          ([requestId, record]) =>
            !preexistingWsRequestIds.has(requestId) &&
            record.ws_url.includes("BidiGenerateContentConstrained") &&
            record.ws_url.includes("access_token="),
        )?.[1] ?? null,
      20000,
      "Gemini Live constrained WebSocket (new for this session)",
    );
    // Setup frame declares the grounding tool; server must ack with setupComplete.
    await pollUntil(
      async () => wsRecord.sent_frames.some((frame) => frame.includes('"setup"') && frame.includes("ask_about_story")),
      15000,
      "setup frame declaring ask_about_story",
    );
    await pollUntil(async () => wsRecord.received_frames.some((frame) => frame.includes("setupComplete")), 15000, "setupComplete ack");
    // The greeting nudge makes the model respond with zero real mic speech.
    await pollUntil(
      async () => page.evaluate(() => document.querySelector(".orb.story.responding") !== null),
      45000,
      "model response (orb .responding)",
    );
    await this.screenshot("10-voice-responding");
    const voiceErrors = this.consoleRecords.filter(
      (record) => record.message_text.includes("voice_live_") && record.message_type === "error",
    );
    if (voiceErrors.length > 0) {
      throw new Error(`voice_live error events during the session: ${voiceErrors[0].message_text.slice(0, 200)}`);
    }
    await this.clickButtonByText("END VOICE");
    await pollUntil(async () => wsRecord.is_closed, 15000, "WebSocket closed after END");
    return "voice session: token minted, constrained WS connected, ask_about_story declared, setupComplete acked, model responded, clean teardown";
  }

  async stepPersonalizedFeed(): Promise<string> {
    const page = this.mustPage();
    // Fresh load so the feed provider re-resolves; the fallback event must NOT fire.
    this.consoleRecords = [];
    await page.goto(`${this.baseUrl}/`, { waitUntil: "domcontentloaded" });
    await this.waitForSelector(".reel", 40000);
    await sleep(2000);
    if (this.hasConsoleEvent("reel_feed_fallback_global")) {
      throw new Error("personalized pass still fell back to the global feed");
    }
    // DB truth: the reel's first headline must match the user's daily_feeds position 1.
    const serviceClient = createClient(
      process.env.SUPABASE_URL ?? requireEnv("NEXT_PUBLIC_SUPABASE_URL"),
      requireEnv("SUPABASE_SERVICE_ROLE_KEY"),
      { auth: { persistSession: false } },
    );
    const today = new Date().toISOString().slice(0, 10);
    const { data, error } = await serviceClient
      .from("daily_feeds")
      .select("feed_story_id, feed_position, stories(story_headline)")
      .eq("feed_user_id", this.testUser.user_id)
      .eq("feed_date", today)
      .order("feed_position", { ascending: true })
      .limit(1);
    if (error || !data || data.length === 0) {
      throw new Error(`no daily_feeds row for ${this.profile.profile_name} on ${today}: ${error?.message ?? "empty"}`);
    }
    const expectedHeadline = (data[0] as unknown as { stories: { story_headline: string } }).stories.story_headline;
    const firstReelHeadline = await page.evaluate(() => document.querySelector(".headline")?.textContent?.trim() ?? "");
    if (!firstReelHeadline || firstReelHeadline !== expectedHeadline.trim()) {
      throw new Error(`reel position 1 "${firstReelHeadline}" != daily_feeds position 1 "${expectedHeadline}"`);
    }
    await this.screenshot("11-personalized-reel");
    return `personalized feed live: reel position 1 matches daily_feeds ("${firstReelHeadline.slice(0, 80)}")`;
  }

  async shutdown(): Promise<void> {
    try {
      await this.browser?.close();
    } catch {
      // already gone
    }
    this.chromeProcess?.kill("SIGTERM");
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  loadDotEnv();
  const args = parseArgs();

  const profilesFile = JSON.parse(readFileSync(path.join(REPO_ROOT, "scripts", "e2e", "profiles.json"), "utf8")) as {
    profiles: E2eProfileDefinition[];
  };
  const profile = profilesFile.profiles.find((candidate) => candidate.profile_name === args.profile_name);
  if (!profile) {
    throw new Error(`unknown profile ${args.profile_name}`);
  }
  const testUsers = JSON.parse(readFileSync(path.join(E2E_STATE_DIR, "test-users.json"), "utf8")) as SeededTestUser[];
  const testUser = testUsers.find((candidate) => candidate.profile_name === args.profile_name);
  if (!testUser) {
    throw new Error(`no seeded test user for ${args.profile_name} — run scripts/e2e/seed-test-users.ts first`);
  }

  const requestedSteps = args.steps ?? [...FIRST_PASS_STEPS, ...(args.expect_personalized ? ["personalized_feed"] : [])];
  const driver = new ProfileDriver(profile, testUser, args.base_url);
  const results: StepResult[] = [];
  const failedSteps = new Set<string>();

  console.log(JSON.stringify({ event: "e2e_drive_started", profile_name: profile.profile_name, steps: requestedSteps }));
  await driver.boot(args.headed);

  const stepRunners: Record<string, () => Promise<string>> = {
    onboarding_splash: () => driver.stepOnboardingSplash(),
    picker: () => driver.stepPicker(),
    sources: () => driver.stepSources(),
    build_30: () => driver.stepBuild30(),
    reel_loads: () => driver.stepReelLoads(args.expect_personalized),
    reel_playback: () => driver.stepReelPlayback(),
    article_layer: () => driver.stepArticleLayer(),
    text_qa: () => driver.stepTextQa(),
    voice_live: () => driver.stepVoiceLive(),
    personalized_feed: () => driver.stepPersonalizedFeed(),
  };

  for (const stepName of requestedSteps) {
    const runner = stepRunners[stepName];
    if (!runner) {
      results.push({ step_name: stepName, status: "skipped", evidence: "unknown step name", artifacts: [], duration_ms: 0 });
      continue;
    }
    const isBlocked = (STEP_DEPENDENCIES[stepName] ?? []).some(
      (dependency) => failedSteps.has(dependency) && requestedSteps.includes(dependency),
    );
    if (isBlocked) {
      failedSteps.add(stepName); // transitively block downstream steps
      results.push({ step_name: stepName, status: "blocked", evidence: "a prerequisite step failed", artifacts: [], duration_ms: 0 });
      console.log(JSON.stringify({ event: "e2e_step_blocked", profile_name: profile.profile_name, step_name: stepName }));
      continue;
    }
    const startedAt = Date.now();
    try {
      const evidence = await runner();
      results.push({ step_name: stepName, status: "pass", evidence, artifacts: [], duration_ms: Date.now() - startedAt });
      console.log(JSON.stringify({ event: "e2e_step_passed", profile_name: profile.profile_name, step_name: stepName }));
    } catch (error) {
      failedSteps.add(stepName);
      const shotPath = await driver.screenshot(`FAIL-${stepName}`);
      const telemetryPaths = driver.dumpTelemetry(`FAIL-${stepName}`);
      results.push({
        step_name: stepName,
        status: "fail",
        evidence: error instanceof Error ? error.message : String(error),
        artifacts: [shotPath, ...telemetryPaths].filter(Boolean),
        duration_ms: Date.now() - startedAt,
      });
      console.log(
        JSON.stringify({
          event: "e2e_step_failed",
          profile_name: profile.profile_name,
          step_name: stepName,
          error_message: error instanceof Error ? error.message.slice(0, 300) : String(error),
          fix_suggestion: "Inspect the FAIL-* screenshot + console/network dumps in the profile's artifacts dir.",
        }),
      );
    }
  }

  const consoleErrorCount = driver.consoleRecords.filter((record) => record.message_type === "error").length;
  const resultPayload = {
    profile_name: profile.profile_name,
    base_url: args.base_url,
    expect_personalized: args.expect_personalized,
    steps: results,
    console_error_count: consoleErrorCount,
    page_errors: driver.pageErrors,
    failed_requests: driver.failedRequests,
    error_responses: driver.errorResponses,
  };
  const resultPath = path.join(E2E_STATE_DIR, `${profile.profile_name}-result.json`);
  writeFileSync(resultPath, `${JSON.stringify(resultPayload, null, 2)}\n`);

  await driver.shutdown();

  const allPassed = results.every((result) => result.status === "pass" || result.status === "skipped");
  console.log(
    JSON.stringify({
      event: "e2e_drive_completed",
      profile_name: profile.profile_name,
      all_passed: allPassed,
      result_file: path.relative(REPO_ROOT, resultPath),
    }),
  );
  process.exit(allPassed ? 0 : 1);
}

main().catch((error: unknown) => {
  console.error(
    JSON.stringify({
      event: "e2e_drive_crashed",
      error_message: error instanceof Error ? error.message : String(error),
      fix_suggestion: "Infra-class failure (Chrome/CDP/auth) — check the dev server is up and the seed ran.",
    }),
  );
  process.exit(2);
});
