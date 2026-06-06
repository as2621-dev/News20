import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Phase 5c SP3a — the source-search client (worker fetch + follow annotation).
 *
 * WHY these tests exist (Rule 9 — encode the product contract, not the call shape):
 *  - A search hit the user ALREADY follows must be annotated `is_already_added:
 *    true` (matched by `external_id`), and a new one `false`. A wrong flag shows
 *    "Add" on a followed source (duplicate-follow bug) or "Added" on a new one
 *    (the user can't add it). We assert both against a mocked follow set.
 *  - An ANON search (no session) must degrade to all-false WITHOUT throwing —
 *    onboarding searches the catalog before/around sign-in.
 *  - A worker failure (non-200, `search_ok:false`, malformed body, transport
 *    error) must surface as `search_ok:false` with EMPTY results — NOT an empty
 *    list masquerading as "no matches" (Rule 12). The modal needs to tell
 *    "unavailable" from "no matches".
 *  - An X handle result must carry `is_pending` straight through (the modal shows
 *    a "pending" affordance for free-text X follows).
 *
 * Mocks the global fetch (the worker call) + the Supabase client's auth.getUser()
 * and the follow-join read at the boundary — no real worker, no real Supabase.
 */

import type { SearchableSourceType, WorkerSourceSearchResult } from "@/lib/sourceSearch";
import { searchSources } from "@/lib/sourceSearch";

/** Build one worker result row (the pre-annotation shape). */
function makeWorkerResult(overrides: Partial<WorkerSourceSearchResult> = {}): WorkerSourceSearchResult {
  return {
    source_name: "Lex Fridman",
    external_id: "UC_lex",
    content_source_type: "youtube_channel",
    thumbnail_url: "https://yt/lex.jpg",
    description: "Conversations about AI.",
    subscriber_count: 4_200_000,
    is_pending: false,
    ...overrides,
  };
}

/** A fetch mock returning a 200 with the given JSON body. */
function fetchOk(body: unknown): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: vi.fn().mockResolvedValue(body),
  }) as unknown as typeof fetch;
}

/**
 * A fake Supabase client exposing auth.getUser() + the chained follow-join read.
 * `followedExternalIds` are the external_ids the (authed) user already follows on
 * the searched axis; pass `user: null` to simulate an anon (signed-out) search.
 */
function makeClient(opts: { user: { id: string } | null; followedExternalIds?: string[] }) {
  const rows = (opts.followedExternalIds ?? []).map((external_id) => ({
    content_sources: { external_id, content_source_type: "youtube_channel" },
  }));
  // The read chain: from().select().eq().eq() → resolves to { data, error }.
  const secondEq = vi.fn().mockResolvedValue({ data: rows, error: null });
  const firstEq = vi.fn().mockReturnValue({ eq: secondEq });
  const select = vi.fn().mockReturnValue({ eq: firstEq });
  const from = vi.fn().mockReturnValue({ select });
  return {
    auth: { getUser: vi.fn().mockResolvedValue({ data: { user: opts.user }, error: null }) },
    from,
  } as never;
}

const KIND: SearchableSourceType = "youtube_channel";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("searchSources — follow annotation (the DoD)", () => {
  it("annotates is_already_added TRUE for followed external_ids and FALSE otherwise", async () => {
    // WHY: the badge correctness is the whole point — a followed source shows
    // "Added", a new one shows "Add". We match by external_id (platform id).
    const fetchImpl = fetchOk({
      search_ok: true,
      results: [
        makeWorkerResult({ external_id: "UC_followed", source_name: "Followed Ch" }),
        makeWorkerResult({ external_id: "UC_new", source_name: "New Ch" }),
      ],
    });
    const client = makeClient({ user: { id: "u1" }, followedExternalIds: ["UC_followed"] });

    const { results, search_ok } = await searchSources({ query: "ch", kind: KIND, client, fetchImpl });

    expect(search_ok).toBe(true);
    expect(results).toHaveLength(2);
    expect(results.find((r) => r.external_id === "UC_followed")?.is_already_added).toBe(true);
    expect(results.find((r) => r.external_id === "UC_new")?.is_already_added).toBe(false);
  });

  it("degrades to all-false WITHOUT throwing on an anon (signed-out) search", async () => {
    // WHY: onboarding searches the catalog before sign-in; an anon search must
    // never crash — is_already_added is simply false for every row.
    const fetchImpl = fetchOk({ search_ok: true, results: [makeWorkerResult({ external_id: "UC_a" })] });
    const client = makeClient({ user: null });

    const { results } = await searchSources({ query: "a", kind: KIND, client, fetchImpl });

    expect(results[0].is_already_added).toBe(false);
    // The follow-join read must NOT have run for an anon user.
    expect((client as unknown as { from: ReturnType<typeof vi.fn> }).from).not.toHaveBeenCalled();
  });
});

