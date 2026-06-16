/**
 * Daily personalized-feed pipeline (Phase 7c SP2) — Trigger.dev v4 schedule.
 *
 * Fires at **00:00 America/New_York** and drives the Python daily pipeline over
 * HTTP: an authenticated `POST ${WORKER_BASE_URL}/pipeline/daily` to the Railway
 * worker (the seam built in Phase 7), carrying the ET calendar `target_date` so
 * the run produces for the day the cron just entered (never the worker's clock).
 *
 * SDK: Trigger.dev **v4** (`@trigger.dev/sdk`, `schedules.task`) per the project
 * rule — never `client.defineJob`. "Midnight" is expressed as
 * `{ pattern, timezone: "America/New_York" }` so DST is handled by zone name.
 *
 * The worker answers `202` (the run is enqueued as a background task there); any
 * non-2xx makes this task throw so Trigger.dev surfaces the failure (Rule 12).
 */
import { schedules } from "@trigger.dev/sdk";

/**
 * Cron for the daily run: midnight US Eastern, DST handled by zone name.
 *
 * Exported so the schedule's firing contract (pattern + timezone) is unit-testable
 * — the Trigger.dev `Task` object does not re-expose its cron config, so the test
 * asserts this declaration directly (it is the exact value handed to the task).
 */
export const DAILY_FEED_CRON = { pattern: "0 0 * * *", timezone: "America/New_York" } as const;

/** Path on the Railway worker that kicks off a full daily pipeline run (Phase 7). */
const PIPELINE_DAILY_PATH = "/pipeline/daily";

/**
 * Compute the US-Eastern calendar date (`YYYY-MM-DD`) for an instant.
 *
 * Pure + deterministic: it derives the date solely from the passed `instant`
 * (the schedule payload's `timestamp`), never `Date.now()`, so it is unit-testable
 * with a pinned timestamp. `America/New_York` is resolved by name via
 * `Intl.DateTimeFormat`, so the UTC→ET offset (and DST) is applied correctly — a
 * late-evening ET instant that is already the next day in UTC still yields the ET
 * date.
 *
 * @param instant - The instant to convert (the trigger run's timestamp).
 * @returns The Eastern-Time calendar date as `YYYY-MM-DD`.
 *
 * @example
 * // 2026-03-09T03:30:00Z is 2026-03-08 22:30 in New York (UTC-5).
 * easternCalendarDate(new Date("2026-03-09T03:30:00Z")); // "2026-03-08"
 */
export function easternCalendarDate(instant: Date): string {
  // Reason: en-CA renders ISO-ordered YYYY-MM-DD parts, so the formatted value
  // is the ET calendar date directly — no manual offset math (DST-safe by zone).
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return formatter.format(instant);
}

/** Result envelope returned by the scheduled run (for the Trigger.dev run log). */
export interface DailyPipelineRunResult {
  /** Always `true` — the schedule fired and the worker accepted the run. */
  scheduled: true;
  /** The ET calendar date posted to the worker as `target_date`. */
  targetDate: string;
  /** The worker's HTTP status (expected `202 Accepted`). */
  workerStatus: number;
}

/**
 * Read a required env var or throw loudly (Rule 12 — a misconfigured trigger must
 * fail, not silently POST to `undefined` or with an empty bearer token).
 *
 * @param name - The environment variable name.
 * @returns The non-empty value.
 */
function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set — required for the daily pipeline trigger to call the worker.`);
  }
  return value;
}

/**
 * Run body for the daily schedule: compute the ET `target_date` and POST it to the
 * worker with the bearer pipeline token. Exported (not just inlined in the task) so
 * it is directly unit-testable with `fetch` mocked.
 *
 * @param instant - The trigger run's timestamp (drives the ET date deterministically).
 * @returns The run result envelope (target date + worker status).
 * @throws If `WORKER_BASE_URL` / `PIPELINE_TRIGGER_SECRET` are unset, or the worker
 *   responds with a non-2xx status.
 */
export async function runDailyPipelineSchedule(instant: Date): Promise<DailyPipelineRunResult> {
  const workerBaseUrl = requireEnv("WORKER_BASE_URL");
  const pipelineSecret = requireEnv("PIPELINE_TRIGGER_SECRET");
  const targetDate = easternCalendarDate(instant);

  const endpoint = `${workerBaseUrl.replace(/\/$/, "")}${PIPELINE_DAILY_PATH}`;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${pipelineSecret}`,
    },
    body: JSON.stringify({ target_date: targetDate }),
  });

  if (!response.ok) {
    // Reason: surface a failed dispatch so Trigger.dev marks the run failed and
    // retries/alerts apply — never swallow a non-2xx into a "scheduled: true".
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Worker rejected daily pipeline run for ${targetDate}: ` +
        `${response.status} ${response.statusText} ${detail}`.trim(),
    );
  }

  return { scheduled: true, targetDate, workerStatus: response.status };
}

export const dailyPersonalizedFeedTask = schedules.task({
  id: "daily-personalized-feed",
  cron: DAILY_FEED_CRON,
  // Reason: the worker returns 202 immediately (it runs the pipeline on its own
  // background task), so this POST is fast; keep a modest ceiling well above one
  // HTTP round-trip.
  maxDuration: 120,
  retry: { maxAttempts: 1 },
  run: async (payload) => runDailyPipelineSchedule(payload.timestamp),
});
