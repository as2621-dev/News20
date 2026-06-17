import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  dispatchUserSourceIngestion,
  listUsersWithFollowedSources,
  runSourceIngestionFanOut,
  SOURCE_INGESTION_CRON,
  type UserIngestionOutcome,
} from "../../../trigger/sourceIngestion";

/**
 * sourceIngestion.ts — the 2-hourly followed-source ingestion Trigger.dev v4 schedule.
 *
 * WHY these tests (Rule 9): the cron's contract is "every 2 hours, fan
 * `run_source_ingestion` out to each user who follows ≥1 source, logging per-user
 * counts and never silently dropping a user". Four things make or break it:
 *   (a) the cron pattern must be a valid 5-field every-2-hours expression — a wrong
 *       pattern silently changes how often sources are polled;
 *   (b) the fan-out must dispatch EXACTLY ONCE per DISTINCT follower (dedup of the
 *       user_content_sources rows) — double-dispatch double-spends ingestion, a
 *       missed user leaves their followed sources stale;
 *   (c) one user's dispatch failure must NOT abort the others (Rule 12 — surfaced as
 *       an error event, batch continues);
 *   (d) per-user counts (fetched / promoted / dropped) must be logged — no silent
 *       caps (plan SP4 DoD).
 *
 * The Supabase client is a hand-rolled thenable mock and the per-user dispatch is
 * injected so NO worker HTTP call is made (CLAUDE.md: mock at the boundary). One test
 * exercises the real `dispatchUserSourceIngestion` with `fetch` stubbed to prove the
 * bearer-auth seam matches the daily pipeline.
 */

const WORKER_BASE_URL = "https://worker.example.com";
const PIPELINE_TRIGGER_SECRET = "test-pipeline-secret";

/**
 * Build a chainable Supabase-client mock whose single `user_content_sources` read
 * resolves to the canned follow rows.
 *
 * Production issues exactly one `.from("user_content_sources").select("user_id")`
 * resolving `{ data }`. The builder is a real resolved Promise with `.select`
 * returning itself, so the awaited chain yields the canned result (no hand-written
 * `then`, which the linter forbids).
 */
function makeSupabaseMock(followRows: Array<{ user_id: string }>) {
  const from = vi.fn((table: string) => {
    const result = table === "user_content_sources" ? { data: followRows, error: null } : { data: [], error: null };
    const builder = Promise.resolve(result) as Promise<typeof result> & Record<string, unknown>;
    builder.select = vi.fn(() => builder);
    return builder;
  });
  return { from } as unknown as Parameters<typeof runSourceIngestionFanOut>[0] & {
    from: ReturnType<typeof vi.fn>;
  };
}

/** A zeroed-but-shaped per-user outcome for a given user id. */
function outcomeFor(userId: string, overrides: Partial<UserIngestionOutcome> = {}): UserIngestionOutcome {
  return { userId, workerStatus: 202, itemsFetched: 0, itemsPromoted: 0, itemsDropped: 0, ...overrides };
}

describe("sourceIngestionTask cron", () => {
  it("declares a valid every-2-hours 5-field cron expression", () => {
    expect(SOURCE_INGESTION_CRON).toBe("0 */2 * * *");
    // A standard 5-field cron: minute hour day-of-month month day-of-week.
    const fields = SOURCE_INGESTION_CRON.split(" ");
    expect(fields).toHaveLength(5);
    expect(fields[0]).toBe("0"); // top of the hour
    expect(fields[1]).toBe("*/2"); // every 2 hours
  });
});

describe("listUsersWithFollowedSources", () => {
  it("returns the DISTINCT follower user ids (dedups multiple follows per user)", async () => {
    const supabase = makeSupabaseMock([
      { user_id: "u1" },
      { user_id: "u1" }, // u1 follows two sources → still ONE user
      { user_id: "u2" },
    ]);

    const userIds = await listUsersWithFollowedSources(supabase);

    expect(userIds.sort()).toEqual(["u1", "u2"]);
  });

  it("returns an empty list when no user follows any source", async () => {
    const supabase = makeSupabaseMock([]);
    expect(await listUsersWithFollowedSources(supabase)).toEqual([]);
  });
});

