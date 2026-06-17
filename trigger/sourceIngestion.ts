/**
 * Followed-source ingestion cron (Phase 5d SP4) — Trigger.dev v4 schedule.
 *
 * Fires every **2 hours** (`0 *​/2 * * *`) and polls each user's *followed sources*
 * (YouTube channels / X accounts) for fresh content, fanning the Python
 * `run_source_ingestion` out **per user**: it lists every user with ≥1 active
 * followed source (a `user_content_sources` row), then for each user makes an
 * authenticated `POST ${WORKER_BASE_URL}/ingestion/sources` to the Railway worker —
 * the SAME bearer-auth seam the daily pipeline uses (`dailyPipeline.ts` →
 * `/pipeline/daily`), never a new transport.
 *
 * Ported from the TL;DW donor's `trigger/ingestion-cron.ts` fan-out shape
 * (`reference/sources-reuse-map.md` §"PORT"): 2h cron → list active users → per-user
 * dispatch. The donor used `triggerAndWait` on an in-process task; News20's worker
 * is a separate Python service, so each user is dispatched as an HTTP POST (matching
 * the existing `dailyPipeline` seam) and the worker runs `run_source_ingestion` for
 * that user (cadence-filter → adapter → dedup → promote into the per-user pool).
 *
 * SDK: Trigger.dev **v4** (`@trigger.dev/sdk`, `schedules.task`) per the project
 * rule — never `client.defineJob`.
 *
 * ⚠ GATED: live runs make outward worker + adapter API calls (YouTube RSS / yt-dlp,
 * xAI/Grok, Playwright). The shared `PIPELINE_CRON_ENABLED` kill-switch (reused from
 * `dailyPipeline.ts`) keeps this frozen until the M5 deploy explicitly enables it.
 *
 * Secrets (`SUPABASE_SERVICE_ROLE_KEY`, `WORKER_BASE_URL`, `PIPELINE_TRIGGER_SECRET`)
 * come from env and are NEVER logged — only per-run counts are emitted (Rule 12 +
 * CLAUDE.md env-safety).
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { schedules } from "@trigger.dev/sdk";
import { cronEnabled } from "./dailyPipeline";

/**
 * Cron for the source-ingestion fan-out: every 2 hours (UTC).
 *
 * Exported so the schedule's firing contract is unit-testable — the Trigger.dev
 * `Task` object does not re-expose its cron config, so the test asserts this exact
 * string (the value handed to the task). A plain UTC cron (not a zoned object) is
 * correct here: ingestion cadence is a fixed interval, not a calendar moment, so it
 * does not need DST handling like the midnight daily run.
 */
export const SOURCE_INGESTION_CRON = "0 */2 * * *" as const;

/** Path on the Railway worker that runs `run_source_ingestion` for one user. */
const INGESTION_SOURCES_PATH = "/ingestion/sources";

/**
 * Structured JSON logger to stdout (CLAUDE.md logging mandate). Counts only — never
 * secrets/keys. Single-line JSON keeps events greppable in the run log.
 *
 * @param event - snake_case event name.
 * @param fields - Contextual fields (no secrets/PII).
 */
function logEvent(event: string, fields: Record<string, unknown>): void {
  console.log(JSON.stringify({ event, ...fields }));
}

/**
 * Read a required env var or throw loudly (Rule 12 — a misconfigured cron must fail,
 * not silently build a client with an empty key or POST nowhere).
 *
 * @param name - The environment variable name.
 * @returns The non-empty value.
 */