describe("searchSources — worker failure surfaces as unavailable (Rule 12)", () => {
  it("returns search_ok:false + empty on a non-200 response", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false, status: 502, json: vi.fn() }) as unknown as typeof fetch;
    const client = makeClient({ user: { id: "u1" } });

    const outcome = await searchSources({ query: "x", kind: KIND, client, fetchImpl });

    expect(outcome.search_ok).toBe(false);
    expect(outcome.results).toEqual([]);
  });

  it("returns search_ok:false when the worker reports search_ok:false (missing key / upstream)", async () => {
    const fetchImpl = fetchOk({ search_ok: false, results: [] });
    const client = makeClient({ user: { id: "u1" } });

    const outcome = await searchSources({ query: "x", kind: KIND, client, fetchImpl });

    expect(outcome.search_ok).toBe(false);
    expect(outcome.results).toEqual([]);
  });

  it("returns search_ok:false + empty on a transport error (worker unreachable)", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("network down")) as unknown as typeof fetch;
    const client = makeClient({ user: { id: "u1" } });

    const outcome = await searchSources({ query: "x", kind: KIND, client, fetchImpl });

    expect(outcome.search_ok).toBe(false);
    expect(outcome.results).toEqual([]);
  });

  it("drops malformed result rows but keeps well-formed ones", async () => {
    const fetchImpl = fetchOk({
      search_ok: true,
      results: [
        makeWorkerResult({ external_id: "UC_ok" }),
        { external_id: "UC_bad" }, // missing source_name → dropped
        { source_name: "Wrong axis", external_id: "p1", content_source_type: "podcast" }, // kind mismatch → dropped
      ],
    });
    const client = makeClient({ user: { id: "u1" } });

    const { results } = await searchSources({ query: "x", kind: KIND, client, fetchImpl });

    expect(results).toHaveLength(1);
    expect(results[0].external_id).toBe("UC_ok");
  });
});

describe("searchSources — X pending passthrough", () => {
  it("passes is_pending through for an X handle result", async () => {
    const fetchImpl = fetchOk({
      search_ok: true,
      results: [
        {
          source_name: "Reuters",
          external_id: "reuters",
          content_source_type: "x_account",
          thumbnail_url: null,
          description: "@Reuters",
          subscriber_count: null,
          is_pending: true,
        },
      ],
    });
    const client = makeClient({ user: null }); // anon search

    const { results } = await searchSources({ query: "@Reuters", kind: "x_account", client, fetchImpl });

    expect(results[0].is_pending).toBe(true);
    expect(results[0].is_already_added).toBe(false);
  });
});

describe("searchSources — a real follow-read failure is surfaced (not swallowed)", () => {
  it("throws when the authed follow read errors (a silent miss would mis-badge)", async () => {
    // WHY: swallowing a real DB error would show "Add" on an already-followed
    // source (a duplicate-follow bug) — that failure must be loud (Rule 12).
    const fetchImpl = fetchOk({ search_ok: true, results: [makeWorkerResult()] });
    const secondEq = vi.fn().mockResolvedValue({ data: null, error: { message: "permission denied" } });
    const firstEq = vi.fn().mockReturnValue({ eq: secondEq });
    const select = vi.fn().mockReturnValue({ eq: firstEq });
    const client = {
      auth: { getUser: vi.fn().mockResolvedValue({ data: { user: { id: "u1" } }, error: null }) },
      from: vi.fn().mockReturnValue({ select }),
    } as never;

    await expect(searchSources({ query: "x", kind: KIND, client, fetchImpl })).rejects.toThrow(/permission denied/);
  });
});
