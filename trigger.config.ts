/**
 * Trigger.dev v4 project config (Phase 1d SP4).
 *
 * Registers the `./trigger` task dir for the daily personalized-feed pipeline.
 * The heavy work is the Python batch (`agents.pipeline.daily_batch:run_daily_pipeline`);
 * these tasks are the scheduling + per-story fan-out shell.
 *
 * PROVISIONING: `TRIGGER_PROJECT_ID` (proj_…) + `TRIGGER_SECRET_KEY` (tr_dev_…)
 * are set, so this config resolves a real project and the v4 tasks
 * (`schedules.task` / `task` / `batchTrigger`) are deployable. Two things are
 * still deliberately deferred for M1: (1) the TS→Python invocation seam (the
 * `@trigger.dev/python` build extension) is not built, so the manual batch runs
 * directly in Python (see `agents.pipeline.daily_batch`); (2) the scheduled cron
 * is enabled by a deliberate `deploy` AFTER the manual run, per the phase DoD.
 */
import { defineConfig } from "@trigger.dev/sdk";

export default defineConfig({
  // Reason: the project ref is sourced from env (not hardcoded). The placeholder
  // makes the config typecheck while the project is unprovisioned; deploy/dev
  // fail loudly against it until a real proj_… ref is set in .env.
  project: process.env.TRIGGER_PROJECT_ID ?? "proj_PLACEHOLDER_set_TRIGGER_PROJECT_ID",
  dirs: ["./trigger"],
  // Reason: the per-user batch can run minutes at active-interest scale (paid TTS/
  // image per produced story). Generous ceiling; the batch is idempotent.
  maxDuration: 3600,
  retries: {
    // Reason: a paid pipeline must not silently re-run on a transient error and
    // double-spend. Idempotency is the produce-once gate; we still cap attempts.
    enabledInDev: false,
    default: { maxAttempts: 1 },
  },
});
