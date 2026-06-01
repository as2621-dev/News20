import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getVoiceQuotaState,
  recordVoiceHeartbeat,
  recordVoiceSignal,
  startVoiceQuotaHeartbeat,
  VOICE_DAILY_QUOTA_SECONDS,
  VOICE_HEARTBEAT_INTERVAL_SECONDS,
} from "@/lib/signals";

/**
 * Phase 3b SP4 — voice engagement signal + the daily Live-session quota guard.
 *
 * WHY these tests exist (Rule 9 — encode the contract, not just the call shape):
 *  - `recordVoiceSignal` is the ONLY client writer of `player_signals`. Opening
 *    Voice MUST persist exactly ONE owner-scoped `event_type='voice'` row keyed to
 *    the authed user + story — a wrong/missing row silently mis-weights tomorrow's
 *    interest profile. Signed-out MUST write nothing and never throw (Voice is
 *    usable signed-out; a thrown signal would break the hands-free conversation).
 *  - The quota guard bounds the cost of the paid Gemini Live WSS. Reaching the
 *    daily hard cap MUST report `is_over_quota` so the open boundary blocks a new
 *    session (Rule 12) — a regression that never trips the cap would silently burn
 *    budget. The tally MUST reset on a new local day.
 *
 * Mocks the Supabase client at the boundary (CLAUDE.md mocking strategy), mirroring
 * tests/lib/follows.test.ts. localStorage is stubbed per-test so the quota tally is
 * deterministic and isolated.
 */

const AUTHED_USER_ID = "user-uuid-1";
const STORY_ID = "s1";

/**
 * Build a fake Supabase client whose `auth.getUser()` resolves to the configured
 * user and whose `from("player_signals").insert()` is a captured spy.
 *
 * @param options.user - The authed user (or null for signed-out).
 * @param options.insertError - An error to return from the insert (else success).
 */
function makeFakeClient(options: { user: { id: string } | null; insertError?: { message: string } | null }) {
  const getUser = vi.fn().mockResolvedValue({ data: { user: options.user }, error: null });
  const insert = vi.fn().mockResolvedValue({ error: options.insertError ?? null });
  const from = vi.fn().mockReturnValue({ insert });
  // Reason: the fake only implements the surface signals.ts uses; `as never`
  // satisfies the SupabaseClient type at this test boundary without a full stub.
  const client = { auth: { getUser }, from } as never;
  return { client, getUser, from, insert };
}

describe("recordVoiceSignal", () => {
  it("inserts exactly one owner-scoped voice row keyed to the authed user + story", async () => {
    // WHY: the DoD — opening Voice MUST persist exactly one player_signals row
    // pinned to (authed user, story) with event_type 'voice'. Fails if the insert
    // is dropped, sent unscoped, or the event type drifts.
    const { client, insert } = makeFakeClient({ user: { id: AUTHED_USER_ID } });

    await recordVoiceSignal(STORY_ID, client);

    expect(insert).toHaveBeenCalledTimes(1);
    expect(insert).toHaveBeenCalledWith({
      signal_user_id: AUTHED_USER_ID,
      signal_story_id: STORY_ID,
      event_type: "voice",
    });
  });

  it("writes nothing and does not throw when signed out (graceful degrade, Rule 12)", async () => {
    // WHY: Voice is usable signed-out; the engagement signal is authed-only. A
    // signed-out open MUST no-op (anon row the RLS rejects anyway) and never throw
    // into the live conversation. Fails if the unauth guard is removed.
    const { client, from } = makeFakeClient({ user: null });

    await expect(recordVoiceSignal(STORY_ID, client)).resolves.toBeUndefined();
    expect(from).not.toHaveBeenCalled();
  });

  it("swallows a write error (logs, never throws) so a failed signal can't break Voice", async () => {
    // WHY: the signal is best-effort telemetry — a DB error must not propagate into
    // the hands-free conversation. Fails if the error path rethrows.
    const { client } = makeFakeClient({
      user: { id: AUTHED_USER_ID },
      insertError: { message: "rls denied" },
    });

    await expect(recordVoiceSignal(STORY_ID, client)).resolves.toBeUndefined();
  });
});

// ── Quota guard ──────────────────────────────────────────────────────────────

/** A minimal in-memory localStorage stub for deterministic quota tests. */
function installLocalStorageStub(): { store: Map<string, string> } {
  const store = new Map<string, string>();
  const stub = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
  };
  Object.defineProperty(globalThis, "localStorage", { value: stub, configurable: true, writable: true });
  return { store };
}

describe("voice daily quota guard", () => {
  beforeEach(() => {
    installLocalStorageStub();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("starts under quota on a fresh day (zero seconds used)", () => {
    const state = getVoiceQuotaState();
    expect(state.seconds_used_today).toBe(0);
    expect(state.is_over_quota).toBe(false);
  });

  it("trips is_over_quota once accumulated seconds reach the daily hard cap", () => {
    // WHY: the cap is the cost ceiling — reaching it MUST flip is_over_quota so the
    // open boundary blocks the next session (Rule 12). A regression that never trips
    // would silently burn paid WSS budget.
    recordVoiceHeartbeat(VOICE_DAILY_QUOTA_SECONDS - 1);
    expect(getVoiceQuotaState().is_over_quota).toBe(false);

    recordVoiceHeartbeat(1); // now exactly at the cap
    const atCap = getVoiceQuotaState();
    expect(atCap.seconds_used_today).toBe(VOICE_DAILY_QUOTA_SECONDS);
    expect(atCap.is_over_quota).toBe(true);
  });

  it("resets the tally on a new local day", () => {
    // WHY: the budget is per-day. A tally from yesterday MUST NOT block today.
    recordVoiceHeartbeat(VOICE_DAILY_QUOTA_SECONDS);
    expect(getVoiceQuotaState().is_over_quota).toBe(true);

    // Advance the system clock past local midnight (25h) so the date key changes.
    vi.setSystemTime(new Date(Date.now() + 25 * 60 * 60 * 1000));

    const nextDay = getVoiceQuotaState();
    expect(nextDay.seconds_used_today).toBe(0);
    expect(nextDay.is_over_quota).toBe(false);
  });

  it("heartbeat accumulates seconds while running and stops cleanly", () => {
    const stop = startVoiceQuotaHeartbeat();
    // Three ticks of the interval → 3 × interval seconds accrued.
    vi.advanceTimersByTime(VOICE_HEARTBEAT_INTERVAL_SECONDS * 1000 * 3);
    expect(getVoiceQuotaState().seconds_used_today).toBe(VOICE_HEARTBEAT_INTERVAL_SECONDS * 3);

    stop();
    vi.advanceTimersByTime(VOICE_HEARTBEAT_INTERVAL_SECONDS * 1000 * 3);
    // No further accrual after stop().
    expect(getVoiceQuotaState().seconds_used_today).toBe(VOICE_HEARTBEAT_INTERVAL_SECONDS * 3);
  });
});
