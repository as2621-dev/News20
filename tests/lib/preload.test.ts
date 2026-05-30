import { describe, expect, it } from "vitest";
import { computePreloadIndices, DEFAULT_PRELOAD_LOOKAHEAD } from "@/lib/reel/preload";

/**
 * Unit tests for the audio preload window.
 *
 * Rule 9 — these encode WHY the window matters, not just what it returns: the
 * reel needs the NEXT 1–2 stories' audio buffered so auto-advance has no audible
 * gap (port-map §6), but it is a FINITE briefing — the window must never look
 * before the active story, never wrap past the last story, and never produce an
 * out-of-range index that the reel would mount as a broken `<audio>` source.
 * A test that only checked "returns an array" could not catch a wrap or an
 * off-by-one, so each case pins the exact indices the contract guarantees.
 */
describe("computePreloadIndices — preloads only the next in-range stories, never wrapping", () => {
  it("returns the next two indices at the start of the feed (0 → [1, 2])", () => {
    // WHY: the default lookahead is 2; from story 0 we buffer stories 1 and 2 so
    // the first two auto-advances are gap-free.
    expect(computePreloadIndices(0, 5)).toEqual([1, 2]);
  });

  it("returns [] on the last story — there is nothing ahead of the finish line", () => {
    // WHY: the reel is finite. On the last story the briefing is over; preloading
    // must NOT wrap back to index 0 (that is the infinite-feed anti-pattern blip
    // rejects) nor return an out-of-range index.
    expect(computePreloadIndices(4, 5)).toEqual([]);
  });

  it("returns only the last index on the second-last story (3 of 5 → [4])", () => {
    // WHY: from the second-last story only ONE story remains; the window clamps to
    // it instead of greedily returning a non-existent index 5.
    expect(computePreloadIndices(3, 5)).toEqual([4]);
  });

  it("never includes the active index itself", () => {
    // WHY: the active story is already loaded (preload="auto" via {activeIndex});
    // the window is strictly the UPCOMING stories, so the active index must be
    // absent at every position in the feed.
    const storyCount = 6;
    for (let activeIndex = 0; activeIndex < storyCount; activeIndex += 1) {
      expect(computePreloadIndices(activeIndex, storyCount)).not.toContain(activeIndex);
    }
  });

  it("never exceeds storyCount-1 and never returns a negative index, anywhere in the feed", () => {
    // WHY: defensive invariant — every emitted index must be a mountable story
    // slot. Walk the whole feed and assert the bounds hold for each result.
    const storyCount = 7;
    for (let activeIndex = 0; activeIndex < storyCount; activeIndex += 1) {
      for (const preloadIndex of computePreloadIndices(activeIndex, storyCount)) {
        expect(preloadIndex).toBeGreaterThanOrEqual(0);
        expect(preloadIndex).toBeLessThanOrEqual(storyCount - 1);
      }
    }
  });

  it("handles a single-story feed (count 1): nothing to preload from the only story", () => {
    // WHY: boundary — the only story is the last story, so the window is empty
    // (no -1, no overflow, no wrap).
    expect(computePreloadIndices(0, 1)).toEqual([]);
  });

  it("handles a two-story feed (count 2): story 0 → [1], story 1 → []", () => {
    // WHY: smallest feed where a single advance exists. Story 0 buffers story 1;
    // story 1 is the finish line with nothing ahead.
    expect(computePreloadIndices(0, 2)).toEqual([1]);
    expect(computePreloadIndices(1, 2)).toEqual([]);
  });

  it("honors a custom lookahead of 1 (0 → [1])", () => {
    // WHY: the lookahead is tunable; with 1 only the immediate next story buffers.
    expect(computePreloadIndices(0, 5, 1)).toEqual([1]);
  });

  it("clamps the window to the remaining stories when lookahead overshoots the end", () => {
    // WHY: a lookahead of 5 from story 3 of 5 must still stop at the last story —
    // the finite feed bounds the window, not the requested lookahead.
    expect(computePreloadIndices(3, 5, 5)).toEqual([4]);
  });

  it("returns [] for degenerate inputs (empty feed, zero/negative lookahead, negative active index)", () => {
    // WHY: a stray call must never crash the reel with a broken preload target.
    expect(computePreloadIndices(0, 0)).toEqual([]);
    expect(computePreloadIndices(2, 5, 0)).toEqual([]);
    expect(computePreloadIndices(-1, 5)).toEqual([]);
  });

  it("defaults the lookahead to DEFAULT_PRELOAD_LOOKAHEAD (2)", () => {
    // WHY: the documented default IS 2 — pin it so a silent change to the constant
    // is caught (it controls how many audios buffer ahead).
    expect(DEFAULT_PRELOAD_LOOKAHEAD).toBe(2);
    expect(computePreloadIndices(0, 10)).toHaveLength(DEFAULT_PRELOAD_LOOKAHEAD);
  });
});
