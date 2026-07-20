/**
 * Issue #33 browser walkthrough (temporary — deleted after the run).
 *
 * Signs in the seeded e2e profile, temporarily copies its most recent
 * daily_feeds rows to today (so the real reel renders and the wordmark →
 * Settings entry exists), drives to Settings, asserts the build stamp footer
 * ("dev" on the dev server), screenshots it, then deletes the copied rows.
 *
 * Run from repo root: npx tsx .agents/debug/issue-33/walkthrough.ts
 */
import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { createClient } from "@supabase/supabase-js";
import { chromium } from "playwright-core";
import { loadDotEnv, requireEnv } from "../../../scripts/e2e/env";

const CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const BASE_URL = "http://localhost:3111";
const CDP_PORT = 9333;
const SCREENSHOT_DIR = ".agents/debug/issue-33";

function todayLocalDate(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

async function main(): Promise<void> {
  loadDotEnv();
  const testUsers = JSON.parse(readFileSync(".agents/e2e/state/test-users.json", "utf8"));
  // profile-c is the only seeded profile with historical daily_feeds rows to copy.
  const testUser = testUsers.find((candidate: { profile_name: string }) => candidate.profile_name === "profile-c-markets-geo");
  if (!testUser) throw new Error("profile-c-markets-geo not found in test-users.json");

  const serviceClient = createClient(requireEnv("NEXT_PUBLIC_SUPABASE_URL"), requireEnv("SUPABASE_SERVICE_ROLE_KEY"), {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  // ── temporary feed rows so the reel (and its Settings entry) renders ──
  const { data: recentRows, error: selectError } = await serviceClient
    .from("daily_feeds")
    .select("feed_story_id, feed_score, feed_slot_kind")
    .eq("feed_user_id", testUser.user_id)
    .order("feed_date", { ascending: false })
    .limit(3);
  if (selectError || !recentRows || recentRows.length === 0) {
    throw new Error(`no historical feed rows for ${testUser.profile_name}: ${selectError?.message ?? "empty"}`);
  }
  const feedDate = todayLocalDate();
  // Clear any leftover seeded rows from a previous run before inserting.
  await serviceClient.from("daily_feeds").delete().eq("feed_user_id", testUser.user_id).eq("feed_date", feedDate);
  const insertedIds: string[] = [];
  for (const [rowIndex, row] of recentRows.entries()) {
    const { data: inserted, error: insertError } = await serviceClient
      .from("daily_feeds")
      .insert({
        feed_user_id: testUser.user_id,
        feed_story_id: row.feed_story_id,
        feed_date: feedDate,
        feed_position: rowIndex + 1,
        feed_score: row.feed_score,
        feed_slot_kind: row.feed_slot_kind,
      })
      .select("daily_feed_id")
      .single();
    if (insertError) {
      console.log(JSON.stringify({ event: "walkthrough_seed_row_skipped", error_message: insertError.message }));
      continue;
    }
    insertedIds.push(inserted.daily_feed_id);
  }
  if (insertedIds.length === 0) throw new Error("could not seed any temporary feed rows");
  console.log(JSON.stringify({ event: "walkthrough_seeded_rows", total: insertedIds.length, feed_date: feedDate }));

  const cleanupRows = async (): Promise<void> => {
    const { data: deletedRows, error: deleteError } = await serviceClient
      .from("daily_feeds")
      .delete()
      .eq("feed_user_id", testUser.user_id)
      .eq("feed_date", feedDate)
      .select("daily_feed_id");
    console.log(
      JSON.stringify({ event: "walkthrough_cleanup", deleted: deletedRows?.length ?? 0, error: deleteError?.message ?? null }),
    );
  };

  // Sign in on a SEPARATE anon client — signInWithPassword on the service client
  // would swap its Authorization to the user token and break the RLS-bypassing cleanup.
  const anonClient = createClient(requireEnv("NEXT_PUBLIC_SUPABASE_URL"), requireEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY"), {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: signIn, error: signInError } = await anonClient.auth.signInWithPassword({
    email: testUser.email,
    password: testUser.password,
  });
  if (signInError || !signIn.session) {
    await cleanupRows();
    throw new Error(`signInWithPassword failed: ${signInError?.message ?? "no session"}`);
  }
  const projectRef = new URL(requireEnv("NEXT_PUBLIC_SUPABASE_URL")).hostname.split(".")[0];

  const userDataDir = path.join(os.tmpdir(), `issue33-chrome-${Date.now()}`);
  const chromeProcess: ChildProcess = spawn(
    CHROME_BINARY,
    [`--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${userDataDir}`, "--headless=new", "about:blank"],
    { stdio: "ignore" },
  );
  try {
    let browser = null;
    for (let attempt = 0; attempt < 20 && !browser; attempt++) {
      try {
        browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    if (!browser) throw new Error("could not connect to Chrome over CDP");
    const context = browser.contexts()[0];
    const page = context.pages()[0] ?? (await context.newPage());
    page.setViewportSize({ width: 390, height: 844 });

    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await page.evaluate(
      ({ storage_key, session_json }) => {
        window.localStorage.setItem(storage_key, session_json);
      },
      { storage_key: `sb-${projectRef}-auth-token`, session_json: JSON.stringify(signIn.session) },
    );
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });

    // Dismiss the "tap to start" overlay if it is gating the reel (it mounts
    // after the feed loads, so WAIT for it rather than polling isVisible once).
    const tapStartOverlay = page.locator('button[aria-label="Tap to start your briefing"]');
    const overlayAppeared = await tapStartOverlay
      .waitFor({ state: "visible", timeout: 20000 })
      .then(() => true)
      .catch(() => false);
    if (overlayAppeared) {
      await tapStartOverlay.click();
      await tapStartOverlay.waitFor({ state: "hidden", timeout: 10000 }).catch(() => {});
    }
    // The wordmark button on the reel opens the library at the Settings tab.
    const accountButton = page.locator('button[aria-label="Open account"]').first();
    await accountButton.waitFor({ state: "visible", timeout: 30000 });
    await accountButton.click();

    await page.waitForSelector(".set-ver", { timeout: 10000 });
    const footerText = await page.locator(".set-ver").textContent();
    console.log(JSON.stringify({ event: "settings_footer_text", footer_text: footerText }));
    if (!footerText || !footerText.includes("dev")) {
      throw new Error(`stamp missing from footer: "${footerText}"`);
    }
    await page.locator(".set-ver").scrollIntoViewIfNeeded();
    if (!existsSync(SCREENSHOT_DIR)) mkdirSync(SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "settings-build-stamp-dev.png") });
    console.log(JSON.stringify({ event: "walkthrough_passed", footer_text: footerText }));
    await browser.close();
  } finally {
    chromeProcess.kill();
    await cleanupRows();
  }
}

main().catch((walkthroughError) => {
  console.error(JSON.stringify({ event: "walkthrough_failed", error_message: String(walkthroughError) }));
  process.exit(1);
});
