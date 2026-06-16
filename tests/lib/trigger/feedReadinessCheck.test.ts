import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FEED_READINESS_CRON, runFeedReadinessCheck } from "../../../trigger/feedReadinessCheck";

/**
 * feedReadinessCheck.ts — the 05:00-ET safety-net Trigger.dev v4 schedule.
 *
 * WHY these tests (Rule 9): the cron's contract is "five hours after the midnight
 * run, re-produce the day's feeds IF they're missing — and always surface the
 * missing-poster readiness signal". Three things make or break that contract:
 *   (a) the cron must carry `timezone: "America/New_York"` at 05:00 — a UTC cron
 *       would fire at the wrong wall-clock hour and DST-drift twice a year;
 *   (b) "no feeds for today" (active users present, zero daily_feeds rows) must
 *       re-invoke POST /pipeline/daily EXACTLY ONCE — a missed re-run leaves users
 *       feedless all morning; a double-run double-spends the paid pipeline;
 *   (c) "feeds present" must NO-OP (zero POSTs) — re-running on a healthy day
 *       wastes pipeline cost;
 *   and in BOTH branches the missing-poster count must be logged (the readiness
 *       signal Phase 7d will act on).
 *
 * `fetch` is mocked at the global boundary and the Supabase client is a hand-rolled
 * chainable mock (CLAUDE.md: mock at the boundary, never hit real services).
 */

const WORKER_BASE_URL = "https://worker.example.com";
const PIPELINE_TRIGGER_SECRET = "test-pipeline-secret";

/**
 * Build a chainable Supabase-client mock whose three reads return canned values.
 *
 * The production code issues exactly three `.from(...)` reads:
 *   - `user_interest_profile` → `.select("profile_user_id")` resolving `{ data }`
 *   - `daily_feeds` → `.select(..., head).eq(...)` resolving `{ count }`
 *   - `stories` → `.select(..., head).eq(...).is(...)` resolving `{ count }`
 *
 * Each builder is a thenable that resolves to its table's canned result, with
 * `.select/.eq/.is` returning `this` so any chain length works.
 */
function makeSupabaseMock(options: {
  activeUserRows: Array<{ profile_user_id: string }>;
  todaysFeedCount: number;
  missingPosterCount: number;
}) {
  const resultByTable: Record<string, { data?: unknown; count?: number; error: null }> = {
    user_interest_profile: { data: options.activeUserRows, error: null },
    daily_feeds: { count: options.todaysFeedCount, error: null },
    stories: { count: options.missingPosterCount, error: null },
  };

  const from = vi.fn((table: string) => {
    const result = resultByTable[table];
    // Reason: build the chain ON a real resolved Promise, so `await ...select().eq()`
    // resolves to the canned `{ data | count, error }` via the genuine Promise (no
    // hand-written `then` property, which the linter forbids). `.select/.eq/.is`
    // each return the same Promise so any chain length awaits the same result.
    const builder = Promise.resolve(result) as Promise<typeof result> & Record<string, unknown>;
    builder.select = vi.fn(() => builder);
    builder.eq = vi.fn(() => builder);
    builder.is = vi.fn(() => builder);
    return builder;
  });

  return { from } as unknown as Parameters<typeof runFeedReadinessCheck>[1] & {
    from: ReturnType<typeof vi.fn>;
  };
}

describe("feedReadinessCheckTask cron", () => {
  it("declares the 05:00 cron with the America/New_York timezone (DST-safe)", () => {
    expect(FEED_READINESS_CRON).toEqual({
      pattern: "0 5 * * *",
      timezone: "America/New_York",
    });
  });
});

describe("runFeedReadinessCheck", () => {
  let logSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    process.env.WORKER_BASE_URL = WORKER_BASE_URL;
    process.env.PIPELINE_TRIGGER_SECRET = PIPELINE_TRIGGER_SECRET;
    logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logSpy.mockRestore();
    delete process.env.WORKER_BASE_URL;
    delete process.env.PIPELINE_TRIGGER_SECRET;
  });

  /** Parse every console.log JSON line the run emitted into objects. */
  function loggedEvents(): Array<Record<string, unknown>> {
    return logSpy.mock.calls.map((call: unknown[]) => JSON.parse(String(call[0])) as Record<string, unknown>);
  }

  it("re-invokes POST /pipeline/daily exactly once when today's feeds are missing", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ accepted: true }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    const supabase = makeSupabaseMock({
      activeUserRows: [{ profile_user_id: "u1" }, { profile_user_id: "u2" }],
      todaysFeedCount: 0,
      missingPosterCount: 4,
    });

    // 05:00 ET on 2026-01-15 is 10:00:00Z → ET target_date is 2026-01-15.
    const result = await runFeedReadinessCheck(new Date("2026-01-15T10:00:00Z"), supabase);

    expect(result.reranPipeline).toBe(true);
    expect(result.targetDate).toBe("2026-01-15");
    expect(result.activeUserCount).toBe(2);
    expect(result.missingPosterCount).toBe(4);

    // Re-run POSTed exactly once, with the bearer header and the ET target_date.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${WORKER_BASE_URL}/pipeline/daily`);
    expect(init.headers.authorization).toBe(`Bearer ${PIPELINE_TRIGGER_SECRET}`);
    expect(JSON.parse(init.body)).toEqual({ target_date: "2026-01-15" });

    // Missing-poster count is logged in the missing-feeds branch.
    const evaluated = loggedEvents().find((event) => event.event === "feed_readiness_evaluated");
    expect(evaluated?.missing_poster_count).toBe(4);
    expect(evaluated?.feeds_missing).toBe(true);
  });

  it("no-ops (zero POSTs) when today's feeds are present, still logging the poster count", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const supabase = makeSupabaseMock({
      activeUserRows: [{ profile_user_id: "u1" }],
      todaysFeedCount: 30,
      missingPosterCount: 2,
    });

    const result = await runFeedReadinessCheck(new Date("2026-01-15T10:00:00Z"), supabase);

    expect(result.reranPipeline).toBe(false);
    expect(result.todaysFeedCount).toBe(30);
    expect(fetchMock).not.toHaveBeenCalled();

    // Missing-poster count is ALSO logged in the feeds-present branch (DoD).
    const evaluated = loggedEvents().find((event) => event.event === "feed_readiness_evaluated");
    expect(evaluated?.missing_poster_count).toBe(2);
    expect(evaluated?.feeds_missing).toBe(false);
  });

  it("does NOT re-run when there are no active users (no feeds expected)", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const supabase = makeSupabaseMock({
      activeUserRows: [],
      todaysFeedCount: 0,
      missingPosterCount: 0,
    });

    const result = await runFeedReadinessCheck(new Date("2026-01-15T10:00:00Z"), supabase);

    expect(result.reranPipeline).toBe(false);
    expect(result.activeUserCount).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
