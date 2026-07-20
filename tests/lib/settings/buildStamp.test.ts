import { describe, expect, it } from "vitest";

/**
 * Unit tests for the build stamp formatter (issue #33).
 *
 * Rule 9 — WHY (each fails on a real regression):
 *   - The stamp is the founder's on-device proof of WHICH bundle is installed
 *     (the 2026-07-05 stale-bundle incident). A wrong sha or a silently
 *     swallowed parse error defeats the whole point.
 *   - The dev fallback must be the literal "dev" — never a crash, never a
 *     half-formatted string — so the dev server (no baked env) stays usable.
 */

import { formatBuildStamp } from "@/lib/buildStamp";

describe("formatBuildStamp", () => {
  it("formats sha + epoch into '<sha> · <utc time>' (happy path)", () => {
    // 1783502063 = 2026-07-08T09:14:23Z
    expect(formatBuildStamp("a1b2c3d.1783502063")).toBe("a1b2c3d · 2026-07-08 09:14 UTC");
  });

  it("keeps the -dirty suffix on the sha so uncommitted builds are visible", () => {
    expect(formatBuildStamp("a1b2c3d-dirty.1783502063")).toBe("a1b2c3d-dirty · 2026-07-08 09:14 UTC");
  });

  it("falls back to 'dev' when the env is unset or empty (dev server)", () => {
    expect(formatBuildStamp(undefined)).toBe("dev");
    expect(formatBuildStamp("")).toBe("dev");
  });

  it("falls back to 'dev' on malformed ids instead of rendering garbage", () => {
    expect(formatBuildStamp("not-a-build-id")).toBe("dev");
    expect(formatBuildStamp("a1b2c3d.")).toBe("dev");
    expect(formatBuildStamp(".1783502063")).toBe("dev");
    expect(formatBuildStamp("a1b2c3d.notanepoch")).toBe("dev");
  });
});
