import { describe, expect, it, vi } from "vitest";
import { followSource, getUserSources, listSourcesByArchetype, setSourcePriority, unfollowSource } from "@/lib/sources";
import type { ContentSource, UserContentSource } from "@/types/source";

/**
 * Phase 5b SP4 — content-source data layer at the Supabase client boundary.
 *
 * WHY these tests exist (Rule 9 — encode the business contract, not call shapes):
 *  - A FRESH follow MUST default to priority `everything`. That default is the
 *    product promise: a source you just followed ingests all its items until you
 *    tune it down in the control surface. If the default silently flipped to
 *    `off`/`big_stuff`, a freshly-followed source would go dark — a user-visible
 *    "I followed it but see nothing" bug. So we assert the upserted row's
 *    `source_priority` is exactly `everything`.
 *  - `getUserSources` MUST return ONLY the caller's rows (RLS owner-scoping). A
 *    leak here exposes another user's follow set; we assert the read is pinned to
 *    the authed `user_id` and returns exactly the mocked caller rows.
 *  - `setSourcePriority` MUST issue the enum UPDATE on the caller's row — the
 *    control-surface dial is a no-op (or worse, a leak) if it doesn't.
 *  - A query error MUST SURFACE (throw), never be swallowed (Rule 12) — a silent
 *    failure leaves the UI claiming success while the DB is untouched.
 *
 * Mocks the Supabase client at the boundary (CLAUDE.md mocking strategy),
 * mirroring tests/lib/follows.test.ts + tests/lib/entities.test.ts.
 */

const AUTHED_USER_ID = "user-uuid-1";
const SOURCE_ID = "src-uuid-1";

/** A fully-populated content_sources row as PostgREST returns it (for the browse read). */
function makeContentSource(overrides: Partial<ContentSource> = {}): ContentSource {
  return {
    source_id: "src-uuid-cat-1",
    content_source_type: "youtube_channel",
    external_id: "UC_abc",
    source_name: "Frontier AI Weekly",
    source_description: "Deep dives on frontier models.",
    thumbnail_url: "https://example.com/a.jpg",
    subscriber_count: 120000,
    platform_metadata: { country: "US" },
    personas: ["ai-frontier-tech"],
    topic_tags: ["ai", "tech"],
    popularity_score: 90,
    is_curated: true,
    last_fetched_at: null,
    ...overrides,
  };
}

/**
 * Fake client for the public-read browse chain:
 * `.from().select().overlaps().eq().order().limit().returns()`.
 * Captures the overlaps/eq/order/limit calls so a test can assert the GIN
 * overlap filter, axis filter, and popularity ordering are wired correctly.
 */
