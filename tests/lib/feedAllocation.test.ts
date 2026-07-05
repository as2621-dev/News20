import type { PostgrestError } from "@supabase/supabase-js";
import { describe, expect, it, vi } from "vitest";
import { getUserFeedAllocation, saveUserFeedAllocation } from "@/lib/feedAllocation";
import type { AllocationSegment } from "@/lib/feedBuckets";

/**
 * Blip Flow Stage 3 — the feed-allocation data layer at the Supabase client boundary.
 *
 * WHY these tests exist (Rule 9 — encode the business contract, not call shapes):
 *  - SAVE must upsert one owner-scoped row per bucket with the MAPPED enum value and the
 *    list index as allocation_sort_order. The sort order IS the user's manual sequence —
 *    if it's dropped or off-by-one, the feed plays in the wrong order (the whole point of
 *    "Build your 30, IN ORDER"). We assert the exact rows + onConflict key.
 *  - SAVE must DELETE rows for removed buckets so the table reflects EXACTLY the saved set
 *    — a stale row would keep allocating slots to a bucket the user deleted (a ghost block).
 *  - Under the SP3 taxonomy every bucket has a real `feed_category` enum value, so a clean
 *    save persists every bucket, defers NOTHING, and never silently drops one.
 *  - An upsert error MUST surface (throw) — never swallowed (Rule 12).
 *  - READ must be owner-scoped, ordered by sort order, and mapped back to design buckets.
 *
 * Mocks the Supabase client at the boundary (CLAUDE.md mocking strategy), mirroring
 * tests/lib/sources.test.ts.
 */

const AUTHED_USER_ID = "user-uuid-1";

/**
 * Fake client for the owner-scoped user_feed_allocation read + save chains.
 *
 * READ:   `.from().select().eq().order().returns()`
 * UPSERT: `.from().upsert(rows, opts)` → { error }
 * DELETE: `.from().delete().eq().is().is().not()`  (`.not()` omitted when the saved set is empty)
 *
 * Captures every upsert (payload + onConflict), the delete's `.is()` predicates (the coarse
 * scoping — slice #12) and its `.not()` args so a test can assert exactly what was written/
 * pruned, owner-scoped. The delete chain is a single fluent, thenable builder: `.eq`/`.is`/`.not`
 * each return the same builder, and awaiting it resolves. `upsertErrors` is a QUEUE so a
 * multi-upsert sequence (e.g. an error then a retry) can be modelled if ever needed.
 */
function makeAllocationClient(options: {
  user: { id: string } | null;
  readResult?: { data: unknown; error: unknown };
  upsertErrors?: Array<PostgrestError | null>;
  deleteError?: unknown;
}) {
  const getUser = vi.fn().mockResolvedValue({ data: { user: options.user }, error: null });

  const upsertCalls: Array<{ rows: Array<Record<string, unknown>>; onConflict: string | undefined }> = [];
  const upsertErrorQueue = [...(options.upsertErrors ?? [])];
  const upsert = vi.fn((rows: Array<Record<string, unknown>>, opts: { onConflict?: string }) => {
    upsertCalls.push({ rows, onConflict: opts?.onConflict });
    const nextError = upsertErrorQueue.length > 0 ? upsertErrorQueue.shift() : null;
    return Promise.resolve({ error: nextError ?? null });
  });

  // READ chain: select().eq().order().returns()
  const selectEqCalls: Array<[string, unknown]> = [];
  const returns = vi.fn().mockResolvedValue(options.readResult ?? { data: [], error: null });
  const order = vi.fn().mockReturnValue({ returns });
  const select = vi.fn().mockReturnValue({
    eq: vi.fn((column: string, value: unknown) => {
      selectEqCalls.push([column, value]);
      return { order };
    }),
  });

  // DELETE chain: a single fluent, thenable builder. `.eq`/`.is`/`.not` each record their args
  // and return the SAME builder; awaiting it (directly or after `.not`) resolves the delete.
  const deleteEqCalls: Array<[string, unknown]> = [];
  const deleteIsCalls: Array<[string, unknown]> = [];
  const notCalls: Array<[string, string, string]> = [];
  const resolveDelete = () => Promise.resolve({ error: options.deleteError ?? null });
  const del = vi.fn(() => {
    const builder = {
      eq: vi.fn((column: string, value: unknown) => {
        deleteEqCalls.push([column, value]);
        return builder;
      }),
      is: vi.fn((column: string, value: unknown) => {
        deleteIsCalls.push([column, value]);
        return builder;
      }),
      not: vi.fn((notColumn: string, operator: string, listValue: string) => {
        notCalls.push([notColumn, operator, listValue]);
        return builder;
      }),
      // biome-ignore lint/suspicious/noThenProperty: intentional thenable — mirrors the PostgREST query builder so `await deleteQuery` resolves.
      then: (onFulfilled: (value: { error: unknown }) => unknown) => resolveDelete().then(onFulfilled),
    };
    return builder;
  });

  const from = vi.fn().mockReturnValue({ select, upsert, delete: del });
  const client = { auth: { getUser }, from } as never;
  return { client, from, getUser, upsert, del, upsertCalls, selectEqCalls, deleteEqCalls, deleteIsCalls, notCalls };
}

