/**
 * First-run feed assembly client (Phase 7b SP2).
 *
 * Called once, right after onboarding's "Build your 30" allocation persists, to
 * synchronously build the just-onboarded user's feed from the EXISTING catalog so
 * they land on a populated reel instead of waiting for the next daily batch.
 *
 * It POSTs to the worker's JWT-scoped `POST /feed/assemble-mine` (Phase 7b SP1)
 * carrying the user's OWN Supabase session access token as `Authorization: Bearer
 * <jwt>` — the worker derives `user_id` from that verified token, never from the
 * body. The shared `PIPELINE_TRIGGER_SECRET` is NEVER in client code: this seam
 * uses the session token only.
 *
 * The body carries only an optional `feed_date` (`YYYY-MM-DD`, default today UTC).
 * Any failure (no session, non-200, network/parse error) is surfaced as a thrown
 * error so the caller's try/catch can treat it as NON-FATAL and fall back to the
 * global feed — this module never decides UI flow.
 */

import { logger } from "@/lib/logger";
import { getCurrentSession } from "@/lib/supabase/auth";

/** What `assembleFirstRunFeed` resolves to — the allocated story count for the reel. */
export interface AssembleFirstRunFeedResult {
  /** Stories written/already-present in the user's feed (worker `allocated_count`). */
  allocated_count: number;
}

/**
 * Resolve today's feed date as a `YYYY-MM-DD` string in UTC.
 *
 * Matches the worker's `feed_date` default (today UTC); using UTC keeps the
 * client and worker on the same calendar day regardless of device timezone.
 *
 * @returns The current UTC date formatted `YYYY-MM-DD`.
 *
 * @example
 * todayUtcFeedDate(); // → "2026-06-16"
 */
export function todayUtcFeedDate(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Resolve the worker base URL. Empty string (the default) makes the request a
 * same-origin relative path (`/feed/assemble-mine`), correct when a reverse-proxy
 * fronts the worker; set `NEXT_PUBLIC_QA_API_BASE_URL` to the deployed worker
 * origin for the Capacitor static build (no same-origin server). Reuses the SAME
 * env var every other client→worker call reads (askQuestion, fetchStoryCorpus).
 *
 * @returns The base URL with any trailing slash stripped, or `""` for same-origin.
 */
function getWorkerBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_QA_API_BASE_URL ?? "";
  return base.replace(/\/+$/, "");
}

/**
 * Narrow an unknown JSON body to a valid {@link AssembleFirstRunFeedResult}.
 *
 * @param body - The parsed JSON response body (unknown shape).
 * @returns A validated result, or `null` when `allocated_count` is missing/non-numeric.
 */
function parseAssembleResponse(body: unknown): AssembleFirstRunFeedResult | null {
  if (typeof body !== "object" || body === null) {
    return null;
  }
  const candidate = body as Record<string, unknown>;
  if (typeof candidate.allocated_count !== "number" || !Number.isFinite(candidate.allocated_count)) {
    return null;
  }
  return { allocated_count: candidate.allocated_count };
}

/**
 * Assemble the current user's first-run feed via the JWT-scoped worker endpoint.
 *
 * Reads the live Supabase session and ships its access token as a bearer header;
 * the worker identifies the user from that token alone. Throws on ANY failure
 * (no session, non-200, network/parse error) — the onboarding caller swallows the
 * throw and falls back to the global feed, so a worker outage never blocks
 * finishing onboarding (Phase 7b non-fatal contract).
 *
 * @param feedDate - The feed date to assemble (`YYYY-MM-DD`). Defaults to today UTC.
 * @param fetchImpl - Injectable fetch (defaults to the global `fetch`; tests pass a mock).
 * @returns The allocated story count from the worker.
 * @throws If there is no session, the response is non-200, or the body is malformed.
 *
 * @example
 * try {
 *   const { allocated_count } = await assembleFirstRunFeed();
 *   // persist the first-run flag, then route to the reel
 * } catch {
 *   // non-fatal — route to the reel anyway (global-feed fallback)
 * }
 */
