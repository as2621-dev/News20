/**
 * Feed-readiness safety-net cron (Phase 7c SP3) — Trigger.dev v4 schedule.
 *
 * Fires at **05:00 America/New_York**, five hours after the midnight daily run
 * (`dailyPipeline.ts`). It reads Supabase directly with the service-role key and
 * answers one question: did today's daily run actually produce feeds?
 *
 *   - If there ARE active users but ZERO `daily_feeds` rows for today's ET date,
 *     the midnight run failed or never landed — so it re-invokes the SAME
 *     authenticated `POST ${WORKER_BASE_URL}/pipeline/daily` path as the midnight
 *     schedule (reusing `runDailyPipelineSchedule` from `dailyPipeline.ts`, so the
 *     bearer-auth + ET-`target_date` contract is shared, never duplicated).
 *   - In BOTH branches it logs how many CURRENT stories are missing an ambient
 *     poster (`stories.story_ambient_poster_url IS NULL`) as a readiness signal.
 *     This phase only DETECTS + LOGS that gap; the instant-regeneration gate is
 *     Phase 7d (out of scope here).
 *
 * SDK: Trigger.dev **v4** (`@trigger.dev/sdk`, `schedules.task`) per the project
 * rule — never `client.defineJob`. "05:00 ET" is expressed as
 * `{ pattern, timezone: "America/New_York" }` so DST is handled by zone name.
 *
 * Secrets (`SUPABASE_SERVICE_ROLE_KEY`, `WORKER_BASE_URL`, `PIPELINE_TRIGGER_SECRET`)
 * come from env and are NEVER logged — only counts/flags are emitted (Rule 12 +
 * CLAUDE.md env-safety).
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { schedules } from "@trigger.dev/sdk";
import { cronEnabled, easternCalendarDate, runDailyPipelineSchedule } from "./dailyPipeline";

/**
 * Cron for the readiness safety net: 05:00 US Eastern, DST handled by zone name.
 *
 * Exported so the schedule's firing contract (pattern + timezone) is unit-testable
 * — the Trigger.dev `Task` object does not re-expose its cron config, so the test
 * asserts this declaration directly (it is the exact value handed to the task).
 */
export const FEED_READINESS_CRON = { pattern: "0 5 * * *", timezone: "America/New_York" } as const;

/**
 * Structured JSON logger to stdout (CLAUDE.md logging mandate). Trigger.dev's own
 * logger is run-scoped; a plain structured line keeps these readiness events
 * greppable in the run log AND in any aggregated stdout. Counts only — never keys.
 *
 * @param event - snake_case event name.
 * @param fields - Contextual fields (no secrets/PII).
 */
function logEvent(event: string, fields: Record<string, unknown>): void {
  // Reason: single-line JSON so the event is machine-parseable in the run log.
  console.log(JSON.stringify({ event, ...fields }));
}

/**
 * Read a required env var or throw loudly (Rule 12 — a misconfigured readiness
 * cron must fail, not silently build a client with an empty key or POST nowhere).
 *
 * @param name - The environment variable name.
 * @returns The non-empty value.
 */
