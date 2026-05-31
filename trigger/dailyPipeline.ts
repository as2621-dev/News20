/**
 * Daily personalized-feed pipeline (Phase 1d SP4) — Trigger.dev v4 schedule.
 *
 * Thin scheduling shell over the Python batch
 * (`agents.pipeline.daily_batch:run_daily_pipeline`, which chains:
 * update interest weights (§4) → ingest news per active interest → produce
 * digests once → score per user → allocate a ~30-slot per-user `daily_feeds`).
 * This task fires that batch daily and expresses the per-story production fan-out
 * via `batchTrigger` over `produceStoryTask`. It re-implements no pipeline logic.
 *
 * SDK: Trigger.dev **v4** (`@trigger.dev/sdk`, `schedules.task` + `batchTrigger`)
 * per the project rule — never `client.defineJob`.
 *
 * SEAM FLAG: the Trigger project is now provisioned (`TRIGGER_*` set), but the
 * TS→Python invocation remains the deferred seam (Open Q3/Q5). For M1 the Python
 * `run_daily_pipeline` is invoked directly (see the SP4 live e2e). The
 * `loadGatedStoryIds` loader below is the seam stub: it returns `[]` until the
 * Supabase read (or Python hand-off) is wired, so `batchTrigger` is real, typed
 * v4 code that no-ops rather than fabricates work.
 */
import { schedules } from "@trigger.dev/sdk";
import { type ProduceStoryPayload, produceStoryTask } from "./produceStory";

/**
 * Cron for the daily run. 06:00 UTC — before the typical commuter morning, so a
 * fresh per-user feed is materialized in `daily_feeds` when the app is opened.
 */
const DAILY_FEED_CRON_UTC = "0 6 * * *";

/**
 * Seam stub: the gated, not-yet-produced `stories.story_id`s for `targetDateUtc`.
 * Wired later to the Supabase produce-gate read (or returned by the Python batch).
 * Returns `[]` for now so the fan-out below is exercised without fabricating work.
 */
async function loadGatedStoryIds(_targetDateUtc: string): Promise<string[]> {
  // Reason: SP1 ingest + produce-gate select run in Python; this loader is the
  // documented hand-off point, intentionally empty until the seam is wired.
  return [];
}

export const dailyPersonalizedFeedTask = schedules.task({
  id: "daily-personalized-feed",
  cron: DAILY_FEED_CRON_UTC,
  // Reason: the Python batch can take minutes at active-interest scale (paid
  // TTS/image per produced story). Generous ceiling; the batch is idempotent
  // (produce-once per user/day) so a retry cannot duplicate a feed.
  maxDuration: 3600,
  retry: { maxAttempts: 1 },
  run: async (payload) => {
    const targetDateUtc = (payload.timestamp ?? new Date()).toISOString().slice(0, 10);

    // ── Stage C fan-out: one paid production per gated story, in parallel ──────
    // Stages A/B (update weights, ingest+tag) and D/E (score, allocate) run in
    // the Python batch; stage C — per-story production — is the parallelizable
    // paid step, expressed here as a v4 batchTrigger over produceStoryTask.
    const gatedStoryIds = await loadGatedStoryIds(targetDateUtc);
    const produceItems = gatedStoryIds.map((storyId): { payload: ProduceStoryPayload } => ({
      payload: { storyId, targetDateUtc },
    }));
    const fanOut = produceItems.length > 0 ? await produceStoryTask.batchTrigger(produceItems) : undefined;

    return {
      scheduled: true,
      targetDateUtc,
      pythonEntryPoint: "agents.pipeline.daily_batch:run_daily_pipeline",
      fannedOutBatchId: fanOut?.batchId,
      producedStoryCount: gatedStoryIds.length,
      note: "Scheduling shell — TS→Python invocation seam is intentionally not wired for M1 (phase Open Q3/Q5); the Python batch is run directly.",
    };
  },
});
