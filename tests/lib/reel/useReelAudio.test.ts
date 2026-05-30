import { describe, expect, it } from "vitest";
import { computeNextReelState } from "@/lib/reel/useReelAudio";

/**
 * Unit tests for the auto-advance decision seam.
 *
 * Rule 9 — these encode WHY the behaviour matters, not just what it returns:
 * the reel is a FINITE briefing whose whole point is a reachable "all caught up"
 * finish line. Auto-advance must therefore:
 *   - move to exactly the NEXT story when a non-last story ends (never skip),
 *   - report caught-up EXACTLY when the LAST story ends (never early, never loop
 *     back to the start),
 *   - keep `nextIndex` in range so a caller that still reads it can't crash.
 * The real `<audio>` `ended` event can't be driven under jsdom, which is why the
 * decision lives in this pure function and is tested here in isolation.
 */
describe("computeNextReelState — auto-advance reaches the finish line exactly at the last story", () => {
  it("advances to the next index when a non-last story ends (no skipping)", () => {
    // WHY: ending story 0 of 5 must play story 1 next — the briefing proceeds one
    // story at a time, in order.
    const result = computeNextReelState(0, 5);
    expect(result).toEqual({ nextIndex: 1, isCaughtUp: false });
  });

  it("advances by exactly one across the interior of the feed", () => {
    // WHY: no off-by-one anywhere in the middle — story i ends → story i+1.
    for (let currentIndex = 0; currentIndex < 4; currentIndex += 1) {
      const result = computeNextReelState(currentIndex, 5);
      expect(result.nextIndex).toBe(currentIndex + 1);
      expect(result.isCaughtUp).toBe(false);
    }
  });

  it("reports caught-up when the LAST story ends, and does NOT loop to the start", () => {
    // WHY: the signature finish line. Ending the last story (index 4 of 5) must
    // signal caught-up — never wrap back to index 0 (that would make the reel
    // infinite, the exact anti-pattern blip rejects).
    const result = computeNextReelState(4, 5);
    expect(result.isCaughtUp).toBe(true);
    expect(result.nextIndex).not.toBe(0);
    // Clamped to the last valid index so any consumer reading it stays in range.
    expect(result.nextIndex).toBe(4);
  });

  it("treats a single-story feed as immediately caught-up on end (index 0, count 1)", () => {
    // WHY: boundary — the only story IS the last story; finishing it is the finish
    // line, and nextIndex must remain the valid index 0 (no -1, no overflow).
    const result = computeNextReelState(0, 1);
    expect(result.isCaughtUp).toBe(true);
    expect(result.nextIndex).toBe(0);
  });

  it("never returns a nextIndex past the last valid index, even if called past the end", () => {
    // WHY: defensive — a stray call with currentIndex beyond the last story must
    // still clamp to the last index and report caught-up, not return an
    // out-of-range index that would mount nothing / play no audio.
    const storyCount = 5;
    const result = computeNextReelState(7, storyCount);
    expect(result.isCaughtUp).toBe(true);
    expect(result.nextIndex).toBe(storyCount - 1);
  });

  it("matches the 5-fixture reel: only ending digest-5 (index 4) is the finish line", () => {
    // WHY: ties the unit to the real feed shape — indices 0..3 advance, 4 is done.
    const fixtureCount = 5;
    const caughtUpFlags = Array.from(
      { length: fixtureCount },
      (_unused, index) => computeNextReelState(index, fixtureCount).isCaughtUp,
    );
    expect(caughtUpFlags).toEqual([false, false, false, false, true]);
  });
});
