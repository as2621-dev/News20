/**
 * iOS stale-bundle preflight (issue #33).
 *
 * Verifies that the freshly built web export (`out/`) AND the synced native
 * bundle (`ios/App/App/public/`) both carry the SAME baked build id — by
 * searching the text assets for the id string itself, never by comparing
 * mtimes (copies can preserve or mangle timestamps; the baked id can't lie).
 *
 * Exists because on 2026-07-05 a stale native bundle shipped to the founder's
 * iPhone and the 5-tab nav "went missing". Called by `scripts/ios/buildIos.ts`
 * after `cap sync ios`; pure functions so tests drive it with fixture dirs.
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

/** File extensions that can carry the inlined build id (Next.js inlines NEXT_PUBLIC_* into JS). */
const TEXT_ASSET_EXTENSIONS = new Set([".js", ".mjs", ".html", ".json", ".txt"]);

export interface BundlePreflightResult {
  ok: boolean;
  /** What went wrong, naming the offending bundle ("web" / "native"). */
  failure_reason?: string;
  /** The command that fixes it — the founder acts on this, not the exit code. */
  fix_suggestion?: string;
}

/**
 * Recursively search a bundle directory's text assets for the build id string.
 *
 * @param bundleDir - Root of an exported web bundle (e.g. `out/`).
 * @param buildId - The exact baked id, e.g. `"a1b2c3d.1783502063"`.
 * @returns True if any text asset contains the id.
 */
export function bundleContainsBuildId(bundleDir: string, buildId: string): boolean {
  const entries = readdirSync(bundleDir, { withFileTypes: true });
  for (const entry of entries) {
    const entryPath = path.join(bundleDir, entry.name);
    if (entry.isDirectory()) {
      if (bundleContainsBuildId(entryPath, buildId)) {
        return true;
      }
    } else if (entry.isFile() && TEXT_ASSET_EXTENSIONS.has(path.extname(entry.name))) {
      if (readFileSync(entryPath, "utf8").includes(buildId)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Verify the web export and the synced native bundle both carry `buildId`.
 *
 * @param webDir - The Next.js static export dir (`out/`).
 * @param nativeDir - The Capacitor native bundle dir (`ios/App/App/public/`).
 * @param buildId - The id baked into THIS build via `NEXT_PUBLIC_BUILD_ID`.
 * @returns `{ ok: true }` when both match; otherwise a named failure + fix.
 *
 * @example
 * verifyIosBundleFreshness("out", "ios/App/App/public", "a1b2c3d.1783502063")
 * // { ok: false, failure_reason: "native bundle ... stale", fix_suggestion: "... npx cap sync ios" }
 */
export function verifyIosBundleFreshness(webDir: string, nativeDir: string, buildId: string): BundlePreflightResult {
  if (!existsSync(webDir)) {
    return {
      ok: false,
      failure_reason: `web build output missing at ${webDir}`,
      fix_suggestion: "Run `npm run build:ios` — it builds the web export before syncing.",
    };
  }
  if (!bundleContainsBuildId(webDir, buildId)) {
    return {
      ok: false,
      failure_reason: `web build output at ${webDir} does not contain build id ${buildId} — the id was never baked`,
      fix_suggestion: "Build via `npm run build:ios` so NEXT_PUBLIC_BUILD_ID is set during `next build`.",
    };
  }
  if (!existsSync(nativeDir)) {
    return {
      ok: false,
      failure_reason: `native bundle missing at ${nativeDir}`,
      fix_suggestion: "Run `npx cap sync ios` to copy the web export into the native project.",
    };
  }
  if (!bundleContainsBuildId(nativeDir, buildId)) {
    return {
      ok: false,
      failure_reason: `native bundle at ${nativeDir} is STALE — it does not contain build id ${buildId}`,
      fix_suggestion: "Run `npx cap sync ios` (or the full `npm run build:ios`) to re-sync, then re-archive in Xcode.",
    };
  }
  return { ok: true };
}
