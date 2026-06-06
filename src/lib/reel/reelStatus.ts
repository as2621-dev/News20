/**
 * The pure reel-status state machine — extracted here so both the (legacy)
 * `components/reel/Reel` and the Blip Flow Stage-4 `components/blip/reel/BlipReel`
 * share ONE source of truth, and so it stays importable after the legacy reel is
 * archived. No React, no DOM — just the `(status, event) → status` transition,
 * the unit-testable seam (Rule 9). `tests/lib/reel/reelStatus.test.ts` covers it.
 */

/**
 * The reel's high-level status.
 *
 * - `loading`  — initial buffer; a skeleton until the feed resolves.
 * - `tapstart` — feed ready, audio NOT yet unlocked; the TapToStart overlay.
 * - `playing`  — audio unlocked; a story is playing / paused.
 * - `caughtup` — the last story finished; the "all caught up" finish line.
 * - `error`    — the feed load failed; the error screen with retry.
 */
export type ReelStatus = "loading" | "tapstart" | "playing" | "caughtup" | "error";

/**
 * The events that drive {@link nextReelStatus}. Each corresponds to one real
 * thing that happens in the reel.
 *
 * - `feed_loaded`       — `getFeed()` resolved.
 * - `feed_failed`       — `getFeed()` rejected.
 * - `first_tap`         — the user tapped the TapToStart overlay (audio unlock).
 * - `reached_caught_up` — the last story's audio ended.
 * - `replay`            — the user tapped replay on the caught-up screen.
 * - `retry`             — the user tapped retry on the error screen.
 */
export type ReelEvent = "feed_loaded" | "feed_failed" | "first_tap" | "reached_caught_up" | "replay" | "retry";

/**
 * Pure reel-status transition function — the single source of truth for how the
 * machine moves, extracted so it is unit-testable without rendering (Rule 9).
 *
 * Only the legal `(status, event)` pairs transition; every other pair is a
 * guarded no-op that returns the current status unchanged (an event that doesn't
 * apply to the current state must never silently corrupt it — e.g. a stray
 * `reached_caught_up` while still `loading`).
 *
 * Transition table:
 * | from        | event              | to        |
 * |-------------|--------------------|-----------|
 * | `loading`   | `feed_loaded`      | `tapstart`|
 * | `loading`   | `feed_failed`      | `error`   |
 * | `tapstart`  | `first_tap`        | `playing` |
 * | `playing`   | `reached_caught_up`| `caughtup`|
 * | `caughtup`  | `replay`           | `playing` |
 * | `error`     | `retry`            | `loading` |
 * | *any other* | *any other*        | unchanged |
 *
 * @param current - The current reel status.
 * @param event - The event that occurred.
 * @returns The next status (or `current` unchanged if the pair is not legal).
 *
 * @example
 * nextReelStatus("loading", "feed_loaded");       // "tapstart"
 * nextReelStatus("tapstart", "first_tap");        // "playing"
 * nextReelStatus("loading", "reached_caught_up"); // "loading" (guarded no-op)
 */
export function nextReelStatus(current: ReelStatus, event: ReelEvent): ReelStatus {
  switch (current) {
    case "loading":
      if (event === "feed_loaded") {
        return "tapstart";
      }
      if (event === "feed_failed") {
        return "error";
      }
      return current;
    case "tapstart":
      return event === "first_tap" ? "playing" : current;
    case "playing":
      return event === "reached_caught_up" ? "caughtup" : current;
    case "caughtup":
      return event === "replay" ? "playing" : current;
    case "error":
      return event === "retry" ? "loading" : current;
    default:
      return current;
  }
}
