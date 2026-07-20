/**
 * Build stamp — the visible build identity rendered in Settings (issue #33).
 *
 * The blessed iOS build path (`npm run build:ios` → `scripts/ios/buildIos.ts`)
 * bakes `NEXT_PUBLIC_BUILD_ID` as `<short-sha>[-dirty].<epoch-seconds>` into the
 * static export at build time; Next.js inlines the env read below, so the value
 * is a compile-time constant — never fetched at runtime. The dev server (no
 * baked env) renders the literal "dev".
 */

/** Baked id shape: `<short-sha>[-dirty].<unix-epoch-seconds>`. */
const BUILD_ID_PATTERN = /^([0-9a-f]{4,40}(?:-dirty)?)\.(\d{1,13})$/;

/**
 * Format a raw baked build id into the Settings footer stamp.
 *
 * @param rawBuildId - The baked `NEXT_PUBLIC_BUILD_ID` value, e.g.
 *   `"a1b2c3d.1783502063"` or `"a1b2c3d-dirty.1783502063"`.
 * @returns `"<sha> · <YYYY-MM-DD HH:MM> UTC"`, or the literal `"dev"` when the
 *   id is unset, empty, or malformed (never throws — the footer must render).
 *
 * @example
 * formatBuildStamp("a1b2c3d.1783502063") // "a1b2c3d · 2026-07-07 09:14 UTC"
 * formatBuildStamp(undefined)            // "dev"
 */
export function formatBuildStamp(rawBuildId: string | undefined): string {
  if (!rawBuildId) {
    return "dev";
  }
  const match = BUILD_ID_PATTERN.exec(rawBuildId);
  if (match === null) {
    return "dev";
  }
  const [, shortSha, epochSeconds] = match;
  const builtAt = new Date(Number(epochSeconds) * 1000);
  if (Number.isNaN(builtAt.getTime())) {
    return "dev";
  }
  const builtAtIso = builtAt.toISOString(); // "2026-07-07T09:14:23.000Z"
  return `${shortSha} · ${builtAtIso.slice(0, 10)} ${builtAtIso.slice(11, 16)} UTC`;
}

/**
 * The formatted stamp for THIS bundle. `process.env.NEXT_PUBLIC_BUILD_ID` is
 * inlined by Next.js at build time (static export), so this is baked, not read.
 */
export const BUILD_STAMP: string = formatBuildStamp(process.env.NEXT_PUBLIC_BUILD_ID);
