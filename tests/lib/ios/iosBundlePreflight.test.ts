import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

/**
 * Fixture-dir tests for the iOS stale-bundle preflight (issue #33).
 *
 * Rule 9 — WHY (each fails on a real regression):
 *   - The preflight exists because on 2026-07-05 a stale `ios/App/App/public`
 *     bundle shipped to the founder's iPhone and "lost" the 5-tab nav. It must
 *     FAIL when the native bundle lacks the freshly baked build id — a
 *     preflight that passes on a stale bundle is worse than none.
 *   - It compares the baked build id INSIDE the two bundles, never mtimes, so
 *     a copy that preserves timestamps (or a filesystem that doesn't) can't
 *     fool it.
 *   - Every failure names the fix — the founder acts on the message, not on
 *     an exit code.
 */

import { verifyIosBundleFreshness } from "../../../scripts/ios/iosBundlePreflight";

const BUILD_ID = "a1b2c3d.1783502063";

let fixtureRoot: string;
let webDir: string;
let nativeDir: string;

/** Write a minimal exported-bundle fixture: one JS chunk carrying the build id. */
function writeBundleFixture(bundleDir: string, bakedBuildId: string): void {
  mkdirSync(path.join(bundleDir, "_next", "static", "chunks"), { recursive: true });
  writeFileSync(path.join(bundleDir, "_next", "static", "chunks", "main-abc123.js"), `var BUILD_ID="${bakedBuildId}";`);
  writeFileSync(path.join(bundleDir, "index.html"), "<html></html>");
}

beforeEach(() => {
  fixtureRoot = mkdtempSync(path.join(tmpdir(), "blip-preflight-"));
  webDir = path.join(fixtureRoot, "out");
  nativeDir = path.join(fixtureRoot, "ios", "App", "App", "public");
});

afterEach(() => {
  rmSync(fixtureRoot, { recursive: true, force: true });
});

describe("verifyIosBundleFreshness", () => {
  it("passes when both bundles carry the same baked build id (happy path)", () => {
    writeBundleFixture(webDir, BUILD_ID);
    writeBundleFixture(nativeDir, BUILD_ID);
    const result = verifyIosBundleFreshness(webDir, nativeDir, BUILD_ID);
    expect(result.ok).toBe(true);
  });

  it("fails with a fix message when the native bundle is stale (old build id)", () => {
    writeBundleFixture(webDir, BUILD_ID);
    writeBundleFixture(nativeDir, "0ldsha0.1700000000");
    const result = verifyIosBundleFreshness(webDir, nativeDir, BUILD_ID);
    expect(result.ok).toBe(false);
    expect(result.fix_suggestion).toContain("npx cap sync ios");
  });

  it("fails when the native bundle dir is missing entirely (sync never ran)", () => {
    writeBundleFixture(webDir, BUILD_ID);
    const result = verifyIosBundleFreshness(webDir, nativeDir, BUILD_ID);
    expect(result.ok).toBe(false);
    expect(result.fix_suggestion).toContain("npx cap sync ios");
  });

  it("fails when the web output itself never baked the id (env not set during build)", () => {
    writeBundleFixture(webDir, "unstamped");
    writeBundleFixture(nativeDir, "unstamped");
    const result = verifyIosBundleFreshness(webDir, nativeDir, BUILD_ID);
    expect(result.ok).toBe(false);
    expect(result.failure_reason).toContain("web");
  });

  it("fails when the web output dir is missing (build never ran)", () => {
    const result = verifyIosBundleFreshness(webDir, nativeDir, BUILD_ID);
    expect(result.ok).toBe(false);
    expect(result.failure_reason).toContain("web");
  });
});
