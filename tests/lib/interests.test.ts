import { describe, expect, it, vi } from "vitest";
import { fetchChildInterests, fetchRootInterests, type Interest } from "@/lib/interests";

/**
 * Boundary-mocked tests for the interest taxonomy reads (Phase 1e SP3).
 *
 * WHY (Rule 9): these encode the two invariants the chip lazy-expansion depends
 * on, not just the happy path —
 *   1. `fetchRootInterests` must return ONLY depth-0 roots, filtered by
 *      `parent_interest_id IS NULL` (`.is("parent_interest_id", null)`). A regression
 *      that drops the null-parent filter would leak depth-1/2 rows into the root
 *      chip row; the explicit `.is(...)` assertion fails if that filter is removed.
 *   2. `fetchChildInterests(parentId)` must query by THAT parent
 *      (`.eq("parent_interest_id", parentId)`). The lazy expansion is wrong if it
 *      fetches anything other than the tapped node's direct children — the
 *      `.eq("parent_interest_id", parentId)` assertion fails if the parent filter
 *      is dropped or hard-coded.
 *
 * Mocks at the Supabase client boundary (CLAUDE.md mocking strategy), matching
 * `tests/lib/feed/supabaseFeed.test.ts`.
 */

/**
 * Fake Supabase client whose query chain resolves to `result`. Both reads end in
 * `.order(...).returns(...)`; the chainable filter methods (`is`, `eq`) return the
 * same builder so any order/number of filters composes. Spies on `from`, `is`,
 * `eq`, and `order` let the tests assert the exact filters applied.
 */
function makeFakeClient(result: { data: unknown; error: unknown }) {
  const returns = vi.fn().mockResolvedValue(result);
  const order = vi.fn().mockReturnValue({ returns });
  // `is` / `eq` are chainable and also expose `.order()` (terminal filter) — return
  // a builder carrying all three so chains of any shape resolve.
  const builder: { is: ReturnType<typeof vi.fn>; eq: ReturnType<typeof vi.fn>; order: typeof order } = {
    is: vi.fn(),
    eq: vi.fn(),
    order,
  };
  builder.is.mockReturnValue(builder);
  builder.eq.mockReturnValue(builder);
  const select = vi.fn().mockReturnValue(builder);
  const from = vi.fn().mockReturnValue({ select });
  // Reason: the fake implements only the query chain these reads use; `as never`
  // satisfies the SupabaseClient type at this test boundary without a full stub.
  return { client: { from } as never, from, select, is: builder.is, eq: builder.eq, order, returns };
}

/** A depth-0 root row as PostgREST returns it for the interests select. */
const ROOT_ROW: Interest = {
  interest_id: "11111111-1111-1111-1111-111111111111",
  parent_interest_id: null,
  interest_slug: "sport",
  interest_label: "Sport",
  depth_level: 0,
  interest_segment_slug: "sport",
  interest_search_query: null,
  interest_kind: "taxonomy",
};

/** A depth-1 child row of `sport`. */
const CRICKET_ROW: Interest = {
  interest_id: "22222222-2222-2222-2222-222222222222",
  parent_interest_id: "11111111-1111-1111-1111-111111111111",
  interest_slug: "cricket",
  interest_label: "Cricket",
  depth_level: 1,
  interest_segment_slug: "sport",
  interest_search_query: null,
  interest_kind: "taxonomy",
};

describe("fetchRootInterests", () => {
  it("returns only depth-0 roots and filters on a null parent", async () => {
    const { client, from, is, order } = makeFakeClient({ data: [ROOT_ROW], error: null });

    const roots = await fetchRootInterests(client);

    expect(from).toHaveBeenCalledWith("interests");
    // WHY: dropping this null-parent filter would leak depth-1/2 rows into the
    // root chip row — the core invariant of "depth-0 only".
    expect(is).toHaveBeenCalledWith("parent_interest_id", null);
    expect(order).toHaveBeenCalledWith("interest_sort_order", { ascending: true });
    expect(roots).toHaveLength(1);
    expect(roots[0].depth_level).toBe(0);
    expect(roots[0].parent_interest_id).toBeNull();
    expect(roots[0].interest_slug).toBe("sport");
  });

  it("throws when the query returns an error (no swallowed errors)", async () => {
    const { client } = makeFakeClient({ data: null, error: { message: "permission denied" } });

    await expect(fetchRootInterests(client)).rejects.toThrow(/Failed to load root interests/i);
  });
});

describe("fetchChildInterests", () => {
  it("queries by the given parent id and returns its direct children", async () => {
    const parentInterestId = "11111111-1111-1111-1111-111111111111";
    const { client, from, eq, order } = makeFakeClient({ data: [CRICKET_ROW], error: null });

    const children = await fetchChildInterests(parentInterestId, client);

    expect(from).toHaveBeenCalledWith("interests");
    // WHY: the lazy expansion is wrong if it fetches anything other than the
    // tapped node's direct children — this fails if the parent filter is dropped.
    expect(eq).toHaveBeenCalledWith("parent_interest_id", parentInterestId);
    expect(order).toHaveBeenCalledWith("interest_sort_order", { ascending: true });
    expect(children).toHaveLength(1);
    expect(children[0].parent_interest_id).toBe(parentInterestId);
    expect(children[0].interest_slug).toBe("cricket");
  });

  it("throws when the query returns an error (no swallowed errors)", async () => {
    const { client } = makeFakeClient({ data: null, error: { message: "permission denied" } });

    await expect(fetchChildInterests("some-id", client)).rejects.toThrow(/Failed to load child interests/i);
  });
});
