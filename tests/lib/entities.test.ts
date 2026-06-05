import { describe, expect, it, vi } from "vitest";
import { type EntityResult, listEntities, searchEntities } from "@/lib/entities";

/**
 * Tests for the Phase 5 SP2 entity registry data layer. Mocks the Supabase client
 * at the chain boundary (CLAUDE.md mocking rule; mirrors
 * `tests/lib/feed/supabaseFeed.test.ts`). These encode WHY each behavior matters,
 * not just shape (Rule 9):
 *   1. Show-more pagination MUST be overlap-free and MUST terminate — a duplicated
 *      or skipped chip is a user-visible bug, and a non-null cursor on the last
 *      page would loop Show-more forever.
 *   2. searchEntities('Nvidia') MUST surface the company's ticker — the picker
 *      renders NVDA in the rust accent; a dropped ticker mapping is a visible miss.
 *   3. A no-match query MUST return [] (not throw, not null) — the caller relies on
 *      [] to store the typed value as a first-class free-text follow (spec §6).
 */

/** A raw `entities` row as PostgREST / the search_entities RPC returns it. */
interface FakeEntityRow {
  entity_id: string;
  entity_label: string;
  entity_ticker: string | null;
  entity_kind: string;
}

/**
 * Fake Supabase client for the `listEntities` keyset chain:
 * `.from().select().eq().order().limit()[.eq()][.gt()].returns()`.
 *
 * The terminal `.returns()` resolves to `result`; every intermediate builder
 * method returns the same thenable proxy so optional `.eq(kind)` / `.gt(cursor)`
 * links can be chained in any order the SUT calls them. We capture the calls to
 * assert the keyset seek (`.gt`) and ordering are wired correctly.
 */
function makeListClient(result: { data: unknown; error: unknown }) {
  const calls = {
    eq: [] as Array<[string, unknown]>,
    gt: [] as Array<[string, unknown]>,
    order: [] as Array<[string, unknown]>,
    limit: [] as number[],
  };
  const returns = vi.fn().mockResolvedValue(result);
  const builder: Record<string, unknown> = { returns };
  builder.eq = vi.fn((column: string, value: unknown) => {
    calls.eq.push([column, value]);
    return builder;
  });
  builder.gt = vi.fn((column: string, value: unknown) => {
    calls.gt.push([column, value]);
    return builder;
  });
  builder.order = vi.fn((column: string, opts: unknown) => {
    calls.order.push([column, opts]);
    return builder;
  });
  builder.limit = vi.fn((value: number) => {
    calls.limit.push(value);
    return builder;
  });
  const select = vi.fn().mockReturnValue(builder);
  const from = vi.fn().mockReturnValue({ select });
  return { client: { from } as never, from, select, calls };
}

/**
 * Fake Supabase client for the `searchEntities` RPC call: `await client.rpc(
 * 'search_entities', args)` resolves directly to `{ data, error }` (the SUT casts
 * `data` to the row type at the boundary — supabase-js can't infer an untyped RPC).
 */
function makeRpcClient(result: { data: unknown; error: unknown }) {
  const rpc = vi.fn().mockResolvedValue(result);
  return { client: { rpc } as never, rpc };
}

/** Build N seeded company rows under one parent, ids ordered for keyset paging. */
function makeRows(parent: string, startIndex: number, count: number): FakeEntityRow[] {
  return Array.from({ length: count }, (_unused, offset) => {
    const index = startIndex + offset;
    return {
      entity_id: `${parent}/c-${String(index).padStart(3, "0")}`,
      entity_label: `Company ${index}`,
      entity_ticker: `T${index}`,
      entity_kind: "company",
    };
  });
}

const EARNINGS_PARENT = "business/corporate-news/what-to-track/earnings";