const HAPPY_SEGMENTS: AllocationSegment[] = [
  { bucketId: "sport", count: 2 },
  { bucketId: "geopolitics", count: 4 },
  { bucketId: "tech", count: 24 },
];

describe("saveUserFeedAllocation (owner-scoped upsert + stale-prune)", () => {
  it("upserts one mapped row per bucket with the list index as allocation_sort_order", async () => {
    // WHY: sort order = the user's manual sequence ("in order"). An off-by-one or dropped
    // index plays the briefing in the wrong order. We pin the exact mapped rows + conflict key.
    const { client, upsert, upsertCalls } = makeAllocationClient({ user: { id: AUTHED_USER_ID } });

    const result = await saveUserFeedAllocation(HAPPY_SEGMENTS, client);

    expect(upsert).toHaveBeenCalledTimes(1);
    expect(upsertCalls[0].onConflict).toBe("follow_user_id,allocation_category,allocation_interest_id");
    expect(upsertCalls[0].rows).toEqual([
      {
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "sport",
        allocation_slot_count: 2,
        allocation_sort_order: 0,
      },
      {
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "geopolitics",
        allocation_slot_count: 4,
        allocation_sort_order: 1,
      },
      {
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "tech",
        allocation_slot_count: 24,
        allocation_sort_order: 2,
      },
    ]);
    expect(result.persisted_count).toBe(3);
    expect(result.deferred_buckets).toEqual([]);
  });

  it("prunes rows for buckets NOT in the save (removed blocks), scoped to the authed user", async () => {
    // WHY: the table must reflect EXACTLY the saved set; a stale row keeps a deleted bucket
    // claiming feed slots. The delete is owner-scoped and excludes the saved enum values.
    const { client, deleteEqCalls, notCalls } = makeAllocationClient({ user: { id: AUTHED_USER_ID } });

    await saveUserFeedAllocation(HAPPY_SEGMENTS, client);

    expect(deleteEqCalls).toContainEqual(["follow_user_id", AUTHED_USER_ID]);
    expect(notCalls).toHaveLength(1);
    const [notColumn, operator, listValue] = notCalls[0];
    expect(notColumn).toBe("allocation_category");
    expect(operator).toBe("in");
    // The saved enum values are excluded from the delete (so they survive; everything else is pruned).
    expect(listValue).toBe("(sport,geopolitics,tech)");
  });

  it("scopes the stale-prune to COARSE rows only, so niche/beyond-bubble sections survive (slice #12)", async () => {
    // WHY (Rule 9): the whole anti-corruption guarantee. The prune must be pinned to
    // `allocation_interest_id IS NULL AND allocation_section_label IS NULL` — otherwise a coarse
    // save deletes any SECTION row (niche or "Beyond your bubble") whose category falls outside
    // the coarse saved set, silently destroying the backend's niche allocation. If either `.is()`
    // predicate is dropped, this test FAILS and the corruption is loud.
    const { client, deleteIsCalls } = makeAllocationClient({ user: { id: AUTHED_USER_ID } });

    await saveUserFeedAllocation(HAPPY_SEGMENTS, client);

    expect(deleteIsCalls).toContainEqual(["allocation_interest_id", null]);
    expect(deleteIsCalls).toContainEqual(["allocation_section_label", null]);
  });

  it("ignores read-only SECTION segments in the write set (never flattens a niche to coarse)", async () => {
    // WHY (Rule 9): coarse-only editing (founder 2026-07-05). If a niche/beyond-bubble SECTION
    // segment reaches the save, it must NOT be upserted as a coarse row (which would drop its
    // niche columns and flatten the interview's allocation). Only the coarse block is written.
    const mixed: AllocationSegment[] = [
      { bucketId: "sport", count: 4, interestId: "int-ipl", sectionLabel: "IPL" }, // read-only section
      { bucketId: "sport", count: 1, sectionLabel: "Beyond your bubble" }, // beyond-bubble reserve
      { bucketId: "ai", count: 25 }, // coarse — the only writable block
    ];
    const { client, upsert, upsertCalls } = makeAllocationClient({ user: { id: AUTHED_USER_ID } });

    const result = await saveUserFeedAllocation(mixed, client);

    expect(upsert).toHaveBeenCalledTimes(1);
    expect(upsertCalls[0].rows).toEqual([
      {
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "ai",
        allocation_slot_count: 25,
        allocation_sort_order: 0,
      },
    ]);
    expect(result.persisted_count).toBe(1);
  });

  it("persists every bucket with no deferral under the SP3 taxonomy (no degrade case)", async () => {
    // WHY: every SP3 design bucket has a real feed_category enum value (the pre-0010
    // `podcasts`-missing degrade path is retired). A clean multi-bucket save must therefore
    // upsert ONCE, defer NOTHING, and drop no bucket — if a bucket were ever silently
    // dropped the user's "Build your 30" would persist short.
    const segments: AllocationSegment[] = [
      { bucketId: "geopolitics", count: 10 },
      { bucketId: "arts", count: 20 },
    ];
    const { client, upsert, upsertCalls } = makeAllocationClient({ user: { id: AUTHED_USER_ID } });

    const result = await saveUserFeedAllocation(segments, client);

    expect(upsert).toHaveBeenCalledTimes(1); // single upsert, no degrade retry
    const persistedCategories = upsertCalls[0].rows.map((row) => row.allocation_category);
    expect(persistedCategories).toEqual(["geopolitics", "arts"]);
    expect(result.deferred_buckets).toEqual([]);
    expect(result.persisted_count).toBe(2);
  });

  it("throws on a NON-podcasts upsert error (surface, never swallow — Rule 12)", async () => {
    // WHY: a real permission/constraint failure must surface so the UI can retry — only the
    // specific podcasts-missing case degrades. A generic error here must throw.
    const genericError = {
      message: "permission denied for table user_feed_allocation",
      code: "42501",
      details: "",
      hint: "",
      name: "PostgrestError",
    } as PostgrestError;
    const { client } = makeAllocationClient({ user: { id: AUTHED_USER_ID }, upsertErrors: [genericError] });

    await expect(saveUserFeedAllocation(HAPPY_SEGMENTS, client)).rejects.toThrow(/Failed to persist feed allocation/i);
  });

  it("throws when signed out — never writes an anon allocation (Rule 12)", async () => {
    const { client, upsert } = makeAllocationClient({ user: null });

    await expect(saveUserFeedAllocation(HAPPY_SEGMENTS, client)).rejects.toThrow(/signed out/i);
    expect(upsert).not.toHaveBeenCalled();
  });

  it("logs a warning but still persists when the total is NOT 30 (UI invariant drift)", async () => {
    // WHY: the screen enforces 30, but the helper must never silently persist a drifted total
    // (Rule 12) — it warns loudly yet still saves rather than crashing the flow.
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const nonThirty: AllocationSegment[] = [{ bucketId: "geopolitics", count: 5 }];
    const { client, upsert } = makeAllocationClient({ user: { id: AUTHED_USER_ID } });

    const result = await saveUserFeedAllocation(nonThirty, client);

    expect(upsert).toHaveBeenCalledTimes(1);
    expect(result.persisted_count).toBe(1);
    const warned = warnSpy.mock.calls.some((call) => String(call[0]).includes("feed_allocation_total_not_30"));
    expect(warned).toBe(true);
    warnSpy.mockRestore();
  });
});

