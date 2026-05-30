/**
 * Audio preload window for gap-free auto-advance (port-map §6).
 *
 * **Why this exists.** iOS WebView needs the next story's `<audio>` already
 * buffered when the current one fires `ended`, or the karaoke advance has an
 * audible gap. The reel therefore eagerly preloads the next 1–2 stories' audio
 * (`<audio preload="auto">`) while the rest stay `"none"`. This module owns the
 * PURE decision of *which* indices to preload so it is unit-testable without a
 * DOM (the `<audio preload>` wiring in `ReelStory` just consumes the result).
 *
 * The finite-briefing invariant (Rule 9): the window only ever looks AHEAD and
 * never wraps past the last story — the reel is finite, so there is nothing to
 * preload beyond the finish line.
 */

/** Default number of upcoming stories to preload past the active one. */
export const DEFAULT_PRELOAD_LOOKAHEAD = 2;

/**
 * Compute which story indices to eagerly preload, given the active index.
 *
 * Returns the next `lookahead` in-range indices AFTER `activeIndex` (default 2),
 * excluding the active index itself and never wrapping past the last story. The
 * caller unions this with `{activeIndex}` to decide which `<audio>` elements get
 * `preload="auto"` vs `"none"`.
 *
 * Guarantees (all asserted in `tests/lib/preload.test.ts`):
 * - never returns a negative index,
 * - never returns an index ≥ `storyCount` (no wrap, no overflow),
 * - never includes `activeIndex`,
 * - returns `[]` on the last story (nothing ahead) and for empty/degenerate feeds.
 *
 * @param activeIndex - 0-based index of the currently-active (snapped) story.
 * @param storyCount - Total number of stories in the feed (≥ 0).
 * @param lookahead - How many upcoming stories to preload (default 2; clamped ≥ 0).
 * @returns Ascending list of in-range indices to preload (length ≤ `lookahead`).
 *
 * @example
 * computePreloadIndices(0, 5);       // [1, 2] — start of a 5-story feed
 * computePreloadIndices(4, 5);       // []     — last story, nothing ahead
 * computePreloadIndices(3, 5);       // [4]    — second-last, only the last left
 * computePreloadIndices(0, 5, 1);    // [1]    — lookahead of 1
 */
export function computePreloadIndices(
  activeIndex: number,
  storyCount: number,
  lookahead: number = DEFAULT_PRELOAD_LOOKAHEAD,
): number[] {
  // Reason: defend against degenerate inputs (empty feed, out-of-range/negative
  // active index, negative lookahead) so a stray call can never produce an index
  // the reel would mount as a broken <audio> source.
  if (storyCount <= 0 || lookahead <= 0 || activeIndex < 0) {
    return [];
  }

  const lastIndex = storyCount - 1;
  const preloadIndices: number[] = [];
  for (let offset = 1; offset <= lookahead; offset += 1) {
    const candidateIndex = activeIndex + offset;
    // Stop at the finish line — the reel is finite, never wrap to the start.
    if (candidateIndex > lastIndex) {
      break;
    }
    preloadIndices.push(candidateIndex);
  }
  return preloadIndices;
}