function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set — required for the feed-readiness safety-net cron.`);
  }
  return value;
}

/**
 * Build a service-role Supabase client from env (values never logged).
 *
 * Mirrors the repo's server-side pattern: `SUPABASE_URL` with a
 * `NEXT_PUBLIC_SUPABASE_URL` fallback + `SUPABASE_SERVICE_ROLE_KEY`. The
 * service-role key bypasses RLS so the cron can count ALL users' feeds (the
 * `daily_feeds` SELECT policy is self-only — see migration 0003).
 *
 * @returns A service-role Supabase client.
 * @throws If the URL or service-role key env vars are unset.
 */
export function createServiceRoleClient(): SupabaseClient {
  const supabaseUrl = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!supabaseUrl) {
    throw new Error("SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) is not set — required for the feed-readiness cron.");
  }
  const serviceRoleKey = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  // Reason: a cron has no user session; disable session persistence/refresh so the
  // service-role client is a pure server client.
  return createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

/**
 * Count the distinct active users — those with ≥1 `user_interest_profile` row.
 *
 * Same "active user" definition the Python pipeline uses (`_load_active_user_ids`
 * in `agents/pipeline/daily_batch.py`). If this is zero there is nothing to
 * produce, so the readiness cron must NOT re-run (no users → no feeds expected).
 *
 * @param supabaseClient - Service-role client.
 * @returns The count of distinct active user ids.
 */
async function countActiveUsers(supabaseClient: SupabaseClient): Promise<number> {
  const { data, error } = await supabaseClient.from("user_interest_profile").select("profile_user_id");
  if (error) {
    throw new Error(
      `Failed to read user_interest_profile for readiness check: ${error.message}. ` +
        "fix_suggestion: verify SUPABASE_SERVICE_ROLE_KEY and the table name.",
    );
  }
  const distinctUserIds = new Set((data ?? []).map((row) => String(row.profile_user_id)));
  return distinctUserIds.size;
}

/**
 * Count today's `daily_feeds` rows for the given ET `feed_date`.
 *
 * Uses a head-only exact count so no row payload is transferred. Any non-zero
 * count means the midnight run landed feeds for today (the per-user uniqueness is
 * enforced by the table's constraints; we only need presence here).
 *
 * @param supabaseClient - Service-role client.
 * @param feedDate - The ET calendar date (`YYYY-MM-DD`) to check.
 * @returns The number of `daily_feeds` rows dated `feedDate`.
 */
async function countTodaysFeeds(supabaseClient: SupabaseClient, feedDate: string): Promise<number> {
  const { count, error } = await supabaseClient
    .from("daily_feeds")
    .select("daily_feed_id", { count: "exact", head: true })
    .eq("feed_date", feedDate);
  if (error) {
    throw new Error(
      `Failed to count daily_feeds for ${feedDate}: ${error.message}. ` +
        "fix_suggestion: verify the daily_feeds table and feed_date column.",
    );
  }
  return count ?? 0;
}

/**
 * Count CURRENT stories that are missing an ambient poster.
 *
 * "Current" = the story has a current digest (`digests.digest_is_current = true`),
 * which is the same set the reel feed renders. "Missing poster" =
 * `stories.story_ambient_poster_url IS NULL`. This is a READINESS SIGNAL only —
 * Phase 7d owns acting on it (instant regeneration); here we just log the gap.
 *
 * @param supabaseClient - Service-role client.
 * @returns The number of current stories with a null `story_ambient_poster_url`.
 */
async function countCurrentStoriesMissingPoster(supabaseClient: SupabaseClient): Promise<number> {
  const { count, error } = await supabaseClient
    .from("stories")
    // Reason: digests!inner restricts to stories that HAVE a current digest, so the
    // count reflects only stories actually in rotation (not stale/never-produced).
    .select("story_id, digests!inner(digest_is_current)", { count: "exact", head: true })
    .eq("digests.digest_is_current", true)
    .is("story_ambient_poster_url", null);
  if (error) {
    throw new Error(
      `Failed to count current stories missing a poster: ${error.message}. ` +
        "fix_suggestion: verify the stories.story_ambient_poster_url column and digests embed.",
    );
  }
  return count ?? 0;
}

/** Result envelope returned by the readiness run (for the Trigger.dev run log). */
export interface FeedReadinessRunResult {
  /** The ET calendar date the readiness check evaluated. */
  targetDate: string;
  /** Distinct active users (those with ≥1 interest profile row). */
  activeUserCount: number;
  /** `daily_feeds` rows present for `targetDate`. */
  todaysFeedCount: number;
  /** Current stories missing `story_ambient_poster_url` (logged, not acted on). */
  missingPosterCount: number;
  /** Whether this run re-invoked the daily pipeline POST. */
  reranPipeline: boolean;
}

/**
 * Readiness check body: did today's feeds land? If active users exist but no
 * feeds do, re-invoke the daily pipeline POST (reusing the midnight schedule's
 * authenticated helper). The missing-poster count is logged in BOTH branches.
 *
 * Exported (not just inlined in the task) so it is directly unit-testable with the
 * Supabase client and `fetch` mocked.
 *
 * @param instant - The trigger run's timestamp (drives the ET date deterministically).
 * @param supabaseClient - Service-role client (injected so tests can mock it).
 * @returns The readiness run result envelope.
 * @throws If a Supabase read fails, or the re-run POST is attempted and the worker
 *   responds non-2xx (propagated from `runDailyPipelineSchedule`).
 */
export async function runFeedReadinessCheck(
  instant: Date,
  supabaseClient: SupabaseClient,
): Promise<FeedReadinessRunResult> {
  const targetDate = easternCalendarDate(instant);
  const [activeUserCount, todaysFeedCount, missingPosterCount] = await Promise.all([
    countActiveUsers(supabaseClient),
    countTodaysFeeds(supabaseClient, targetDate),
    countCurrentStoriesMissingPoster(supabaseClient),
  ]);

  // Reason: re-run only when there ARE users to serve but the day produced no
  // feeds. Zero rows for today (with active users) is the trigger condition.
  const feedsMissing = activeUserCount > 0 && todaysFeedCount === 0;

  // Logged in BOTH branches (DoD): the missing-poster readiness signal is always
  // surfaced, independent of the feed-presence decision.
  logEvent("feed_readiness_evaluated", {
    target_date: targetDate,
    active_user_count: activeUserCount,
    todays_feed_count: todaysFeedCount,
    missing_poster_count: missingPosterCount,
    feeds_missing: feedsMissing,
  });

  if (!feedsMissing) {
    return {
      targetDate,
      activeUserCount,
      todaysFeedCount,
      missingPosterCount,
      reranPipeline: false,
    };
  }

  // Reason: a still-empty feed at 05:00 ET is a production-impacting gap — emit a
  // structured `error` event (per the plan's Open question: log, don't page; 7d
  // wires alerting) before kicking the re-run.
  logEvent("feed_readiness_rerun_triggered", {
    target_date: targetDate,
    active_user_count: activeUserCount,
    fix_suggestion: "Midnight daily run produced no feeds for today; re-invoking POST /pipeline/daily.",
  });
  const rerun = await runDailyPipelineSchedule(instant);
  logEvent("feed_readiness_rerun_accepted", {
    target_date: rerun.targetDate,
    worker_status: rerun.workerStatus,
  });

  return {
    targetDate,
    activeUserCount,
    todaysFeedCount,
    missingPosterCount,
    reranPipeline: true,
  };
}

export const feedReadinessCheckTask = schedules.task({
  id: "feed-readiness-check",
  cron: FEED_READINESS_CRON,
  // Reason: the worker re-run answers 202 immediately and the Supabase counts are
  // head-only; a 120s ceiling is well above the few round-trips this makes.
  maxDuration: 120,
  retry: { maxAttempts: 1 },
  run: async (payload): Promise<FeedReadinessRunResult> => {
    const targetDate = easternCalendarDate(payload.timestamp);
    if (!cronEnabled()) {
      // Reason: kill-switch is OFF by default — early-return BEFORE building the
      // service-role client so a frozen cron never reads SUPABASE_SERVICE_ROLE_KEY.
      logEvent("feed_readiness_cron_skipped_disabled", {
        task_id: "feed-readiness-check",
        target_date: targetDate,
        fix_suggestion: "PIPELINE_CRON_ENABLED is not 'true'; set it to re-enable the readiness cron.",
      });
      return {
        targetDate,
        activeUserCount: 0,
        todaysFeedCount: 0,
        missingPosterCount: 0,
        reranPipeline: false,
      };
    }
    return runFeedReadinessCheck(payload.timestamp, createServiceRoleClient());
  },
});
