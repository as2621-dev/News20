/**
 * Blessed iOS build path (issue #33): `npm run build:ios`.
 *
 * 1. Computes the build id from git: `<short-sha>[-dirty].<epoch-seconds>`.
 * 2. Runs `next build` with `NEXT_PUBLIC_BUILD_ID` baked in (static export → `out/`).
 * 3. Runs `cap sync ios` (copies `out/` → `ios/App/App/public/`).
 * 4. Preflight: fails NON-ZERO unless both bundles carry the freshly baked id
 *    ({@link verifyIosBundleFreshness}) — the durable fix for the 2026-07-05
 *    stale-bundle install.
 *
 * Fails loudly (no silent "dev" bundle) if git is unavailable or `ios/` is
 * missing. The web-only `npm run build` is untouched — this gate is iOS-only.
 */

import { execFileSync, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { verifyIosBundleFreshness } from "./iosBundlePreflight";

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const WEB_EXPORT_DIR = path.join(REPO_ROOT, "out");
const NATIVE_BUNDLE_DIR = path.join(REPO_ROOT, "ios", "App", "App", "public");

/** Log a structured line; the build script's stdout is its UI. */
function logStep(event_name: string, fields: Record<string, string>): void {
  console.log(JSON.stringify({ event: event_name, ...fields }));
}

/** Exit non-zero with the failure and the fix, both named. */
function failBuild(failure_reason: string, fix_suggestion: string): never {
  console.error(JSON.stringify({ event: "build_ios_failed", failure_reason, fix_suggestion }));
  process.exit(1);
}

/**
 * Compute the build id from git state: `<short-sha>[-dirty].<epoch-seconds>`.
 * A missing git context is a hard error — a bundle without identity is exactly
 * the failure mode this script exists to prevent.
 */
function computeBuildId(): string {
  let shortSha: string;
  let isDirty: boolean;
  try {
    shortSha = execFileSync("git", ["rev-parse", "--short", "HEAD"], { cwd: REPO_ROOT, encoding: "utf8" }).trim();
    isDirty = execFileSync("git", ["status", "--porcelain"], { cwd: REPO_ROOT, encoding: "utf8" }).trim().length > 0;
  } catch (gitError: unknown) {
    failBuild(
      `git unavailable: ${gitError instanceof Error ? gitError.message : "unknown"}`,
      "Run the iOS build from the git checkout — the bundle must carry a real sha.",
    );
  }
  const epochSeconds = Math.floor(Date.now() / 1000);
  return `${shortSha}${isDirty ? "-dirty" : ""}.${epochSeconds}`;
}

/** Run a build step inheriting stdio; fail the whole build if it exits non-zero. */
function runStep(stepName: string, command: string, commandArgs: string[], extraEnv: Record<string, string>): void {
  logStep("build_ios_step", { step: stepName, command: `${command} ${commandArgs.join(" ")}` });
  const stepResult = spawnSync(command, commandArgs, {
    cwd: REPO_ROOT,
    stdio: "inherit",
    env: { ...process.env, ...extraEnv },
  });
  if (stepResult.status !== 0) {
    failBuild(
      `step "${stepName}" exited with ${stepResult.status ?? "signal"}`,
      `Fix the ${stepName} failure above and re-run \`npm run build:ios\`.`,
    );
  }
}

function main(): void {
  if (!existsSync(path.join(REPO_ROOT, "ios", "App"))) {
    failBuild(
      "ios/App project missing — nothing to sync into",
      "Run `npx cap add ios` on a Mac with Xcode first, then re-run `npm run build:ios`.",
    );
  }

  const buildId = computeBuildId();
  logStep("build_ios_started", { build_id: buildId });

  runStep("next build", "npx", ["next", "build"], { NEXT_PUBLIC_BUILD_ID: buildId });
  runStep("cap sync ios", "npx", ["cap", "sync", "ios"], {});

  const preflightResult = verifyIosBundleFreshness(WEB_EXPORT_DIR, NATIVE_BUNDLE_DIR, buildId);
  if (!preflightResult.ok) {
    failBuild(
      preflightResult.failure_reason ?? "preflight failed",
      preflightResult.fix_suggestion ?? "Re-run `npm run build:ios`.",
    );
  }

  logStep("build_ios_verified", {
    build_id: buildId,
    native_bundle: NATIVE_BUNDLE_DIR,
    next_step: "Open ios/App in Xcode and Run/Archive — the synced bundle is current.",
  });
}

main();
