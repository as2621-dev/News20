/**
 * Player-signal persistence + the Voice-session daily quota guard (Phase 3b SP4).
 *
 * Two concerns, one module, because both are owned by the same surface (the
 * in-news Voice mode) and neither warrants its own file:
 *
 * 1. **`recordVoiceSignal`** — the ONLY client-side writer of `player_signals`.
 *    Opening Voice mode writes one `event_type='voice'` row (the deep-engagement
 *    signal the daily profile-update job reads). Mirrors `src/lib/follows.ts`
 *    exactly: optional injected `client` defaulting to the shared browser anon
 *    client, the user id resolved app-side so a signed-out caller degrades to a
 *    no-op (no throw, no row) rather than relying on RLS to reject an anon write,
 *    and the row owner-scoped by `player_signals_owner_all` (migration 0003).
 *    `signal_story_id` is `stories.story_id` (carried on `Story.digest_id`).
 *
 * 2. **The Voice quota guard** — the expensive resource is the Gemini Live WSS
 *    (a paid realtime session), NOT the cheap `/question` calls. We port TLDW's
 *    600s/day **heartbeat + hard-cap** pattern: a per-local-day tally of session
 *    seconds in `localStorage` (no DB table, no migration), a hard cap that blocks
 *    a NEW session once exceeded, and a heartbeat that accumulates seconds while a
 *    session is live. Client-side only — this bounds cost on the happy path; it is
 *    not a security boundary (a determined client can clear localStorage). A
 *    server-side budget can layer on later if abuse appears.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** The `player_signals` table name — single source of truth for table/column refs. */
const PLAYER_SIGNALS_TABLE = "player_signals";

/** The `event_type` enum value for entering Voice mode (migration 0003 enum). */
const VOICE_EVENT_TYPE = "voice";

/**
 * Resolve the authed user's id, or `null` when signed out.
 *
 * Mirrors `follows.ts` so the signed-out path is a graceful no-op (Voice is
 * usable signed-out; the engagement signal simply isn't recorded then).
 *
 * @param client - The Supabase client to read the session from.
 * @returns The authed `user_id` (= `auth.uid()`), or `null` if unauthenticated.
 */
async function resolveAuthedUserId(client: SupabaseClient): Promise<string | null> {
  const { data, error } = await client.auth.getUser();
  if (error || !data.user) {
    return null;
  }
  return data.user.id;
}

/**
 * Record ONE `player_signals` `voice` row for the authed user + story.
 *
 * Called once per Voice-mode open (the caller guards against double-fire). The
 * row is owner-scoped — `signal_user_id` is pinned to the authed id (also
 * enforced by `player_signals_owner_all` RLS), so a caller can only ever write
 * their own signal. `occurred_at` defaults to `now()` (migration 0003). NEVER
 * throws into the UI: a signed-out caller or a write error is logged and
 * swallowed so a failed signal can't break the hands-free conversation (Rule 12).
 *
 * @param story_id - The `stories.story_id` (the reel `Story.digest_id`).
 * @param client - Optional Supabase client (injected in tests; defaults to the
 *   shared browser anon client).
 *
 * @example
 * await recordVoiceSignal("s1"); // one player_signals row, event_type='voice'
 */
