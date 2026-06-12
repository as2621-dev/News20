/**
 * Finite-briefing constants, extracted here so they stay importable after the
 * legacy `components/reel/ReelChrome` is archived. Both the legacy reel chrome
 * and the Blip Flow Stage-4 reel ({@link ReelStage}) read the SAME two numbers.
 */

// --- Finite-briefing cap ------------------------------------------------------
// Reason: the briefing is FINITE by design — at most 30 stories per day. The
// live reel counts REAL feed positions (storyIndex / storyCount); this cap is
// what the loading skeleton renders (feed length unknown while buffering) and
// what the feed loader defensively slices to. The prototype's demo-only
// FEED_START_INDEX=25 offset (which made a 7-story live feed read "32 / 30")
// was removed with it.

/** Maximum number of stories in the day's finite briefing. */
export const FEED_TOTAL = 30;
