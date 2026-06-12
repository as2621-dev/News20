import { describe, expect, it, vi } from "vitest";
import { getProfileDisplayName, PROFILE_DISPLAY_NAME_MAX_LENGTH, saveProfileDisplayName } from "@/lib/profile";

/**
 * user_profiles persistence at the Supabase client boundary (migration 0012).
 *
 * WHY these tests exist: the display name feeds the Settings header AND the
 * avatar initial — a save that silently drops, writes unscoped, or skips
 * validation would either lose the user's edit or trip the DB check constraint
 * with an unreadable error. These encode the contract (Rule 9):
 *  - save MUST upsert exactly one owner-scoped row with the TRIMMED name.
 *  - empty / over-length names MUST be rejected app-side (no write).
 *  - a signed-out caller MUST write nothing and get a readable error.
 *  - read returns the saved name, or null when no row exists (the email-derived
 *    fallback path).
 *
 * Mocks the Supabase client at the boundary, mirroring tests/lib/follows.test.ts.
 */

const AUTHED_USER_ID = "user-uuid-1";

/**
 * Build a fake Supabase client for the profile read/upsert chains.
 *
 * @param options.user - The authed user (or null for signed-out).
 * @param options.savedName - The stored display name maybeSingle resolves to.
 */
function makeFakeClient(options: { user: { id: string } | null; savedName?: string }) {
  const getUser = vi.fn().mockResolvedValue({ data: { user: options.user }, error: null });

  // getProfileDisplayName: .from().select().eq().maybeSingle()
  const maybeSingle = vi.fn().mockResolvedValue({
    data: options.savedName !== undefined ? { profile_display_name: options.savedName } : null,
    error: null,
  });
  const select = vi.fn().mockReturnValue({ eq: vi.fn().mockReturnValue({ maybeSingle }) });
  const upsert = vi.fn().mockResolvedValue({ error: null });
  const from = vi.fn().mockReturnValue({ select, upsert });

  // Reason: the fake only implements the surface profile.ts uses; `as never`
  // satisfies the SupabaseClient type at this test boundary without a full stub.
  const client = { auth: { getUser }, from } as never;
  return { client, getUser, from, upsert, maybeSingle };
}

describe("saveProfileDisplayName", () => {
  it("upserts one owner-scoped row with the trimmed name (happy path)", async () => {
    // WHY: the DoD — saving "  Riya Sharma  " must persist exactly one row keyed
    // to the AUTHED user with the trimmed name. Fails if the write is dropped,
    // unscoped, or sent untrimmed (which would render padded in the header).
    const { client, upsert } = makeFakeClient({ user: { id: AUTHED_USER_ID } });

    const result = await saveProfileDisplayName("  Riya Sharma  ", client);

    expect(result).toEqual({ ok: true });
    expect(upsert).toHaveBeenCalledTimes(1);
    expect(upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        profile_user_id: AUTHED_USER_ID,
        profile_display_name: "Riya Sharma",
      }),
      { onConflict: "profile_user_id" },
    );
  });

  it("rejects empty and over-length names app-side without writing (failure case)", async () => {
    // WHY: mirrors ck_profile_display_name_length — the user must get a readable
    // error, not a Postgres check-constraint violation. Fails if validation is
    // removed or runs after the write.
    const { client, upsert } = makeFakeClient({ user: { id: AUTHED_USER_ID } });

    const emptyResult = await saveProfileDisplayName("   ", client);
    const overLengthResult = await saveProfileDisplayName("x".repeat(PROFILE_DISPLAY_NAME_MAX_LENGTH + 1), client);

    expect(emptyResult.ok).toBe(false);
    expect(overLengthResult.ok).toBe(false);
    expect(upsert).not.toHaveBeenCalled();
  });

  it("writes nothing and returns a readable error when signed out (graceful degrade, Rule 12)", async () => {
    // WHY: editing the name is an authed surface; a signed-out save must not
    // attempt an anon write (RLS would reject it opaquely anyway).
    const { client, upsert } = makeFakeClient({ user: null });

    const result = await saveProfileDisplayName("Riya", client);

    expect(result.ok).toBe(false);
    expect(upsert).not.toHaveBeenCalled();
  });
});

describe("getProfileDisplayName", () => {
  it("returns the saved name when a row exists, null when absent (edge: fallback path)", async () => {
    // WHY: null is the signal for SettingsLayer to fall back to the
    // email-derived name — returning "" or throwing would break the header.
    const present = makeFakeClient({ user: { id: AUTHED_USER_ID }, savedName: "Riya Sharma" });
    const absent = makeFakeClient({ user: { id: AUTHED_USER_ID } });

    expect(await getProfileDisplayName(present.client)).toBe("Riya Sharma");
    expect(await getProfileDisplayName(absent.client)).toBeNull();
  });

  it("returns null when signed out without querying the table", async () => {
    // WHY: the layer renders signed-out settings without a profile round-trip.
    const { client, from } = makeFakeClient({ user: null });

    expect(await getProfileDisplayName(client)).toBeNull();
    expect(from).not.toHaveBeenCalled();
  });
});