function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set — required for the source-ingestion cron.`);
  }
  return value;
}

/**
 * Build a service-role Supabase client from env (values never logged).
 *
 * Mirrors `feedReadinessCheck.ts`: `SUPABASE_URL` with a `NEXT_PUBLIC_SUPABASE_URL`
 * fallback + `SUPABASE_SERVICE_ROLE_KEY`. The service-role key bypasses RLS so the
 * cron can read ALL users' follows (the `user_content_sources` policy is self-only —
 * migration 0009).
 *
 * @returns A service-role Supabase client.
 * @throws If the URL or service-role key env vars are unset.
 */
export function createServiceRoleClient(): SupabaseClient {
  const supabaseUrl = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!supabaseUrl) {
    throw new Error("SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) is not set — required for the source-ingestion cron.");
  }
  const serviceRoleKey = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  // Reason: a cron has no user session; disable session persistence/refresh so the
  // service-role client is a pure server client.
  return createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

/**
 * List the distinct user ids that have ≥1 active followed source.
 *
 * An "active followed source" is simply a `user_content_sources` row (the follow's
 * existence is the active signal — migration 0009 has no separate is_active flag).
 * These are the only users worth fanning out to; a user with no follows has nothing
 * to ingest.
 *
 * @param supabaseClient - Service-role client.
 * @returns The distinct follower user ids.
 * @throws If the Supabase read fails (Rule 12 — surfaced, not swallowed).
 */
export async function listUsersWithFollowedSources(supabaseClient: SupabaseClient): Promise<string[]> {
  const { data, error } = await supabaseClient.from("user_content_sources").select("user_id");
  if (error) {
    throw new Error(
      `Failed to read user_content_sources for source-ingestion fan-out: ${error.message}. ` +
        "fix_suggestion: verify SUPABASE_SERVICE_ROLE_KEY and the user_content_sources table.",
    );
  }
  const distinctUserIds = new Set((data ?? []).map((row) => String(row.user_id)));
  return [...distinctUserIds];
}

/** Per-user ingestion outcome reported by the worker (mirrors `SourceIngestionResult`). */
export interface UserIngestionOutcome {
  /** The user this run was for. */
  userId: string;
  /** The worker's HTTP status (expected `202 Accepted`). */
  workerStatus: number;
  /** Raw items the worker fetched across the user's polled sources (pre-dedup). */
  itemsFetched: number;
  /** Items promoted into the user's story pool. */
  itemsPromoted: number;
  /** Items dropped (dedup + not-substantive) — surfaced so caps are never silent. */
  itemsDropped: number;
}

/**
 * Dispatch `run_source_ingestion` for ONE user via the worker's HTTP seam.
 *
 * Mirrors `dailyPipeline.ts.runDailyPipelineSchedule`: authenticated POST with the
 * bearer pipeline token. The worker runs the per-user ingestion and answers with the
 * run counts (fetched / promoted / dropped) so this cron can log them (no silent
 * caps — plan SP4 DoD). Exported so it is directly unit-testable with `fetch` mocked.
 *
 * @param userId - The user to ingest followed sources for.
 * @returns The per-user ingestion outcome (counts + worker status).
 * @throws If `WORKER_BASE_URL` / `PIPELINE_TRIGGER_SECRET` are unset, or the worker
 *   responds with a non-2xx status (propagated so Trigger.dev marks the run failed).
 */
export async function dispatchUserSourceIngestion(userId: string): Promise<UserIngestionOutcome> {
  const workerBaseUrl = requireEnv("WORKER_BASE_URL");
  const pipelineSecret = requireEnv("PIPELINE_TRIGGER_SECRET");

  const endpoint = `${workerBaseUrl.replace(/\/$/, "")}${INGESTION_SOURCES_PATH}`;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${pipelineSecret}`,
    },
    body: JSON.stringify({ user_id: userId }),
  });

  if (!response.ok) {
    // Reason: surface a failed dispatch so Trigger.dev marks the run failed and
    // retries/alerts apply — never swallow a non-2xx into a fake success.
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Worker rejected source ingestion for user ${userId}: ` +
        `${response.status} ${response.statusText} ${detail}`.trim(),
    );
  }

  // Reason: the worker echoes the SourceIngestionResult counts; default to 0 so a
  // terse worker reply still yields a well-formed (zeroed) outcome rather than NaN.
  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  return {
    userId,
    workerStatus: response.status,
    itemsFetched: Number(body.items_fetched ?? 0),
    itemsPromoted: Number(body.items_promoted ?? 0),
    itemsDropped: Number(body.items_dropped ?? 0),
  };
}

/** Result envelope returned by the scheduled run (for the Trigger.dev run log). */
export interface SourceIngestionRunResult {
  /** `true` when the schedule fired and fanned out; `false` when the kill-switch skipped it. */
  scheduled: boolean;
  /** Users with ≥1 followed source that were fanned out to. */
  userCount: number;
  /** Per-user outcomes (counts logged so no cap is silent). */
  outcomes: UserIngestionOutcome[];
}

/**
 * Fan-out body: list users with followed sources and dispatch per-user ingestion.
 *
 * Exported (not just inlined in the task) so it is directly unit-testable with the
 * Supabase client and `fetch`/`dispatch` mocked. Each user is dispatched in turn; a
 * single user's failure is logged and skipped (the batch continues — one bad source
 * must not abort everyone's ingestion), mirroring the Python pipeline's per-source
 * resilience.
 *
 * @param supabaseClient - Service-role client (injected so tests can mock it).
 * @param dispatchForUser - Per-user dispatch fn (injected; defaults to the HTTP seam
 *   `dispatchUserSourceIngestion`, overridable in tests to avoid network).
 * @returns The run result envelope (user count + per-user outcomes).
 * @throws If the Supabase user-list read fails (a whole-run blocker — surfaced).
 */
export async function runSourceIngestionFanOut(
  supabaseClient: SupabaseClient,
  dispatchForUser: (userId: string) => Promise<UserIngestionOutcome> = dispatchUserSourceIngestion,
): Promise<SourceIngestionRunResult> {
  const userIds = await listUsersWithFollowedSources(supabaseClient);
  logEvent("source_ingestion_fanout_started", { user_count: userIds.length });

  const outcomes: UserIngestionOutcome[] = [];
  for (const userId of userIds) {
    try {
      const outcome = await dispatchForUser(userId);
      outcomes.push(outcome);
      logEvent("source_ingestion_user_completed", {
        user_id: userId,
        worker_status: outcome.workerStatus,
        items_fetched: outcome.itemsFetched,
        items_promoted: outcome.itemsPromoted,
        items_dropped: outcome.itemsDropped,
      });
    } catch (error) {
      // Reason: one user's ingestion failure must not abort the whole fan-out
      // (Rule 12 — surfaced as an error event, not swallowed silently).
      logEvent("source_ingestion_user_failed", {
        user_id: userId,
        error_message: error instanceof Error ? error.message : "unknown",
        fix_suggestion: "This user's source ingestion failed and was skipped; the batch continued.",
      });
    }
  }

  logEvent("source_ingestion_fanout_completed", {
    user_count: userIds.length,
    succeeded: outcomes.length,
    total_items_fetched: outcomes.reduce((sum, o) => sum + o.itemsFetched, 0),
    total_items_promoted: outcomes.reduce((sum, o) => sum + o.itemsPromoted, 0),
    total_items_dropped: outcomes.reduce((sum, o) => sum + o.itemsDropped, 0),
  });

  return { scheduled: true, userCount: userIds.length, outcomes };
}

export const sourceIngestionTask = schedules.task({
  id: "source-ingestion",
  cron: SOURCE_INGESTION_CRON,
  // Reason: the fan-out POSTs are fast (the worker runs each user's ingestion on its
  // own background task); a generous ceiling covers many sequential users.
  maxDuration: 600,
  retry: { maxAttempts: 1 },
  run: async (): Promise<SourceIngestionRunResult> => {
    if (!cronEnabled()) {
      // Reason: kill-switch is OFF by default — early-return BEFORE building the
      // service-role client so a frozen cron never reads SUPABASE_SERVICE_ROLE_KEY
      // or makes any outward call. Re-enable with PIPELINE_CRON_ENABLED=true.
      logEvent("source_ingestion_cron_skipped_disabled", {
        task_id: "source-ingestion",
        fix_suggestion: "PIPELINE_CRON_ENABLED is not 'true'; set it to re-enable the source-ingestion cron.",
      });
      return { scheduled: false, userCount: 0, outcomes: [] };
    }
    return runSourceIngestionFanOut(createServiceRoleClient());
  },
});