describe("getUserFeedAllocation (RLS owner-scoped read, mapped back to design buckets)", () => {
  it("reads the caller's rows ordered by sort order and maps enum values back to design ids", async () => {
    // WHY: hydrating a returning user's screen must rebuild their EXACT saved order with the
    // correct buckets. A wrong inverse map shows the wrong blocks; a dropped order scrambles them.
    const rows = [
      { allocation_category: "geopolitics", allocation_slot_count: 4, allocation_sort_order: 0 },
      { allocation_category: "tech", allocation_slot_count: 26, allocation_sort_order: 1 },
    ];
    const { client, from, selectEqCalls } = makeAllocationClient({
      user: { id: AUTHED_USER_ID },
      readResult: { data: rows, error: null },
    });

    const result = await getUserFeedAllocation(client);

    expect(from).toHaveBeenCalledWith("user_feed_allocation");
    expect(selectEqCalls).toContainEqual(["follow_user_id", AUTHED_USER_ID]);
    expect(result).toEqual([
      { bucketId: "geopolitics", count: 4 },
      { bucketId: "tech", count: 26 },
    ]);
  });

  it("returns [] when the user has no saved allocation (edge case — fresh user)", async () => {
    const { client } = makeAllocationClient({ user: { id: AUTHED_USER_ID }, readResult: { data: [], error: null } });

    expect(await getUserFeedAllocation(client)).toEqual([]);
  });

  it("throws when the read errors (surface, never swallow — Rule 12)", async () => {
    const { client } = makeAllocationClient({
      user: { id: AUTHED_USER_ID },
      readResult: { data: null, error: { message: "permission denied" } },
    });

    await expect(getUserFeedAllocation(client)).rejects.toThrow(/Failed to read user feed allocation/i);
  });

  it("throws when signed out (no anon read of a per-user table)", async () => {
    const { client, from } = makeAllocationClient({ user: null });

    await expect(getUserFeedAllocation(client)).rejects.toThrow(/signed out/i);
    expect(from).not.toHaveBeenCalled();
  });

  it("projects the migration-0026 niche columns so a section row hydrates as a read-only named block", async () => {
    // WHY (Rule 9): slice #12 loader-projection fix. Without projecting allocation_interest_id +
    // allocation_section_label, a niche row collapses into an anonymous coarse `sport` block and
    // the screen can't render "IPL — 4". A coarse row must still hydrate as bare `{ bucketId, count }`.
    const rows = [
      {
        allocation_category: "sport",
        allocation_slot_count: 4,
        allocation_sort_order: 0,
        allocation_interest_id: "int-ipl",
        allocation_section_label: "IPL",
      },
      {
        allocation_category: "geopolitics",
        allocation_slot_count: 1,
        allocation_sort_order: 1,
        allocation_interest_id: null,
        allocation_section_label: "Beyond your bubble",
      },
      {
        allocation_category: "ai",
        allocation_slot_count: 5,
        allocation_sort_order: 2,
        allocation_interest_id: null,
        allocation_section_label: null,
      },
    ];
    const { client } = makeAllocationClient({ user: { id: AUTHED_USER_ID }, readResult: { data: rows, error: null } });

    const result = await getUserFeedAllocation(client);

    expect(result).toEqual([
      { bucketId: "sport", count: 4, interestId: "int-ipl", sectionLabel: "IPL" },
      { bucketId: "geopolitics", count: 1, interestId: null, sectionLabel: "Beyond your bubble" },
      { bucketId: "ai", count: 5 }, // coarse row stays exactly { bucketId, count }
    ]);
  });
});