function makeBrowseClient(result: { data: unknown; error: unknown }) {
  const calls = {
    overlaps: [] as Array<[string, unknown]>,
    eq: [] as Array<[string, unknown]>,
    order: [] as Array<[string, unknown]>,
    limit: [] as number[],
  };
  const returns = vi.fn().mockResolvedValue(result);
  const builder: Record<string, unknown> = { returns };
  builder.overlaps = vi.fn((column: string, value: unknown) => {
    calls.overlaps.push([column, value]);
    return builder;
  });
  builder.eq = vi.fn((column: string, value: unknown) => {
    calls.eq.push([column, value]);
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
  // Reason: the fake only implements the surface sources.ts uses; `as never`
  // satisfies the SupabaseClient type at this test boundary without a full stub.
  return { client: { from } as never, from, select, calls };
}

/**
 * Fake client for the owner-scoped user_content_sources mutations/reads. Wires
 * `auth.getUser()` plus the upsert/delete/update/select chains and captures their
 * payloads so a test can assert exactly what was written, owner-scoped.
 */
function makeUserSourcesClient(options: {
  user: { id: string } | null;
  readResult?: { data: unknown; error: unknown };
  writeError?: unknown;
}) {
  const getUser = vi.fn().mockResolvedValue({ data: { user: options.user }, error: null });

  const upsert = vi.fn().mockResolvedValue({ error: options.writeError ?? null });

  // getUserSources read: .from().select().eq().returns()
  const returns = vi.fn().mockResolvedValue(options.readResult ?? { data: [], error: null });
  const selectEqCalls = [] as Array<[string, unknown]>;
  const select = vi.fn().mockReturnValue({
    eq: vi.fn((column: string, value: unknown) => {
      selectEqCalls.push([column, value]);
      return { returns };
    }),
  });

  // delete: .from().delete().eq(user).eq(source) → resolves the delete
  const deleteEqCalls = [] as Array<[string, unknown]>;
  const deleteSecondEq = vi.fn((column: string, value: unknown) => {
    deleteEqCalls.push([column, value]);
    return Promise.resolve({ error: options.writeError ?? null });
  });
  const del = vi.fn().mockReturnValue({
    eq: vi.fn((column: string, value: unknown) => {
      deleteEqCalls.push([column, value]);
      return { eq: deleteSecondEq };
    }),
  });

  // update: .from().update(payload).eq(user).eq(source) → resolves the update
  const updateEqCalls = [] as Array<[string, unknown]>;
  let updatePayload: unknown = null;
  const updateSecondEq = vi.fn((column: string, value: unknown) => {
    updateEqCalls.push([column, value]);
    return Promise.resolve({ error: options.writeError ?? null });
  });
  const update = vi.fn((payload: unknown) => {
    updatePayload = payload;
    return {
      eq: vi.fn((column: string, value: unknown) => {
        updateEqCalls.push([column, value]);
        return { eq: updateSecondEq };
      }),
    };
  });

  const from = vi.fn().mockReturnValue({ select, upsert, delete: del, update });
  const client = { auth: { getUser }, from } as never;
  return {
    client,
    from,
    getUser,
    upsert,
    select,
    del,
    update,
    selectEqCalls,
    deleteEqCalls,
    updateEqCalls,
    getUpdatePayload: () => updatePayload,
  };
}

describe("listSourcesByArchetype (public catalog browse)", () => {
  it("filters by persona overlap + axis and orders by popularity desc", async () => {
    // WHY: the recommendation grid (5c) ranks by popularity within an archetype.
    // A dropped overlaps()/order() would surface the wrong or unranked sources.
    const rows = [makeContentSource(), makeContentSource({ source_id: "src-uuid-cat-2", popularity_score: 80 })];
    const { client, from, calls } = makeBrowseClient({ data: rows, error: null });

    const result = await listSourcesByArchetype(["ai-frontier-tech"], "youtube_channel", 12, client);

    expect(from).toHaveBeenCalledWith("content_sources");
    expect(calls.overlaps).toContainEqual(["personas", ["ai-frontier-tech"]]);
    expect(calls.eq).toContainEqual(["content_source_type", "youtube_channel"]);
    expect(calls.order).toContainEqual(["popularity_score", { ascending: false }]);
    expect(calls.limit).toContain(12);
    expect(result).toHaveLength(2);
    expect(result[0].source_name).toBe("Frontier AI Weekly");
  });

  it("short-circuits an empty persona set to [] without a round-trip (edge case)", async () => {
    // WHY: a `personas && '{}'` overlap matches zero rows anyway; skipping the
    // query is cheaper and avoids a pointless DB hit. The `from` must never fire.
    const { client, from } = makeBrowseClient({ data: [], error: null });

    const result = await listSourcesByArchetype([], "podcast", 20, client);

    expect(result).toEqual([]);
    expect(from).not.toHaveBeenCalled();
  });

  it("throws when the browse query errors (surface, never swallow — Rule 12)", async () => {
    const { client } = makeBrowseClient({ data: null, error: { message: "permission denied" } });

    await expect(listSourcesByArchetype(["ai-frontier-tech"], "youtube_channel", 20, client)).rejects.toThrow(
      /Failed to list youtube_channel sources/i,
    );
  });
});

describe("followSource (owner-scoped upsert)", () => {
  it("upserts a row with default priority 'everything' for a fresh follow (the DoD)", async () => {
    // WHY: the product default — a freshly-followed source ingests EVERYTHING
    // until tuned. If this default flips, the source silently goes dark. We assert
    // the upserted row is owner-scoped AND carries source_priority 'everything'.
    const { client, upsert } = makeUserSourcesClient({ user: { id: AUTHED_USER_ID } });

    await followSource(SOURCE_ID, undefined, client);

    expect(upsert).toHaveBeenCalledTimes(1);
    const [payload, options] = upsert.mock.calls[0];
    expect(payload).toMatchObject({
      user_id: AUTHED_USER_ID,
      source_id: SOURCE_ID,
      source_priority: "everything",
    });
    // Idempotent re-follow rides the (user_id, source_id) PK.
    expect(options).toMatchObject({ onConflict: "user_id,source_id" });
  });

  it("honors an explicit non-default priority when one is passed", async () => {
    // WHY: following at 'big_stuff' (mute-to-highlights) must not be overridden by
    // the default — the explicit arg is the user's intent.
    const { client, upsert } = makeUserSourcesClient({ user: { id: AUTHED_USER_ID } });

    await followSource(SOURCE_ID, "big_stuff", client);

    expect(upsert.mock.calls[0][0]).toMatchObject({ source_priority: "big_stuff" });
  });

  it("throws when signed out — never writes an anon follow (Rule 12)", async () => {
    // WHY: a source follow is an explicit authed action. Signed out it must throw
    // loudly (not silently no-op), so the UI never claims a follow that RLS rejects.
    const { client, upsert } = makeUserSourcesClient({ user: null });

    await expect(followSource(SOURCE_ID, undefined, client)).rejects.toThrow(/signed out/i);
    expect(upsert).not.toHaveBeenCalled();
  });

  it("throws when the upsert errors (surface, never swallow — Rule 12)", async () => {
    const { client } = makeUserSourcesClient({
      user: { id: AUTHED_USER_ID },
      writeError: { message: "duplicate key value" },
    });

    await expect(followSource(SOURCE_ID, undefined, client)).rejects.toThrow(/Failed to follow source/i);
  });
});

describe("getUserSources (RLS owner-scoped read)", () => {
  it("returns ONLY the caller's rows, pinned to the authed user_id (RLS)", async () => {
    // WHY: the read must be scoped to auth.uid() — a leak exposes another user's
    // follow set. We assert the read filters on the authed user_id and hands back
    // exactly the mocked caller rows.
    const callerRows: UserContentSource[] = [
      { user_id: AUTHED_USER_ID, source_id: SOURCE_ID, source_priority: "everything", added_via: "onboarding" },
      { user_id: AUTHED_USER_ID, source_id: "src-uuid-2", source_priority: "big_stuff", added_via: "manual" },
    ];
    const { client, from, selectEqCalls } = makeUserSourcesClient({
      user: { id: AUTHED_USER_ID },
      readResult: { data: callerRows, error: null },
    });

    const result = await getUserSources(client);

    expect(from).toHaveBeenCalledWith("user_content_sources");
    expect(selectEqCalls).toContainEqual(["user_id", AUTHED_USER_ID]);
    expect(result).toHaveLength(2);
    expect(result.every((row) => row.user_id === AUTHED_USER_ID)).toBe(true);
  });

  it("throws when signed out (no anon read of a per-user table)", async () => {
    const { client, from } = makeUserSourcesClient({ user: null });

    await expect(getUserSources(client)).rejects.toThrow(/signed out/i);
    expect(from).not.toHaveBeenCalled();
  });

  it("throws when the read errors (surface, never swallow — Rule 12)", async () => {
    const { client } = makeUserSourcesClient({
      user: { id: AUTHED_USER_ID },
      readResult: { data: null, error: { message: "permission denied" } },
    });

    await expect(getUserSources(client)).rejects.toThrow(/Failed to read user content sources/i);
  });
});

describe("setSourcePriority (control-surface enum update)", () => {
  it("issues the enum UPDATE on the caller's (user_id, source_id) row", async () => {
    // WHY: the control-surface dial re-prioritizes a follow. The update payload
    // MUST set source_priority to the new enum, scoped to the authed user + source
    // — otherwise the dial is a no-op or, worse, touches another user's row.
    const { client, update, updateEqCalls, getUpdatePayload } = makeUserSourcesClient({
      user: { id: AUTHED_USER_ID },
    });

    await setSourcePriority(SOURCE_ID, "off", client);

    expect(update).toHaveBeenCalledTimes(1);
    expect(getUpdatePayload()).toEqual({ source_priority: "off" });
    expect(updateEqCalls).toContainEqual(["user_id", AUTHED_USER_ID]);
    expect(updateEqCalls).toContainEqual(["source_id", SOURCE_ID]);
  });

  it("throws when the update errors (surface, never swallow — Rule 12)", async () => {
    const { client } = makeUserSourcesClient({
      user: { id: AUTHED_USER_ID },
      writeError: { message: "invalid input value for enum" },
    });

    await expect(setSourcePriority(SOURCE_ID, "off", client)).rejects.toThrow(/Failed to set priority/i);
  });
});

describe("unfollowSource (owner-scoped delete)", () => {
  it("deletes the caller's row, scoped to the authed user + source", async () => {
    const { client, del, deleteEqCalls } = makeUserSourcesClient({ user: { id: AUTHED_USER_ID } });

    await unfollowSource(SOURCE_ID, client);

    expect(del).toHaveBeenCalledTimes(1);
    expect(deleteEqCalls).toContainEqual(["user_id", AUTHED_USER_ID]);
    expect(deleteEqCalls).toContainEqual(["source_id", SOURCE_ID]);
  });

  it("throws when the delete errors (surface, never swallow — Rule 12)", async () => {
    const { client } = makeUserSourcesClient({
      user: { id: AUTHED_USER_ID },
      writeError: { message: "permission denied" },
    });

    await expect(unfollowSource(SOURCE_ID, client)).rejects.toThrow(/Failed to unfollow source/i);
  });
});
