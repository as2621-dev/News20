import { describe, expect, it } from "vitest";
import { nextReelStatus, type ReelEvent, type ReelStatus } from "@/lib/reel/reelStatus";

/**
 * Unit tests for the reel's status state machine.
 *
 * Rule 9 — these encode WHY each transition matters, not just that the function
 * returns a string:
 *   - the audio-unlock gate (`tapstart → first_tap → playing`) is the iOS
 *     muted-autoplay contract: the machine must NOT be `playing` (and so must not
 *     auto-play audio) until a real first tap;
 *   - the finite-briefing finish line (`playing → reached_caught_up → caughtup`)
 *     is the whole product thesis — reaching the end is a state, not an empty feed;
 *   - replay/retry are the only ways back out of the terminal screens;
 *   - every OTHER (status, event) pair must be a guarded no-op, so a mis-fired or
 *     out-of-order event can never silently corrupt the machine (e.g. a stray
 *     `reached_caught_up` arriving while still `loading`).
 * Each assertion pins an exact target status, so a wrong transition FAILS the
 * test rather than merely compiling.
 */
describe("nextReelStatus — the reel status state machine", () => {
  describe("legal transitions", () => {
    it("loading --feed_loaded--> tapstart (feed ready, show the unlock gate)", () => {
      expect(nextReelStatus("loading", "feed_loaded")).toBe("tapstart");
    });

    it("loading --feed_failed--> error (offline / failed load)", () => {
      expect(nextReelStatus("loading", "feed_failed")).toBe("error");
    });

    it("tapstart --first_tap--> playing (the iOS audio-unlock gesture)", () => {
      // WHY: only this event may start playback. Before it, the machine is NOT
      // `playing`, so no story auto-plays — the muted-autoplay gate holds.
      expect(nextReelStatus("tapstart", "first_tap")).toBe("playing");
    });

    it("playing --reached_caught_up--> caughtup (the finite finish line)", () => {
      expect(nextReelStatus("playing", "reached_caught_up")).toBe("caughtup");
    });

    it("caughtup --replay--> playing (restart the briefing from story 1)", () => {
      expect(nextReelStatus("caughtup", "replay")).toBe("playing");
    });

    it("error --retry--> loading (re-attempt the feed load)", () => {
      expect(nextReelStatus("error", "retry")).toBe("loading");
    });
  });

  describe("guarded no-ops — irrelevant or out-of-order events never corrupt state", () => {
    it("ignores reached_caught_up while still loading (cannot be caught up before play)", () => {
      // WHY: a stray end-of-audio signal before the feed is even ready must not
      // jump the machine to the finish line.
      expect(nextReelStatus("loading", "reached_caught_up")).toBe("loading");
    });

    it("ignores first_tap while loading (no audio to unlock yet)", () => {
      expect(nextReelStatus("loading", "first_tap")).toBe("loading");
    });

    it("ignores a second first_tap once already playing (idempotent unlock)", () => {
      // WHY: taps keep coming (pause/play) once playing; they must not reset the
      // machine — only `reached_caught_up` leaves `playing`.
      expect(nextReelStatus("playing", "first_tap")).toBe("playing");
    });

    it("ignores feed_loaded once already playing (a late resolve can't reset playback)", () => {
      expect(nextReelStatus("playing", "feed_loaded")).toBe("playing");
    });

    it("ignores replay unless on the caught-up screen", () => {
      expect(nextReelStatus("playing", "replay")).toBe("playing");
      expect(nextReelStatus("tapstart", "replay")).toBe("tapstart");
    });

    it("ignores retry unless on the error screen", () => {
      expect(nextReelStatus("playing", "retry")).toBe("playing");
      expect(nextReelStatus("caughtup", "retry")).toBe("caughtup");
    });

    it("ignores feed_failed once past loading (a stale failure can't kill a live reel)", () => {
      expect(nextReelStatus("playing", "feed_failed")).toBe("playing");
      expect(nextReelStatus("caughtup", "feed_failed")).toBe("caughtup");
    });
  });

  it("is exhaustive: every (status, event) pair returns a valid status and no pair throws", () => {
    // WHY: defensive total-function check — the machine must be defined for ALL
    // inputs (so a future new event/status can't silently fall through to an
    // invalid value), and every result must be one of the known states.
    const allStatuses: ReelStatus[] = ["loading", "tapstart", "playing", "caughtup", "error"];
    const allEvents: ReelEvent[] = ["feed_loaded", "feed_failed", "first_tap", "reached_caught_up", "replay", "retry"];
    for (const status of allStatuses) {
      for (const event of allEvents) {
        const result = nextReelStatus(status, event);
        expect(allStatuses).toContain(result);
      }
    }
  });

  it("drives the full happy path mount → tapstart → playing → caughtup → replay → playing", () => {
    // WHY: ties the individual transitions into the real lifecycle the reel runs,
    // proving they compose into the finite loop the phase exists to ship.
    let status: ReelStatus = "loading";
    status = nextReelStatus(status, "feed_loaded");
    expect(status).toBe("tapstart");
    status = nextReelStatus(status, "first_tap");
    expect(status).toBe("playing");
    status = nextReelStatus(status, "reached_caught_up");
    expect(status).toBe("caughtup");
    status = nextReelStatus(status, "replay");
    expect(status).toBe("playing");
  });
});