describe("runSourceIngestionFanOut", () => {
  let logSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logSpy.mockRestore();
  });

  /** Parse every console.log JSON line the run emitted into objects. */
  function loggedEvents(): Array<Record<string, unknown>> {
    return logSpy.mock.calls.map((call: unknown[]) => JSON.parse(String(call[0])) as Record<string, unknown>);
  }

  it("dispatches exactly once per distinct user and logs per-user counts", async () => {
    const supabase = makeSupabaseMock([{ user_id: "u1" }, { user_id: "u1" }, { user_id: "u2" }]);
    const dispatch = vi.fn(async (userId: string) =>
      outcomeFor(userId, { itemsFetched: 3, itemsPromoted: 2, itemsDropped: 1 }),
    );

    const result = await runSourceIngestionFanOut(supabase, dispatch);

    expect(result.scheduled).toBe(true);
    expect(result.userCount).toBe(2);
    expect(dispatch).toHaveBeenCalledTimes(2);
    expect(dispatch.mock.calls.map((c) => c[0]).sort()).toEqual(["u1", "u2"]);

    // Per-user counts are logged (no silent caps — DoD).
    const completed = loggedEvents().filter((e) => e.event === "source_ingestion_user_completed");
    expect(completed).toHaveLength(2);
    expect(completed[0].items_fetched).toBe(3);
    expect(completed[0].items_promoted).toBe(2);
    expect(completed[0].items_dropped).toBe(1);

    // The completion summary aggregates the totals.
    const summary = loggedEvents().find((e) => e.event === "source_ingestion_fanout_completed");
    expect(summary?.succeeded).toBe(2);
    expect(summary?.total_items_promoted).toBe(4);
  });

  it("continues the batch when one user's dispatch fails (Rule 12 — surfaced, not fatal)", async () => {
    const supabase = makeSupabaseMock([{ user_id: "u1" }, { user_id: "u2" }]);
    const dispatch = vi.fn(async (userId: string) => {
      if (userId === "u1") {
        throw new Error("worker 500 for u1");
      }
      return outcomeFor(userId, { itemsPromoted: 5 });
    });

    const result = await runSourceIngestionFanOut(supabase, dispatch);

    // u2 still ran despite u1 failing.
    expect(dispatch).toHaveBeenCalledTimes(2);
    expect(result.outcomes).toHaveLength(1);
    expect(result.outcomes[0].userId).toBe("u2");

    const failed = loggedEvents().find((e) => e.event === "source_ingestion_user_failed");
    expect(failed?.user_id).toBe("u1");
    expect(String(failed?.error_message)).toContain("worker 500 for u1");
  });
});

describe("dispatchUserSourceIngestion (worker HTTP seam)", () => {
  beforeEach(() => {
    process.env.WORKER_BASE_URL = WORKER_BASE_URL;
    process.env.PIPELINE_TRIGGER_SECRET = PIPELINE_TRIGGER_SECRET;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.WORKER_BASE_URL;
    delete process.env.PIPELINE_TRIGGER_SECRET;
  });

  it("POSTs to /ingestion/sources with bearer auth and the user_id body, parsing counts", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ items_fetched: 7, items_promoted: 4, items_dropped: 3 }), { status: 202 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const outcome = await dispatchUserSourceIngestion("u1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${WORKER_BASE_URL}/ingestion/sources`);
    expect(init.headers.authorization).toBe(`Bearer ${PIPELINE_TRIGGER_SECRET}`);
    expect(JSON.parse(init.body)).toEqual({ user_id: "u1" });

    expect(outcome).toEqual({
      userId: "u1",
      workerStatus: 202,
      itemsFetched: 7,
      itemsPromoted: 4,
      itemsDropped: 3,
    });
  });

  it("throws on a non-2xx worker response (surfaced so Trigger.dev marks it failed)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("nope", { status: 500, statusText: "Server Error" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(dispatchUserSourceIngestion("u1")).rejects.toThrow(/rejected source ingestion for user u1/);
  });
});
