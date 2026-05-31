/**
 * Daily personalized-feed pipeline (Phase 1d SP4) — Trigger.dev schedule.
 *
 * Thin scheduling shell. The SUBSTANCE is the Python batch
 * (`agents/pipeline/orchestrator.py::assemble_daily_feeds`, with
 * `agents/pipeline/feed_assembly.py`): update interest weights → ingest news per
 * active interest → produce digests once → score per user → allocate a ~30-slot
 * per-user `daily_feeds` feed. This task only fires that batch on a fixed daily
 * UTC schedule; it does not re-implement any pipeline logic.
 *
 * SDK VERSION (flagged conflict): the plan/global rules say Trigger.dev v4, but
 * this repo standardizes on the v3 SDK entry point — `@trigger.dev/sdk/v3` with
 * `schedules.task`. We match the repo (no SDK upgrade / scope creep). See the
 * SP4 execution report for the rationale.
 */
import { schedules } from '@trigger.dev/sdk/v3';

/**
 * Cron for the daily run. 06:00 UTC — before the typical commuter morning, so a
 * fresh per-user feed is materialized in `daily_feeds` when the app is opened.
 * Tunable; the heavy paid work (TTS/image/news) runs inside the Python batch.
 */
const DAILY_FEED_CRON_UTC = '0 6 * * *';

export const dailyPipelineTask = schedules.task({
  id: 'daily-personalized-feed',
  cron: DAILY_FEED_CRON_UTC,
  // Reason: the Python batch can take minutes at active-interest scale (paid
  // TTS/image per produced story). Generous ceiling; the batch is idempotent
  // (produce-once per user/day) so a retry cannot duplicate a feed.
  maxDuration: 3600,
  run: async (payload) => {
    const targetDateUtc = (payload.timestamp ?? new Date()).toISOString().slice(0, 10);

    // ── TS → Python seam (honest stub) ────────────────────────────────────
    // Reason: the pipeline substance is Python; this TS task is only the
    // scheduler. Wiring the actual TS→Python invocation (HTTP call to a Python
    // worker endpoint, or a Trigger.dev Python build extension) is the
    // execution-host decision flagged in the phase file (Open Q3 / Q5) and is
    // NOT done here — SP4's Python `assemble_daily_feeds` is the real, tested
    // unit. Do NOT treat this task as a working end-to-end TS→Python call until
    // that seam is implemented. For the M1 manual run the Python batch is
    // invoked directly (see tests/agents/pipeline + the SP3 live e2e pattern).
    return {
      scheduled: true,
      targetDateUtc,
      pythonEntryPoint: 'agents.pipeline.orchestrator:assemble_daily_feeds',
      note: 'Scheduling shell only — TS→Python invocation seam is intentionally not wired (phase Open Q3/Q5).',
    };
  },
});