export async function recordVoiceSignal(story_id: string, client?: SupabaseClient): Promise<void> {
  // Reason (Rule 12): resolve the client INSIDE the guard, not as an eager default
  // arg. getSupabaseBrowserClient() throws when the public env vars are missing —
  // an eager default would let that throw escape as an unhandled rejection (the
  // caller fires this fire-and-forget). Building it here keeps the documented
  // "never throws into the UI" contract honest; tests inject a client so they skip
  // this branch.
  let supabaseClient: SupabaseClient;
  try {
    supabaseClient = client ?? getSupabaseBrowserClient();
  } catch (error: unknown) {
    logger.error("voice_signal_client_unavailable", {
      story_id,
      error_message: error instanceof Error ? error.message : "unknown",
      fix_suggestion:
        "Set NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY; the voice signal is skipped without a client.",
    });
    return;
  }

  const authedUserId = await resolveAuthedUserId(supabaseClient);
  if (!authedUserId) {
    // Reason: Voice is usable signed-out; the engagement signal is authed-only,
    // so we no-op (no crash, no anon row the RLS would reject anyway).
    logger.warn("voice_signal_skipped_unauthenticated", {
      story_id,
      fix_suggestion:
        "Sign in (email magic-link) to record the voice engagement signal — anon users have no player_signals row.",
    });
    return;
  }

  const { error } = await supabaseClient.from(PLAYER_SIGNALS_TABLE).insert({
    signal_user_id: authedUserId,
    signal_story_id: story_id,
    event_type: VOICE_EVENT_TYPE,
  });

  if (error) {
    // Reason: the signal is best-effort engagement telemetry — a failed write
    // must never break the live conversation; log + swallow (never throw).
    logger.error("voice_signal_write_failed", {
      story_id,
      error_message: error.message,
      fix_suggestion:
        "Confirm migration 0003 applied and the player_signals_owner_all RLS policy allows the authed INSERT.",
    });
    return;
  }
  logger.info("voice_signal_recorded", { story_id, event_type: VOICE_EVENT_TYPE });
}

// ── Voice-session daily quota (TLDW 600s/day heartbeat + hard-cap) ───────────

/**
 * The per-user daily Live-session budget in seconds. Defaults to TLDW's 600s/day
 * (10 minutes). A named constant so it is easy to tune once commuter session
 * length is known (plan Open question — "Quota tuning").
 */
export const VOICE_DAILY_QUOTA_SECONDS = 600;

/** The `localStorage` key holding the per-day Voice-session tally. */
const VOICE_QUOTA_STORAGE_KEY = "n20-voice-quota";

/** The persisted shape: which local day, and how many session-seconds it holds. */
interface VoiceQuotaRecord {
  /** The local calendar day this tally is for (`YYYY-MM-DD`), so it resets daily. */
  quota_local_date: string;
  /** Accumulated Live-session seconds for `quota_local_date`. */
  seconds_used_today: number;
}

/** The quota state the open boundary reads to decide whether to start a session. */
export interface VoiceQuotaState {
  /** Seconds of Live session already used today (0 on a fresh local day). */
  seconds_used_today: number;
  /** True once today's usage has reached/exceeded {@link VOICE_DAILY_QUOTA_SECONDS}. */
  is_over_quota: boolean;
}

/**
 * The current local calendar day as `YYYY-MM-DD`. Used as the tally's reset key —
 * a new day yields a fresh zero tally without any cron/cleanup.
 *
 * @returns The local date string, e.g. `"2026-05-31"`.
 */
function getLocalDateKey(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Read the persisted quota tally for TODAY. A record for a previous day (or a
 * missing/corrupt one) is treated as a fresh zero tally for today, so the budget
 * resets at local midnight with no cleanup job.
 *
 * SSR/no-`localStorage` safe: returns a zero tally when `window`/`localStorage`
 * is unavailable (the static-export build renders some pages server-side).
 *
 * @returns Today's tally (zeroed on a new day or when storage is unavailable).
 */
function readTodayTally(): VoiceQuotaRecord {
  const today = getLocalDateKey();
  const empty: VoiceQuotaRecord = { quota_local_date: today, seconds_used_today: 0 };

  if (typeof window === "undefined" || !window.localStorage) {
    return empty;
  }
  try {
    const raw = window.localStorage.getItem(VOICE_QUOTA_STORAGE_KEY);
    if (!raw) {
      return empty;
    }
    const parsed = JSON.parse(raw) as Partial<VoiceQuotaRecord>;
    // Reason: a tally from a previous local day is stale → reset to zero today.
    if (parsed.quota_local_date !== today || typeof parsed.seconds_used_today !== "number") {
      return empty;
    }
    return { quota_local_date: today, seconds_used_today: Math.max(0, parsed.seconds_used_today) };
  } catch {
    // Reason: corrupt/blocked storage must never break the gate — fall back to a
    // fresh tally (worst case: the cap under-counts, never over-blocks silently).
    return empty;
  }
}

/**
 * Persist today's tally. Best-effort: a storage write failure (quota exceeded,
 * private mode) is logged and swallowed — it must never break the conversation.
 *
 * @param tally - The record to persist.
 */
function writeTodayTally(tally: VoiceQuotaRecord): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(VOICE_QUOTA_STORAGE_KEY, JSON.stringify(tally));
  } catch (error: unknown) {
    logger.warn("voice_quota_persist_failed", {
      error_message: error instanceof Error ? error.message : "unknown",
      fix_suggestion:
        "localStorage write failed (private mode / quota); the daily Voice cap may under-count this session.",
    });
  }
}

