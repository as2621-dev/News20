import { describe, expect, it, vi } from "vitest";
import { getFollowedStoryIds, isFollowing, toggleFollow } from "@/lib/follows";

/**
 * Phase 3d SP3 — follow persistence at the Supabase client boundary.
 *
 * WHY these tests exist: the follow toggle is a RANKING signal — a wrong row
 * (or a missing one) silently mis-weights tomorrow's feed. So these encode the
 * business contract, not just call shapes (Rule 9):
 *  - toggle ON when absent MUST insert exactly one owner-scoped row and report
 *    `true`; toggle OFF when present MUST delete it and report `false` (the DoD).
 *  - a signed-out caller MUST write NOTHING and report "not following" — follow
 *    is an authed surface that degrades gracefully, never crashes or leaks.
 *
 * Mocks the Supabase client at the boundary (CLAUDE.md mocking strategy),
 * mirroring tests/lib/feed/supabaseFeed.test.ts + tests/lib/supabase/auth.test.ts.
 */

const AUTHED_USER_ID = "user-uuid-1";
const STORY_ID = "s1";

/**
 * Build a fake Supabase client whose `auth.getUser()` and `from("follows")`
 * query chains resolve to configured results. Captures the insert/delete payloads
 * so a test can assert exactly what was written (owner-scoped, idempotent).
 *
 * @param options.user - The authed user (or null for signed-out).
 * @param options.existingFollow - Whether `isFollowing`'s read finds a row.
 */
function makeFakeClient(options: { user: { id: string } | null; existingFollow?: boolean }) {
  const getUser = vi.fn().mockResolvedValue({
    data: { user: options.user },
    error: null,
  });

  // isFollowing's read: .from().select().eq().eq().maybeSingle()
  const maybeSingle = vi
    .fn()
    .mockResolvedValue({ data: options.existingFollow ? { follow_id: "f1" } : null, error: null });
  // getFollowedStoryIds' read: .from().select().eq().returns()
  const returns = vi.fn().mockResolvedValue({
    data: options.existingFollow ? [{ follow_story_id: STORY_ID }] : [],
    error: null,
  });

  const insert = vi.fn().mockResolvedValue({ error: null });

  // The two eq() calls in a read chain end in maybeSingle; the single eq() in the
  // hydrate read ends in returns; delete's two eq() calls resolve the delete.
  const deleteEqUser = { eq: vi.fn().mockResolvedValue({ error: null }) };
  const del = vi.fn().mockReturnValue({ eq: vi.fn().mockReturnValue(deleteEqUser) });

  const selectChain = {
    // hydrate: select().eq().returns()
    eq: vi.fn().mockReturnValue({
      returns,
      // isFollowing: select().eq().eq().maybeSingle()
      eq: vi.fn().mockReturnValue({ maybeSingle }),
    }),
  };
  const select = vi.fn().mockReturnValue(selectChain);
  const from = vi.fn().mockReturnValue({ select, insert, delete: del });

  // Reason: the fake only implements the surface follows.ts uses; `as never`
  // satisfies the SupabaseClient type at this test boundary without a full stub.
  const client = { auth: { getUser }, from } as never;
  return { client, getUser, from, select, insert, del, maybeSingle, returns };
}

describe("toggleFollow", () => {
  it("inserts an owner-scoped row and returns true when not yet following (toggle ON)", async () => {
    // WHY: the DoD — tapping follow on an unfollowed story MUST persist exactly
    // one row keyed to the AUTHED user + story, and report the new "on" state.
    // Fails if the insert is dropped, sent unscoped, or the return flips.
    const { client, insert } = makeFakeClient({ user: { id: AUTHED_USER_ID }, existingFollow: false });

    const result = await toggleFollow(STORY_ID, client);

    expect(result).toBe(true);
    expect(insert).toHaveBeenCalledTimes(1);
    expect(insert).toHaveBeenCalledWith({
      follow_user_id: AUTHED_USER_ID,
      follow_story_id: STORY_ID,
    });
  });

  it("deletes the row and returns false when already following (toggle OFF)", async () => {
    // WHY: the second half of the DoD — tapping again removes the row and reverts
    // to "off". Fails if delete is skipped or the return state is wrong.
    const { client, del, insert } = makeFakeClient({ user: { id: AUTHED_USER_ID }, existingFollow: true });

    const result = await toggleFollow(STORY_ID, client);

    expect(result).toBe(false);
    expect(del).toHaveBeenCalledTimes(1);
    expect(insert).not.toHaveBeenCalled();
  });

  it("writes nothing and returns false when signed out (graceful degrade, Rule 12)", async () => {
    // WHY: follow is an authed surface. A signed-out tap MUST NOT write (it would
    // be an anon row the RLS rejects anyway) and MUST report "not following" so
    // the optimistic UI reverts. Fails if the unauth guard is removed.
    const { client, insert, del } = makeFakeClient({ user: null });

    const result = await toggleFollow(STORY_ID, client);

    expect(result).toBe(false);
    expect(insert).not.toHaveBeenCalled();
    expect(del).not.toHaveBeenCalled();
  });
});

describe("isFollowing", () => {
  it("reflects the persisted row: true when present, false when absent", async () => {
    // WHY: the DoD requires isFollowing to mirror the stored row — this is what
    // hydrates the reel accent on reload.
    const present = makeFakeClient({ user: { id: AUTHED_USER_ID }, existingFollow: true });
    const absent = makeFakeClient({ user: { id: AUTHED_USER_ID }, existingFollow: false });

    expect(await isFollowing(STORY_ID, present.client)).toBe(true);
    expect(await isFollowing(STORY_ID, absent.client)).toBe(false);
  });

  it("returns false when signed out without touching the table", async () => {
    const { client, from } = makeFakeClient({ user: null });

    expect(await isFollowing(STORY_ID, client)).toBe(false);
    expect(from).not.toHaveBeenCalled();
  });
});

describe("getFollowedStoryIds", () => {
  it("returns the set of followed story ids for the authed user", async () => {
    const { client } = makeFakeClient({ user: { id: AUTHED_USER_ID }, existingFollow: true });

    const followed = await getFollowedStoryIds(client);

    expect(followed.has(STORY_ID)).toBe(true);
    expect(followed.size).toBe(1);
  });

  it("returns an empty set when signed out (no crash)", async () => {
    const { client, from } = makeFakeClient({ user: null });

    const followed = await getFollowedStoryIds(client);

    expect(followed.size).toBe(0);
    expect(from).not.toHaveBeenCalled();
  });
});