export async function assembleFirstRunFeed(
  feedDate: string = todayUtcFeedDate(),
  fetchImpl: typeof fetch = fetch,
): Promise<AssembleFirstRunFeedResult> {
  const session = await getCurrentSession();
  const accessToken = session?.access_token;
  if (!accessToken) {
    // Reason: Rule 12 — fail loud (no silent skip). The worker requires the user's
    // own JWT; without a session we cannot scope the assembly, so the caller must
    // fall back to the global feed.
    logger.warn("assemble_first_run_feed_no_session", {
      fix_suggestion:
        "A Supabase session must exist before assembling the first-run feed; falling back to global feed.",
    });
    throw new Error("No Supabase session — cannot assemble first-run feed.");
  }

  const endpoint = `${getWorkerBaseUrl()}/feed/assemble-mine`;
  logger.info("assemble_first_run_feed_started", { feed_date: feedDate });

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Reason: the worker derives user_id from THIS verified token (SP1). No
        // shared secret — only the caller's own session access token.
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ feed_date: feedDate }),
    });
  } catch (error: unknown) {
    logger.error("assemble_first_run_feed_failed", {
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion: "Check network connectivity and that NEXT_PUBLIC_QA_API_BASE_URL points at the reachable worker.",
    });
    throw error instanceof Error ? error : new Error("Unknown error assembling first-run feed.");
  }

  if (!response.ok) {
    logger.error("assemble_first_run_feed_non_200", {
      status: response.status,
      fix_suggestion:
        "401 = expired session; 404 = onboarded-but-no-profile; 500 = worker error. Treat any non-200 as non-fatal.",
    });
    throw new Error(`assemble-mine returned HTTP ${response.status}`);
  }

  const body: unknown = await response.json();
  const result = parseAssembleResponse(body);
  if (result === null) {
    logger.error("assemble_first_run_feed_malformed_body", {
      fix_suggestion: "Endpoint must return { allocated_count, feed_total }.",
    });
    throw new Error("Malformed assemble-mine response body.");
  }

  logger.info("assemble_first_run_feed_completed", { allocated_count: result.allocated_count });
  return result;
}

/**
 * The localStorage key prefix for the per-date first-run flag. SP3's reel reads
 * `firstRunFlagKey(feed_date)` to derive `is_first_run` for the "past 24 hours"
 * banner. Keyed by feed date so the flag naturally expires day-over-day.
 */
const FIRST_RUN_FLAG_PREFIX = "blip:first-run:";

/**
 * Build the localStorage key for a given feed date's first-run flag.
 *
 * @param feedDate - The feed date (`YYYY-MM-DD`).
 * @returns The namespaced localStorage key, e.g. `blip:first-run:2026-06-16`.
 *
 * @example
 * firstRunFlagKey("2026-06-16"); // → "blip:first-run:2026-06-16"
 */
export function firstRunFlagKey(feedDate: string): string {
  return `${FIRST_RUN_FLAG_PREFIX}${feedDate}`;
}

/**
 * Persist the per-date first-run flag (set ONLY after a successful assembly).
 *
 * SP3 reads this to gate the "Showing you the past 24 hours — n/30" banner so it
 * shows only on the day-one first-run feed. A `localStorage` failure (private
 * mode / quota) is non-fatal and swallowed — the banner simply won't show.
 *
 * @param feedDate - The feed date (`YYYY-MM-DD`) the flag is keyed to.
 *
 * @example
 * markFirstRunFeed("2026-06-16"); // localStorage["blip:first-run:2026-06-16"] = "1"
 */
export function markFirstRunFeed(feedDate: string): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(firstRunFlagKey(feedDate), "1");
  } catch (error: unknown) {
    logger.warn("mark_first_run_feed_failed", {
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion: "localStorage unavailable (private mode/quota); the first-run banner will be skipped — harmless.",
    });
  }
}
