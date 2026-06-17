import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cronEnabled,
  DAILY_FEED_CRON,
  easternCalendarDate,
  runDailyPipelineSchedule,
} from "../../../trigger/dailyPipeline";

/**
 * dailyPipeline.ts — the midnight-ET Trigger.dev v4 schedule that drives the
 * Python daily pipeline over HTTP.
 *
 * WHY these tests (Rule 9): the schedule's contract is "at 00:00 America/New_York,
 * POST the ET calendar date to the worker with the shared bearer token". Three
 * things make or break that contract:
 *   (a) the cron must carry `timezone: "America/New_York"` — a UTC-only cron would
 *       fire at the wrong wall-clock hour and DST-drift twice a year;
 *   (b) `target_date` must be the ET calendar date, NOT the UTC date — at 00:00 ET
 *       the UTC instant is already the next calendar day, so a naive UTC slice
 *       would target tomorrow and produce the wrong feed;
 *   (c) the POST must carry `Authorization: Bearer <secret>` and a non-2xx must
 *       throw — otherwise an unauthenticated or failed dispatch would be silently
 *       reported as a successful schedule (Rule 12).
 *
 * `fetch` is mocked at the global boundary (CLAUDE.md mocking strategy) so no real
 * HTTP is made; the ET-date helper is pure and tested with pinned instants.
 */

const WORKER_BASE_URL = "https://worker.example.com";
const PIPELINE_TRIGGER_SECRET = "test-pipeline-secret";

describe("easternCalendarDate", () => {
  it("returns the ET calendar date for a late-evening ET instant that is next-day UTC", () => {
    // 2026-03-09T03:30:00Z is 22:30 the previous day in New York (EST, UTC-5).
    expect(easternCalendarDate(new Date("2026-03-09T03:30:00Z"))).toBe("2026-03-08");
  });

  it("returns the ET calendar date at exactly 00:00 ET (the cron's own firing instant)", () => {
    // 00:00 EST on 2026-01-15 is 05:00:00Z — the ET date is the 15th, not the 14th/15th-UTC edge.
    expect(easternCalendarDate(new Date("2026-01-15T05:00:00Z"))).toBe("2026-01-15");
  });

  it("applies EDT (UTC-4) during daylight time", () => {
    // 00:00 EDT on 2026-07-04 is 04:00:00Z.
    expect(easternCalendarDate(new Date("2026-07-04T04:00:00Z"))).toBe("2026-07-04");
  });
});

describe("dailyPersonalizedFeedTask cron", () => {
  it("declares the midnight cron with the America/New_York timezone (DST-safe)", () => {
    expect(DAILY_FEED_CRON).toEqual({
      pattern: "0 0 * * *",
      timezone: "America/New_York",
    });
  });
});

describe("cronEnabled (kill-switch)", () => {
  afterEach(() => {
    delete process.env.PIPELINE_CRON_ENABLED;
  });

  // WHY (Rule 9): the crons must stay frozen unless EXPLICITLY enabled. Off-by-default
  // is the safety contract — a truthy-but-not-"true" value (or unset) must NOT run.
  it("is false when PIPELINE_CRON_ENABLED is unset", () => {
    delete process.env.PIPELINE_CRON_ENABLED;
    expect(cronEnabled()).toBe(false);
  });

  it("is false for any value other than the literal 'true'", () => {
    for (const value of ["false", "1", "yes", "TRUE", ""]) {
      process.env.PIPELINE_CRON_ENABLED = value;
      expect(cronEnabled()).toBe(false);
    }
  });

  it("is true only when PIPELINE_CRON_ENABLED === 'true'", () => {
    process.env.PIPELINE_CRON_ENABLED = "true";
    expect(cronEnabled()).toBe(true);
  });
});

describe("runDailyPipelineSchedule", () => {
  beforeEach(() => {
    process.env.WORKER_BASE_URL = WORKER_BASE_URL;
    process.env.PIPELINE_TRIGGER_SECRET = PIPELINE_TRIGGER_SECRET;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.WORKER_BASE_URL;
    delete process.env.PIPELINE_TRIGGER_SECRET;
  });

  it("POSTs the ET target_date with the bearer header and returns the worker status", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ run_id: "abc", accepted: true }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    // 03:30Z is 22:30 the previous ET day → target_date must be 2026-03-08.
    const result = await runDailyPipelineSchedule(new Date("2026-03-09T03:30:00Z"));

    expect(result).toEqual({ scheduled: true, targetDate: "2026-03-08", workerStatus: 202 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${WORKER_BASE_URL}/pipeline/daily`);
    expect(init.method).toBe("POST");
    expect(init.headers.authorization).toBe(`Bearer ${PIPELINE_TRIGGER_SECRET}`);
    expect(JSON.parse(init.body)).toEqual({ target_date: "2026-03-08" });
  });

  it("throws when the worker responds non-2xx so Trigger.dev surfaces the failure", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("invalid bearer token", { status: 401, statusText: "Unauthorized" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(runDailyPipelineSchedule(new Date("2026-01-15T05:00:00Z"))).rejects.toThrow(/401/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("throws (without calling fetch) when PIPELINE_TRIGGER_SECRET is unset", async () => {
    delete process.env.PIPELINE_TRIGGER_SECRET;
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(runDailyPipelineSchedule(new Date("2026-01-15T05:00:00Z"))).rejects.toThrow(/PIPELINE_TRIGGER_SECRET/);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