/**
 * Integration (NO mock of the write logic — a real in-memory `user_feed_allocation` table) proving
 * the slice #12 anti-corruption guarantee end-to-end: a coarse "Build your 30" save round-trips
 * through the REAL save→delete→reload chain and the backend's niche/beyond-bubble SECTION rows
 * survive untouched, while the coarse blocks are replaced by exactly the saved set.
 *
 * The fake table implements the two behaviours the corruption seam depends on:
 *  - UPSERT arbiter (follow_user_id, allocation_category, allocation_interest_id), NULLS NOT
 *    DISTINCT (migration 0026) — a coarse upsert (interest NULL) collides only with the coarse
 *    row for that category, never with a niche row (interest set).
 *  - DELETE honouring `.eq`/`.is`/`.not.in` predicates — so the coarse-scoped prune the code
 *    issues is actually applied against stored rows (not just asserted as call args).
 */
interface StoredAllocationRow {
  allocation_id: string;
  follow_user_id: string;
  allocation_category: string;
  allocation_interest_id: string | null;
  allocation_section_label: string | null;
  allocation_slot_count: number;
  allocation_sort_order: number;
}

/** A minimal in-memory `user_feed_allocation` supporting the exact read/upsert/delete chains used. */
function makeInMemoryAllocationDb(initialRows: StoredAllocationRow[], user: { id: string }) {
  let rows: StoredAllocationRow[] = initialRows.map((row) => ({ ...row }));
  let nextId = initialRows.length + 1;
  const getUser = vi.fn().mockResolvedValue({ data: { user }, error: null });

  const from = vi.fn(() => ({
    // READ: select().eq(col,val).order(col).returns()
    select: vi.fn(() => {
      const eqFilters: Array<[string, unknown]> = [];
      const builder = {
        eq: (column: string, value: unknown) => {
          eqFilters.push([column, value]);
          return builder;
        },
        order: (column: keyof StoredAllocationRow, opts: { ascending: boolean }) => ({
          returns: () => {
            const filtered = rows
              .filter((row) =>
                eqFilters.every(([col, val]) => (row as unknown as Record<string, unknown>)[col] === val),
              )
              .sort((a, b) => (opts.ascending ? 1 : -1) * (Number(a[column]) - Number(b[column])));
            return Promise.resolve({ data: filtered.map((row) => ({ ...row })), error: null });
          },
        }),
      };
      return builder;
    }),

    // UPSERT: apply the 3-col NULLS NOT DISTINCT arbiter (interest omitted → NULL).
    upsert: vi.fn((newRows: Array<Record<string, unknown>>) => {
      for (const incoming of newRows) {
        const category = incoming.allocation_category as string;
        const interestId = (incoming.allocation_interest_id as string | null | undefined) ?? null;
        const existing = rows.find(
          (row) =>
            row.follow_user_id === incoming.follow_user_id &&
            row.allocation_category === category &&
            row.allocation_interest_id === interestId,
        );
        if (existing) {
          existing.allocation_slot_count = incoming.allocation_slot_count as number;
          existing.allocation_sort_order = incoming.allocation_sort_order as number;
        } else {
          rows.push({
            allocation_id: `gen-${nextId++}`,
            follow_user_id: incoming.follow_user_id as string,
            allocation_category: category,
            allocation_interest_id: interestId,
            allocation_section_label: (incoming.allocation_section_label as string | null | undefined) ?? null,
            allocation_slot_count: incoming.allocation_slot_count as number,
            allocation_sort_order: incoming.allocation_sort_order as number,
          });
        }
      }
      return Promise.resolve({ error: null });
    }),

    // DELETE: fluent thenable applying .eq / .is / .not.in predicates against stored rows.
    delete: vi.fn(() => {
      const predicates: Array<(row: StoredAllocationRow) => boolean> = [];
      const builder = {
        eq: (column: string, value: unknown) => {
          predicates.push((row) => (row as unknown as Record<string, unknown>)[column] === value);
          return builder;
        },
        is: (column: string, value: unknown) => {
          predicates.push((row) => ((row as unknown as Record<string, unknown>)[column] ?? null) === value);
          return builder;
        },
        not: (column: string, _operator: string, listValue: string) => {
          const list = listValue.slice(1, -1).split(",");
          predicates.push((row) => !list.includes(String((row as unknown as Record<string, unknown>)[column])));
          return builder;
        },
        // biome-ignore lint/suspicious/noThenProperty: intentional thenable — the delete applies its accumulated predicates when awaited.
        then: (onFulfilled: (value: { error: unknown }) => unknown) => {
          rows = rows.filter((row) => !predicates.every((predicate) => predicate(row)));
          return Promise.resolve({ error: null }).then(onFulfilled);
        },
      };
      return builder;
    }),
  }));

  const client = { auth: { getUser }, from } as never;
  return { client, snapshot: () => rows.map((row) => ({ ...row })) };
}

