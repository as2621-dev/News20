/**
 * Finite-briefing constants, extracted here so they stay importable after the
 * legacy `components/reel/ReelChrome` is archived. Both the legacy reel chrome
 * and the Blip Flow Stage-4 reel ({@link ReelStage}) read the SAME two numbers.
 */

// --- Finite-bar provenance (data.js) -----------------------------------------
// Reason: the prototype's `data.js` places the 5 detailed digests at the END of
// a 30-story briefing so the "all caught up" finish line is reachable in-demo —
// the counter reads 26/30 … 30/30. Ported here as named constants where the
// FiniteBar lives (the only consumer). FEED_TOTAL = total stories in the day's
// briefing; FEED_START_INDEX = 0-based feed position of the FIRST fixture story.

/** Total number of stories in the day's finite briefing. */
export const FEED_TOTAL = 30;

/** 0-based feed position of the FIRST fixture story in the briefing. */
export const FEED_START_INDEX = 25;