/**
 * Read the current Voice-session quota state for TODAY.
 *
 * The open boundary calls this BEFORE opening the socket: when `is_over_quota` is
 * true it must block the new session with a calm message instead of connecting
 * (Rule 12 — no silent failure).
 *
 * @returns Today's seconds-used + whether the daily hard cap is reached.
 *
 * @example
 * if (getVoiceQuotaState().is_over_quota) renderCalmBlock();
 */
export function getVoiceQuotaState(): VoiceQuotaState {
  const seconds_used_today = readTodayTally().seconds_used_today;
  return {
    seconds_used_today,
    is_over_quota: seconds_used_today >= VOICE_DAILY_QUOTA_SECONDS,
  };
}

/**
 * Add `seconds` to today's Live-session tally (the heartbeat accumulator).
 *
 * Called periodically WHILE a session is live (see {@link startVoiceQuotaHeartbeat})
 * so the daily budget reflects real usage and the next open is blocked once the
 * cap is crossed. Clamps non-finite/negative inputs to 0 (defensive).
 *
 * @param seconds - Elapsed Live-session seconds to add (e.g. one heartbeat tick).
 * @returns The updated quota state after accumulating.
 *
 * @example
 * recordVoiceHeartbeat(5); // +5s toward the 600s/day cap
 */
export function recordVoiceHeartbeat(seconds: number): VoiceQuotaState {
  const increment = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const tally = readTodayTally();
  const updated: VoiceQuotaRecord = {
    quota_local_date: tally.quota_local_date,
    seconds_used_today: tally.seconds_used_today + increment,
  };
  writeTodayTally(updated);
  return {
    seconds_used_today: updated.seconds_used_today,
    is_over_quota: updated.seconds_used_today >= VOICE_DAILY_QUOTA_SECONDS,
  };
}

/** The heartbeat tick interval (seconds). 5s balances accuracy vs. write churn. */
export const VOICE_HEARTBEAT_INTERVAL_SECONDS = 5;

/**
 * Start a heartbeat that accumulates Live-session seconds toward the daily cap.
 *
 * Ticks every {@link VOICE_HEARTBEAT_INTERVAL_SECONDS}, adding that many seconds to
 * today's tally. Returns a `stop()` to call when the session ends (close/unmount)
 * — the caller MUST invoke it or the tally keeps growing. Mirrors TLDW's "tick
 * while talking" budget: cost accrues for the time the WSS is actually open.
 *
 * @returns A stop function that clears the heartbeat timer (idempotent).
 *
 * @example
 * const stop = startVoiceQuotaHeartbeat();
 * // …session runs…
 * stop(); // on close/unmount
 */
export function startVoiceQuotaHeartbeat(): () => void {
  const intervalId = setInterval(() => {
    recordVoiceHeartbeat(VOICE_HEARTBEAT_INTERVAL_SECONDS);
  }, VOICE_HEARTBEAT_INTERVAL_SECONDS * 1000);

  let isStopped = false;
  return (): void => {
    if (isStopped) {
      return;
    }
    isStopped = true;
    clearInterval(intervalId);
  };
}
