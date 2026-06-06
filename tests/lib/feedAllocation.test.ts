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
 *  - The `podcasts`-enum-missing path (migration 0010 not yet applied) must DEGRADE: persist
 *    the other 8, surface podcasts as deferred, and NOT throw — else the whole onboarding
 *    save crashes for every user until 0010 ships.
 *  - A NON-podcasts upsert error MUST surface (throw) — never swallowed (Rule 12).
 *  - READ must be owner-scoped, ordered by sort order, and mapped back to design buckets.
 *
 * Mocks the Supabase client at the boundary (CLAUDE.md mocking strategy), mirroring
 * tests/lib/sources.test.ts.
 */

const AUTHED_USER_ID = "user-uuid-1";

/** A PostgrestError shaped like Postgres' "unknown enum literal" failure (SQLSTATE 22P02). */
function makePodcastsEnumMissingError(): PostgrestError {
  return {
    message: 'invalid input value for enum feed_category: "podcasts"',
    code: "22P02",
    details: "",
    hint: "",
    name: "PostgrestError",
  } as PostgrestError;
}

/**
 * Fake client for the owner-scoped user_feed_allocation read + save chains.
 *
 * READ:   `.from().select().eq().order().returns()`
 * UPSERT: `.from().upsert(rows, opts)` → { error }
 * DELETE: `.from().delete().eq().not()`  (and `.eq()` alone when the saved set is empty)
 *
 * Captures every upsert (payload + onConflict) and the delete's `.not()` args so a test
 * can assert exactly what was written/pruned, owner-scoped. `upsertErrors` is a QUEUE so the
 * podcasts-degrade path (first upsert fails, retry succeeds) can be modelled.
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

  // DELETE chain: delete().eq(user) → { not() } | resolves directly when awaited (empty-set guard).
  const deleteEqCalls: Array<[string, unknown]> = [];
  const notCalls: Array<[string, string, string]> = [];
  const del = vi.fn().mockReturnValue({
    eq: vi.fn((column: string, value: unknown) => {
      deleteEqCalls.push([column, value]);
      const resolved = Promise.resolve({ error: options.deleteError ?? null });
      // The query is "thenable": awaiting it directly (no .not()) resolves the delete (the
      // empty-saved-set clear-all path); chaining .not() narrows it then resolves.
      return Object.assign(resolved, {
        not: vi.fn((notColumn: string, operator: string, listValue: string) => {
          notCalls.push([notColumn, operator, listValue]);
          return Promise.resolve({ error: options.deleteError ?? null });
        }),
      });
    }),
  });

  const from = vi.fn().mockReturnValue({ select, upsert, delete: del });
  const client = { auth: { getUser }, from } as never;
  return { client, from, getUser, upsert, del, upsertCalls, selectEqCalls, deleteEqCalls, notCalls };
}

const HAPPY_SEGMENTS: AllocationSegment[] = [
  { bucketId: "breaking", count: 2 },
  { bucketId: "world", count: 4 },
  { bucketId: "tech", count: 24 },
];

describe("saveUserFeedAllocation (owner-scoped upsert + stale-prune)", () => {
  it("upserts one mapped row per bucket with the list index as allocation_sort_order", async () => {
    // WHY: sort order = the user's manual sequence ("in order"). An off-by-one or dropped
    // index plays the briefing in the wrong order. We pin the exact mapped rows + conflict key.
    const { client, upsert, upsertCalls } = makeAllocationClient({ user: { id: AUTHED_USER_ID } });

    const result = await saveUserFeedAllocation(HAPPY_SEGMENTS, client);

    expect(upsert).toHaveBeenCalledTimes(1);
    expect(upsertCalls[0].onConflict).toBe("follow_user_id,allocation_category");
    expect(upsertCalls[0].rows).toEqual([
      {
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "breaking",
        allocation_slot_count: 2,
        allocation_sort_order: 0,
      },
      {
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "world_politics",
        allocation_slot_count: 4,
        allocation_sort_order: 1,
      },
      {
        follow_user_id: AUTHED_USER_ID,
        allocation_category: "tech_science",
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
    expect(listValue).toBe("(breaking,world_politics,tech_science)");
  });

  it("degrades gracefully when the podcasts enum value is missing (migration 0010 not applied)", async () => {
    // WHY: until 0010 ships, a podcasts upsert 22P02-fails. The whole save must NOT crash —
    // it drops podcasts, re-upserts the other 8, surfaces podcasts as deferred, and succeeds.
    // FAILS if it throws, or if the retry doesn't exclude podcasts.
    const segments: AllocationSegment[] = [
      { bucketId: "breaking", count: 10 },
      { bucketId: "podcasts", count: 20 },
    ];
    const { client, upsert, upsertCalls } = makeAllocationClient({
      user: { id: AUTHED_USER_ID },
      upsertErrors: [makePodcastsEnumMissingError(), null], // first fails, retry succeeds
    });

    const result = await saveUserFeedAllocation(segments, client);

    expect(upsert).toHaveBeenCalledTimes(2); // initial (all) + retry (without podcasts)
    // The retry rows exclude the podcasts row.
    const retryCategories = upsertCalls[1].rows.map((row) => row.allocation_category);
    expect(retryCategories).toEqual(["breaking"]);
    expect(retryCategories).not.toContain("podcasts");
    expect(result.deferred_buckets).toEqual(["podcasts"]);
    expect(result.persisted_count).toBe(1); // only breaking persisted
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
    const nonThirty: AllocationSegment[] = [{ bucketId: "breaking", count: 5 }];
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
      { allocation_category: "world_politics", allocation_slot_count: 4, allocation_sort_order: 0 },
      { allocation_category: "tech_science", allocation_slot_count: 26, allocation_sort_order: 1 },
    ];
    const { client, from, selectEqCalls } = makeAllocationClient({
      user: { id: AUTHED_USER_ID },
      readResult: { data: rows, error: null },
    });

    const result = await getUserFeedAllocation(client);

    expect(from).toHaveBeenCalledWith("user_feed_allocation");
    expect(selectEqCalls).toContainEqual(["follow_user_id", AUTHED_USER_ID]);
    expect(result).toEqual([
      { bucketId: "world", count: 4 },
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
});
