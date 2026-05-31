/**
 * Per-story production task (Phase 1d SP4) — the `batchTrigger` fan-out unit.
 *
 * The daily schedule (`dailyPipeline.ts`) fans out one of these per qualifying,
 * not-yet-produced story so production parallelizes (each story = a paid TTS +
 * image + LLM render). The SUBSTANCE is Python
 * (`agents.pipeline.orchestrator:orchestrate_story`): script → verify → TTS →
 * caption → poster → persist, with the verification HALT gate.
 *
 * TS → Python seam (honest, flagged — phase Open Q3/Q5): this task's `run` is the
 * scheduling envelope; the actual cross-runtime call (Trigger Python build
 * extension `python.runScript`, or an HTTP worker) is NOT wired here. The project
 * is now provisioned, but the `@trigger.dev/python` extension is not built yet and
 * the M1 batch is run directly in Python. Do NOT treat this as a working TS→Python
 * producer until the seam is implemented.
 */
import { task } from "@trigger.dev/sdk";

/** Payload for one per-story production run. */
export interface ProduceStoryPayload {
  /** `stories.story_id` of the gated story to produce a digest for. */
  storyId: string;
  /** ISO `YYYY-MM-DD` feed date this production belongs to. */
  targetDateUtc: string;
}

export const produceStoryTask = task({
  id: "produce-story-digest",
  // Reason: one story's full paid render (TTS + image + 2 LLM passes) can take a
  // minute-plus; generous per-attempt ceiling.
  maxDuration: 600,
  retry: { maxAttempts: 1 },
  run: async (payload: ProduceStoryPayload) => {
    // ── TS → Python seam (intentionally unwired) ──────────────────────────────
    // Reason: production is Python. Wiring `python.runScript` →
    // `agents.pipeline.orchestrator:orchestrate_story` is deferred to a
    // provisioned project (phase Open Q3/Q5). Returns the envelope only.
    return {
      storyId: payload.storyId,
      targetDateUtc: payload.targetDateUtc,
      produced: false,
      seam: "agents.pipeline.orchestrator:orchestrate_story (Python — not invoked from TS in M1)",
    };
  },
});
