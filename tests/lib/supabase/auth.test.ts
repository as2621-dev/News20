import { describe, expect, it, vi } from "vitest";
import { sendMagicLink } from "@/lib/supabase/auth";

/**
 * Fake Supabase auth client whose `auth.signInWithOtp` resolves to the given
 * result. Mocks at the client boundary (CLAUDE.md mocking strategy), mirroring
 * `tests/lib/feed/supabaseFeed.test.ts`.
 */
function makeFakeAuthClient(result: { error: unknown }) {
  const signInWithOtp = vi.fn().mockResolvedValue(result);
  // Reason: the fake only implements the `auth.signInWithOtp` surface sendMagicLink
  // uses; `as never` satisfies the SupabaseClient type at this test boundary
  // without stubbing the whole client.
  return { client: { auth: { signInWithOtp } } as never, signInWithOtp };
}

describe("sendMagicLink", () => {
  it("calls signInWithOtp exactly once for a valid email and returns ok", async () => {
    // WHY: the happy path is the contract — a valid address must reach Supabase
    // exactly once with the email and a /callback redirect. This fails if the call
    // is dropped, duplicated, or sent without the redirect option.
    const { client, signInWithOtp } = makeFakeAuthClient({ error: null });

    const result = await sendMagicLink("reader@example.com", client);

    expect(result).toEqual({ ok: true });
    expect(signInWithOtp).toHaveBeenCalledTimes(1);
    const callArg = signInWithOtp.mock.calls[0][0];
    expect(callArg.email).toBe("reader@example.com");
    expect(callArg.options.emailRedirectTo).toMatch(/\/callback$/);
  });

  it("returns ok:false and NEVER calls signInWithOtp for an invalid email (Rule 12)", async () => {
    // WHY: this encodes the fail-loud guarantee — an invalid email must be rejected
    // locally with ZERO API calls. This test FAILS if the Zod validation guard in
    // sendMagicLink is removed (signInWithOtp would then be called).
    const { client, signInWithOtp } = makeFakeAuthClient({ error: null });

    const result = await sendMagicLink("not-an-email", client);

    expect(result.ok).toBe(false);
    expect(signInWithOtp).not.toHaveBeenCalled();
  });

  it("maps a Supabase error to ok:false with the error message", async () => {
    // WHY: a server-side failure must surface to the UI as an error state, not a
    // silent success — fails if the error branch is dropped.
    const { client, signInWithOtp } = makeFakeAuthClient({ error: { message: "rate limit exceeded" } });

    const result = await sendMagicLink("reader@example.com", client);

    expect(result).toEqual({ ok: false, error_message: "rate limit exceeded" });
    expect(signInWithOtp).toHaveBeenCalledTimes(1);
  });
});
