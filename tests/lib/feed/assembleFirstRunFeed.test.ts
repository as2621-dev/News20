/**
 * Tests for the first-run feed assembly client (Phase 7b SP2).
 *
 * WHY (Rule 9 + the phase's non-fatal contract):
 *   - The request MUST carry the user's OWN Supabase session access token as a
 *     Bearer header (the worker derives user_id from it) and MUST NOT carry the
 *     shared PIPELINE_TRIGGER_SECRET — a regression that dropped the token, or
 *     leaked a secret, would break the JWT-scoped seam the phase chose for security.
 *   - feed_date defaults to today UTC (YYYY-MM-DD) so client and worker agree on the
 *     calendar day; a wrong format would assemble the wrong day's feed.
 *   - Every failure (no session, non-200, malformed body, network) MUST throw so the
 *     onboarding caller can treat it as non-fatal — silently resolving would let the
 *     first-run flag be set on a feed that was never assembled.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase/auth", () => ({
  getCurrentSession: vi.fn(),
}));

import {
  assembleFirstRunFeed,
  firstRunFlagKey,
  markFirstRunFeed,
  todayUtcFeedDate,
} from "@/lib/feed/assembleFirstRunFeed";
import { getCurrentSession } from "@/lib/supabase/auth";

const mockGetCurrentSession = vi.mocked(getCurrentSession);

/** Build a fetch mock returning a given status + JSON body. */
function buildFetchMock(status: number, body: unknown): typeof fetch {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as typeof fetch;
}

beforeEach(() => {
  mockGetCurrentSession.mockReset();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("assembleFirstRunFeed", () => {
  it("POSTs the session access token as a Bearer header and returns allocated_count (happy path)", async () => {
    mockGetCurrentSession.mockResolvedValue({
      access_token: "jwt-abc",
      user: { id: "u1" },
    } as Awaited<ReturnType<typeof getCurrentSession>>);
    const fetchMock = buildFetchMock(200, { allocated_count: 24, feed_total: 30 });

    const result = await assembleFirstRunFeed("2026-06-16", fetchMock);

    expect(result.allocated_count).toBe(24);
    const [, requestInit] = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const headers = requestInit.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer jwt-abc");
    const parsedBody = JSON.parse(requestInit.body as string);
    expect(parsedBody.feed_date).toBe("2026-06-16");
    // WHY: the shared secret must NEVER reach the client request body or headers.
    expect(JSON.stringify(requestInit)).not.toContain("user_id");
  });

  it("defaults feed_date to today UTC (YYYY-MM-DD) when omitted", async () => {
    mockGetCurrentSession.mockResolvedValue({
      access_token: "jwt-abc",
      user: { id: "u1" },
    } as Awaited<ReturnType<typeof getCurrentSession>>);
    const fetchMock = buildFetchMock(200, { allocated_count: 30, feed_total: 30 });

    await assembleFirstRunFeed(undefined, fetchMock);

    const [, requestInit] = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const parsedBody = JSON.parse(requestInit.body as string);
    expect(parsedBody.feed_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(parsedBody.feed_date).toBe(todayUtcFeedDate());
  });

  it("throws and never calls fetch when there is no session (failure case)", async () => {
    mockGetCurrentSession.mockResolvedValue(null);
    const fetchMock = buildFetchMock(200, { allocated_count: 1, feed_total: 30 });

    await expect(assembleFirstRunFeed("2026-06-16", fetchMock)).rejects.toThrow(/No Supabase session/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("throws on a non-200 response (worker error is non-fatal upstream)", async () => {
    mockGetCurrentSession.mockResolvedValue({
      access_token: "jwt-abc",
      user: { id: "u1" },
    } as Awaited<ReturnType<typeof getCurrentSession>>);
    const fetchMock = buildFetchMock(500, {});

    await expect(assembleFirstRunFeed("2026-06-16", fetchMock)).rejects.toThrow(/HTTP 500/);
  });

  it("throws on a malformed body (missing allocated_count)", async () => {
    mockGetCurrentSession.mockResolvedValue({
      access_token: "jwt-abc",
      user: { id: "u1" },
    } as Awaited<ReturnType<typeof getCurrentSession>>);
    const fetchMock = buildFetchMock(200, { feed_total: 30 });

    await expect(assembleFirstRunFeed("2026-06-16", fetchMock)).rejects.toThrow(/Malformed/);
  });

  it("targets the worker base URL from NEXT_PUBLIC_QA_API_BASE_URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_QA_API_BASE_URL", "https://worker.example.com/");
    mockGetCurrentSession.mockResolvedValue({
      access_token: "jwt-abc",
      user: { id: "u1" },
    } as Awaited<ReturnType<typeof getCurrentSession>>);
    const fetchMock = buildFetchMock(200, { allocated_count: 5, feed_total: 30 });

    await assembleFirstRunFeed("2026-06-16", fetchMock);

    const [endpoint] = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(endpoint).toBe("https://worker.example.com/feed/assemble-mine");
  });
});

/**
 * A minimal in-memory localStorage stub (mirrors tests/lib/onboardingProfile.test.ts).
 * The jsdom/node build's native localStorage lacks a usable implementation here, so we
 * stub `globalThis.localStorage` (= `window.localStorage` in jsdom) for deterministic state.
 */
function installLocalStorageStub(): void {
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
}

describe("firstRunFlagKey / markFirstRunFeed", () => {
  beforeEach(() => {
    installLocalStorageStub();
  });

  it("keys the flag by feed date", () => {
    expect(firstRunFlagKey("2026-06-16")).toBe("blip:first-run:2026-06-16");
  });

  it("persists the per-date flag to localStorage", () => {
    markFirstRunFeed("2026-06-16");
    expect(window.localStorage.getItem("blip:first-run:2026-06-16")).toBe("1");
  });
});
