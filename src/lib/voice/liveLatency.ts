/**
 * Live-voice latency instrumentation (issue #37 — PRD story #8).
 *
 * Every live-voice latency number is logged as ONE structured event,
 * `voice_live_latency_mark`, with a `mark_name` naming the segment it measures:
 *
 *   - `token_mint`                       — ephemeral-token mint (or prewarm-cache hit)
 *   - `setup_complete`                   — connect() start → `{setupComplete}` frame
 *   - `speech_end_to_own_transcript`     — user stops talking → their transcript echoes
 *   - `speech_end_to_first_model_audio`  — user stops talking → first agent audio chunk
 *
 * So a slow live turn is attributable to a specific segment from numbers, not
 * vibes. The per-turn tracker is a plain factory (no React) so it is testable
 * in isolation; `useGeminiLive` owns one instance per session and feeds it the
 * mic-amplitude, transcript, model-audio, and turnComplete signals.
 *
 * "Speech end" is derived from mic energy: the RMS amplitude dropping below
 * {@link SPEECH_ACTIVITY_AMPLITUDE_FLOOR} after having been above it (the same
 * floor AskSheetVoice uses for its silent-mic hint). Events that cannot be
 * attributed to a user utterance — e.g. the greeting audio that plays before
 * the user has said anything — emit NOTHING rather than a fabricated number.
 */

import { logger } from "@/lib/logger";

/**
 * Mic RMS amplitude at/above this counts as the user speaking; a drop below it
 * after speech marks "speech end". Real speech RMS sits well above 0.01.
 */
export const SPEECH_ACTIVITY_AMPLITUDE_FLOOR = 0.01;

/** The named segments a `voice_live_latency_mark` event can measure. */
export type LiveLatencyMarkName =
  | "token_mint"
  | "setup_complete"
  | "speech_end_to_own_transcript"
  | "speech_end_to_first_model_audio";

/**
 * Log one named latency mark as a structured client event.
 *
 * Durations only — NEVER token values or transcript text (CSO: the mint mark
 * must not leak the `auth_tokens/...` name).
 *
 * @param fields - `mark_name`, `duration_ms`, plus optional context
 *   (`turn_index`, `used_prewarmed_token`, ...).
 *
 * @example
 * logLiveLatencyMark({ mark_name: "token_mint", duration_ms: 412, used_prewarmed_token: false });
 */
export function logLiveLatencyMark(
  fields: { mark_name: LiveLatencyMarkName; duration_ms: number } & Record<string, unknown>,
): void {
  logger.info("voice_live_latency_mark", fields);
}

/** Per-session tracker for the two per-turn latency marks. */
export interface LiveTurnLatencyTracker {
  /** Feed each mic RMS amplitude sample; derives the speech-end instant. */
  recordAmplitude(amplitude: number, nowMs?: number): void;
  /** Call on every `inputTranscription` frame (the user's own transcript). */
  recordOwnTranscript(nowMs?: number): void;
  /** Call on every model audio chunk (`modelTurn` inlineData). */
  recordModelAudio(nowMs?: number): void;
  /** Call on `serverContent.turnComplete` — advances to the next turn. */
  recordTurnComplete(): void;
}

/**
 * Create a per-session turn-latency tracker.
 *
 * Each turn emits at most ONE `speech_end_to_own_transcript` and ONE
 * `speech_end_to_first_model_audio` mark, measured from the most recent
 * speech-end instant. Marks that would predate speech end are clamped to 0
 * (transcripts stream during speech). Events with no recorded speech end
 * (e.g. the greeting) are skipped.
 *
 * @param emitMark - Mark sink; defaults to {@link logLiveLatencyMark}.
 * @returns A {@link LiveTurnLatencyTracker}.
 */
export function createLiveTurnLatencyTracker(
  emitMark: (fields: { mark_name: LiveLatencyMarkName; duration_ms: number; turn_index: number }) => void = (fields) =>
    logLiveLatencyMark(fields),
): LiveTurnLatencyTracker {
  let turnIndex = 0;
  let isSpeaking = false;
  let speechEndAtMs: number | null = null;
  let hasLoggedOwnTranscriptThisTurn = false;
  let hasLoggedModelAudioThisTurn = false;

  function emitOnce(markName: LiveLatencyMarkName, nowMs: number): void {
    if (speechEndAtMs === null) {
      // Reason: no user utterance to attribute this to (e.g. the greeting
      // audio) — a mark here would be a fabricated number.
      return;
    }
    emitMark({
      mark_name: markName,
      // Reason: transcripts stream WHILE the user talks; a delta landing at or
      // before speech end means "no lag" — clamp to 0, never negative.
      duration_ms: Math.max(0, nowMs - speechEndAtMs),
      turn_index: turnIndex,
    });
  }

  return {
    recordAmplitude(amplitude: number, nowMs: number = Date.now()): void {
      if (amplitude >= SPEECH_ACTIVITY_AMPLITUDE_FLOOR) {
        isSpeaking = true;
        return;
      }
      if (isSpeaking) {
        isSpeaking = false;
        speechEndAtMs = nowMs;
      }
    },
    recordOwnTranscript(nowMs: number = Date.now()): void {
      if (hasLoggedOwnTranscriptThisTurn) {
        return;
      }
      if (speechEndAtMs !== null) {
        hasLoggedOwnTranscriptThisTurn = true;
        emitOnce("speech_end_to_own_transcript", nowMs);
      }
    },
    recordModelAudio(nowMs: number = Date.now()): void {
      if (hasLoggedModelAudioThisTurn) {
        return;
      }
      if (speechEndAtMs !== null) {
        hasLoggedModelAudioThisTurn = true;
        emitOnce("speech_end_to_first_model_audio", nowMs);
      }
    },
    recordTurnComplete(): void {
      turnIndex += 1;
      speechEndAtMs = null;
      hasLoggedOwnTranscriptThisTurn = false;
      hasLoggedModelAudioThisTurn = false;
    },
  };
}
