import { describe, expect, it } from "vitest";

/**
 * Phase 5c SP-UI — source-swipe data shaping (`sourceSwipeData`). These cover the
 * PURE, load-bearing `computeMatchPct` formula (the % match badge) — the deck/IO
 * wiring is covered in `tests/lib/sources/sourceSwipe.test.tsx`.
 *
 * Rule 9 — WHY this matters: the badge is the user's only signal of "how well does
 * this fit me?". The formula must (a) reward a strong archetype fit, (b) let the
 * in-archetype popularity rank order the badges, and (c) stay in a believable
 * 60–99 band (never a fake 100% nor a demoralizing single digit on a card we chose
 * to SHOW). A formula that clamped wrong, ignored the archetype, or emitted 100
 * would mislead the user — these assert each property.
 */

import { computeMatchPct } from "@/lib/sourceSwipeData";

describe("computeMatchPct — the % match badge formula", () => {
  it("rewards a strong archetype fit over a weak one (same popularity)", () => {
    const strong = computeMatchPct(80, 1.0);
    const weak = computeMatchPct(80, 0.2);
    expect(strong).toBeGreaterThan(weak);
  });

  it("lets in-archetype popularity order the badge (same archetype fit)", () => {
    const popular = computeMatchPct(95, 0.8);
    const obscure = computeMatchPct(30, 0.8);
    expect(popular).toBeGreaterThan(obscure);
  });

  it("clamps to the believable 60–99 band — never below 60, never a fake 100", () => {
    // A worst-case card (zero popularity, zero archetype fit) still floors at 60.
    expect(computeMatchPct(0, 0)).toBe(60);
    // A best-case card (max popularity, perfect fit) ceilings at 99, not 100.
    expect(computeMatchPct(100, 1)).toBe(99);
    // A mid case lands inside the band as an integer.
    const mid = computeMatchPct(70, 0.7);
    expect(mid).toBeGreaterThanOrEqual(60);
    expect(mid).toBeLessThanOrEqual(99);
    expect(Number.isInteger(mid)).toBe(true);
  });
});