describe("listEntities (keyset pagination)", () => {
  it("filters by parent, orders by entity_id, and returns a full-page nextCursor", async () => {
    const page = makeRows(EARNINGS_PARENT, 0, 20);
    const { client, from, calls } = makeListClient({ data: page, error: null });

    const result = await listEntities({ parent: EARNINGS_PARENT, kind: "company", limit: 20 }, client);

    expect(from).toHaveBeenCalledWith("entities");
    // Scope + order are the contract the idx_entities_parent_kind index serves.
    expect(calls.eq).toContainEqual(["entity_parent_slug", EARNINGS_PARENT]);
    expect(calls.eq).toContainEqual(["entity_kind", "company"]);
    expect(calls.order).toContainEqual(["entity_id", { ascending: true }]);
    expect(calls.limit).toContain(20);
    // First page is full (20 == limit) → cursor is the LAST row's id, so Show-more
    // can fetch the next page.
    expect(result.results).toHaveLength(20);
    expect(result.nextCursor).toBe(page[19].entity_id);
  });

  it("uses the cursor to fetch a non-overlapping next page, then terminates on a partial page", async () => {
    // WHY (Rule 9): Show-more must never duplicate a chip (overlap) nor loop forever
    // (non-null cursor on the last page). We drive two real pages and assert the
    // second page's ids are DISJOINT from the first and that the short final page
    // ends paging with nextCursor === null.
    const firstPage = makeRows(EARNINGS_PARENT, 0, 20);
    const firstClient = makeListClient({ data: firstPage, error: null });
    const page1 = await listEntities({ parent: EARNINGS_PARENT, limit: 20 }, firstClient.client);

    expect(page1.nextCursor).toBe(firstPage[19].entity_id);

    // Second call: pass the cursor; the SUT must seek strictly past it via .gt.
    const secondPage = makeRows(EARNINGS_PARENT, 20, 8); // partial: 8 < 20
    const secondClient = makeListClient({ data: secondPage, error: null });
    const page2 = await listEntities(
      { parent: EARNINGS_PARENT, cursor: page1.nextCursor ?? undefined, limit: 20 },
      secondClient.client,
    );

    // The keyset seek is wired: strictly-greater-than the previous page's last id.
    expect(secondClient.calls.gt).toContainEqual(["entity_id", page1.nextCursor]);

    // No overlap: every page-2 id is absent from page-1 (the core anti-duplication
    // guarantee Show-more depends on).
    const page1Ids = new Set(page1.results.map((entity: EntityResult) => entity.id));
    const overlap = page2.results.filter((entity: EntityResult) => page1Ids.has(entity.id));
    expect(overlap).toEqual([]);

    // Partial final page (8 < limit 20) → paging terminates. A non-null cursor here
    // would loop Show-more endlessly.
    expect(page2.results).toHaveLength(8);
    expect(page2.nextCursor).toBeNull();
  });

  it("returns an empty page with a null cursor when a parent has no children", async () => {
    const { client } = makeListClient({ data: [], error: null });

    const result = await listEntities({ parent: "empty/parent", limit: 20 }, client);

    expect(result.results).toEqual([]);
    expect(result.nextCursor).toBeNull();
  });

  it("does NOT apply the kind filter or a cursor seek when neither is provided", async () => {
    const { client, calls } = makeListClient({ data: makeRows(EARNINGS_PARENT, 0, 3), error: null });

    await listEntities({ parent: EARNINGS_PARENT }, client);

    // Only the parent eq is applied — no kind eq, no gt seek (page 1, all kinds).
    expect(calls.eq).toEqual([["entity_parent_slug", EARNINGS_PARENT]]);
    expect(calls.gt).toEqual([]);
  });

  it("throws when the query returns an error (surface, never swallow — Rule 12)", async () => {
    const { client } = makeListClient({ data: null, error: { message: "permission denied" } });

    await expect(listEntities({ parent: EARNINGS_PARENT }, client)).rejects.toThrow(/Failed to list entities/i);
  });
});

describe("searchEntities (registry fuzzy search RPC)", () => {
  it("resolves 'Nvidia' to the company carrying ticker NVDA", async () => {
    // WHY (Rule 9): the picker renders the ticker in the rust accent and stores the
    // resolved entity id. If the entity_ticker → ticker mapping drops, the Add-your-
    // own hit loses its NVDA badge. The RPC is mocked to return the Nvidia row.
    const nvidiaRow: FakeEntityRow = {
      entity_id: "business/corporate-news/what-to-track/earnings/companies-to-track/nvidia",
      entity_label: "Nvidia",
      entity_ticker: "NVDA",
      entity_kind: "company",
    };
    const { client, rpc } = makeRpcClient({ data: [nvidiaRow], error: null });

    const matches = await searchEntities({ q: "Nvidia" }, client);

    // The RPC is called by name with the trimmed query + null optional filters.
    expect(rpc).toHaveBeenCalledWith("search_entities", { q: "Nvidia", k: null, p: null, lim: 20 });
    expect(matches).toHaveLength(1);
    expect(matches[0].label).toBe("Nvidia");
    expect(matches[0].ticker).toBe("NVDA");
    expect(matches[0].kind).toBe("company");
  });

  it("passes through kind and parent filters to the RPC when given", async () => {
    const { client, rpc } = makeRpcClient({ data: [], error: null });

    await searchEntities({ q: "chiefs", kind: "team", parent: "sport/american-football/nfl", limit: 5 }, client);

    expect(rpc).toHaveBeenCalledWith("search_entities", {
      q: "chiefs",
      k: "team",
      p: "sport/american-football/nfl",
      lim: 5,
    });
  });

  it("returns [] for a no-match query so the caller can store free text (spec §6)", async () => {
    // WHY (Rule 9): a miss is a FIRST-CLASS outcome — Add-your-own falls back to a
    // free-text follow. [] (not throw, not null) is the contract the caller branches on.
    const { client } = makeRpcClient({ data: [], error: null });

    const matches = await searchEntities({ q: "zzzz not a real entity" }, client);

    expect(matches).toEqual([]);
  });

  it("short-circuits an empty/whitespace query to [] without calling the RPC", async () => {
    const { client, rpc } = makeRpcClient({ data: null, error: null });

    const matches = await searchEntities({ q: "   " }, client);

    expect(matches).toEqual([]);
    expect(rpc).not.toHaveBeenCalled();
  });

  it("throws when the RPC returns an error (surface, never swallow — Rule 12)", async () => {
    const { client } = makeRpcClient({ data: null, error: { message: "function does not exist" } });

    await expect(searchEntities({ q: "Nvidia" }, client)).rejects.toThrow(/Failed to search entities/i);
  });
});