describe("saveUserFeedAllocation — coarse round-trip preserves niche allocation (integration, Rule 9)", () => {
  it("keeps niche + beyond-bubble section rows intact while replacing the coarse blocks", async () => {
    // A deep-profile user: two niche `sport` sections (IPL, Team India), one beyond-bubble reserve,
    // plus a coarse `tech` block the user is about to remove.
    const initial: StoredAllocationRow[] = [
      {
        allocation_id: "a1",
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "sport",
        allocation_interest_id: "int-ipl",
        allocation_section_label: "IPL",
        allocation_slot_count: 4,
        allocation_sort_order: 0,
      },
      {
        allocation_id: "a2",
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "sport",
        allocation_interest_id: "int-ti",
        allocation_section_label: "Team India",
        allocation_slot_count: 3,
        allocation_sort_order: 1,
      },
      {
        allocation_id: "a3",
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "arts",
        allocation_interest_id: null,
        allocation_section_label: "Beyond your bubble",
        allocation_slot_count: 1,
        allocation_sort_order: 2,
      },
      {
        allocation_id: "a4",
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "tech",
        allocation_interest_id: null,
        allocation_section_label: null,
        allocation_slot_count: 5,
        allocation_sort_order: 3,
      },
    ];
    const { client, snapshot } = makeInMemoryAllocationDb(initial, { id: AUTHED_USER_ID });

    // Coarse-only save: user keeps ai + business, drops the coarse tech block.
    await saveUserFeedAllocation(
      [
        { bucketId: "ai", count: 10 },
        { bucketId: "business", count: 20 },
      ],
      client,
    );

    const stored = snapshot();
    // Both niche rows survive UNCHANGED (label + count intact) — the corruption guard.
    expect(stored).toContainEqual(
      expect.objectContaining({
        allocation_interest_id: "int-ipl",
        allocation_section_label: "IPL",
        allocation_slot_count: 4,
      }),
    );
    expect(stored).toContainEqual(
      expect.objectContaining({
        allocation_interest_id: "int-ti",
        allocation_section_label: "Team India",
        allocation_slot_count: 3,
      }),
    );
    // The beyond-bubble reserve row survives too (label set → not a coarse row).
    expect(stored).toContainEqual(
      expect.objectContaining({ allocation_section_label: "Beyond your bubble", allocation_slot_count: 1 }),
    );
    // The removed coarse tech block is pruned; the new coarse ai/business blocks are written.
    const coarse = stored.filter((row) => row.allocation_interest_id === null && row.allocation_section_label === null);
    expect(coarse.map((row) => row.allocation_category).sort()).toEqual(["ai", "business"]);

    // And a fresh reload hydrates the two niche sections as read-only named blocks.
    const reloaded = await getUserFeedAllocation(client);
    const niche = reloaded.filter((segment) => segment.interestId != null);
    expect(niche).toEqual([
      { bucketId: "sport", count: 4, interestId: "int-ipl", sectionLabel: "IPL" },
      { bucketId: "sport", count: 3, interestId: "int-ti", sectionLabel: "Team India" },
    ]);
  });
});
