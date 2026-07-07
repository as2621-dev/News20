import { describe, expect, it, vi } from "vitest";
import { createLiveTurnLatencyTracker, SPEECH_ACTIVITY_AMPLITUDE_FLOOR } from "@/lib/voice/liveLatency";

/**
 * Tests for the live-voice per-turn latency tracker (issue #37).
 *
 * Rule 9 — WHY: PRD story #8 requires every live turn to carry NAMED latency
 * marks so a slow turn is attributable to a specific segment (own-transcript
 * lag vs first-model-audio lag) from numbers, not vibes. These tests pin:
 *   - marks are emitted once per turn with the right name, duration, and index;
 *   - un-attributable events (greeting audio before any user speech) emit
 *     NOTHING rather than a bogus number;
 *   - turn boundaries (turnComplete) reset the once-per-turn gates.
 */

/** Drive the tracker's mic-energy signal above then below the speech floor. */
function speakThenStop(tracker: ReturnType<typeof createLiveTurnLatencyTracker>, speechEndAtMs: number): void {
  tracker.recordAmplitude(SPEECH_ACTIVITY_AMPLITUDE_FLOOR * 2, speechEndAtMs - 500);
  tracker.recordAmplitude(0, speechEndAtMs);
}

describe("createLiveTurnLatencyTracker", () => {
  it("logs speech_end→own_transcript and speech_end→first_model_audio once per turn (happy path)", () => {
    const emitMark = vi.fn();
    const tracker = createLiveTurnLatencyTracker(emitMark);

    speakThenStop(tracker, 1000);
    tracker.recordOwnTranscript(1350);
    tracker.recordOwnTranscript(1500); // streaming delta — must NOT double-log
    tracker.recordModelAudio(2200);
    tracker.recordModelAudio(2300); // second audio chunk — must NOT double-log

    expect(emitMark).toHaveBeenCalledTimes(2);
    expect(emitMark).toHaveBeenNthCalledWith(1, {
      mark_name: "speech_end_to_own_transcript",
      duration_ms: 350,
      turn_index: 0,
    });
    expect(emitMark).toHaveBeenNthCalledWith(2, {
      mark_name: "speech_end_to_first_model_audio",
      duration_ms: 1200,
      turn_index: 0,
    });
  });

  it("emits nothing for model audio with no preceding user speech (the greeting — edge)", () => {
    // WHY: the session opens with a greeting nudge — model audio arrives before
    // the user has spoken at all. A mark there would be a fabricated number.
    const emitMark = vi.fn();
    const tracker = createLiveTurnLatencyTracker(emitMark);

    tracker.recordModelAudio(500);
    tracker.recordOwnTranscript(600);

    expect(emitMark).not.toHaveBeenCalled();
  });

  it("turnComplete advances turn_index and re-arms the once-per-turn marks (boundary)", () => {
    const emitMark = vi.fn();
    const tracker = createLiveTurnLatencyTracker(emitMark);

    speakThenStop(tracker, 1000);
    tracker.recordOwnTranscript(1200);
    tracker.recordTurnComplete();

    speakThenStop(tracker, 5000);
    tracker.recordOwnTranscript(5400);
    tracker.recordModelAudio(6000);

    expect(emitMark).toHaveBeenCalledTimes(3);
    expect(emitMark).toHaveBeenNthCalledWith(2, {
      mark_name: "speech_end_to_own_transcript",
      duration_ms: 400,
      turn_index: 1,
    });
    expect(emitMark).toHaveBeenNthCalledWith(3, {
      mark_name: "speech_end_to_first_model_audio",
      duration_ms: 1000,
      turn_index: 1,
    });
  });

  it("clamps a transcript that lands before speech-end to 0 ms, never negative (edge)", () => {
    // WHY: Gemini streams input transcription WHILE the user is still talking;
    // once speech ends the next delta must read as "no lag", not a negative ms.
    const emitMark = vi.fn();
    const tracker = createLiveTurnLatencyTracker(emitMark);

    speakThenStop(tracker, 2000);
    tracker.recordOwnTranscript(1900); // clock skew / buffered event

    expect(emitMark).toHaveBeenCalledWith({
      mark_name: "speech_end_to_own_transcript",
      duration_ms: 0,
      turn_index: 0,
    });
  });
});
