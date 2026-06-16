import { describe, expect, it, vi } from "vitest";
import { sendMagicLink, signInWithTestPassword, signOut, TEST_AUTH_CODE, verifyEmailOtp } from "@/lib/supabase/auth";

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

/**
 * Fake Supabase auth client whose `auth.verifyOtp` resolves to the given result.
 * Same client-boundary mocking as {@link makeFakeAuthClient}.
 */
function makeFakeOtpClient(result: { error: unknown }) {
  const verifyOtp = vi.fn().mockResolvedValue(result);
  return { client: { auth: { verifyOtp } } as never, verifyOtp };
}

describe("verifyEmailOtp", () => {
  it("calls verifyOtp exactly once with email + token + type 'email' and returns ok", async () => {
    // WHY: the happy path is the in-app iOS sign-in contract — the emailed code
    // must reach Supabase as a type:"email" OTP verification exactly once. Fails
    // if the call is dropped, duplicated, or sent with the wrong OTP type (a
    // type:"magiclink" token would be rejected server-side).
    const { client, verifyOtp } = makeFakeOtpClient({ error: null });

    const result = await verifyEmailOtp("reader@example.com", "12345678", client);

    expect(result).toEqual({ ok: true });
    expect(verifyOtp).toHaveBeenCalledTimes(1);
    expect(verifyOtp.mock.calls[0][0]).toEqual({
      email: "reader@example.com",
      token: "12345678",
      type: "email",
    });
  });

  it("returns ok:false and NEVER calls verifyOtp for a malformed code (Rule 12)", async () => {
    // WHY: Supabase rate-limits OTP attempts — an obviously bad code (wrong
    // length, non-digits) must be rejected locally with ZERO API calls. Fails if
    // the local code-shape guard is removed.
    const { client, verifyOtp } = makeFakeOtpClient({ error: null });

    const result = await verifyEmailOtp("reader@example.com", "1234", client);

    expect(result.ok).toBe(false);
    expect(verifyOtp).not.toHaveBeenCalled();
  });

  it("maps a Supabase error (expired/wrong code) to ok:false with the error message", async () => {
    // WHY: an expired or mistyped code must surface as an inline error, never a
    // silent success — fails if the error branch is dropped.
    const { client, verifyOtp } = makeFakeOtpClient({ error: { message: "Token has expired or is invalid" } });

    const result = await verifyEmailOtp("reader@example.com", "12345678", client);

    expect(result).toEqual({ ok: false, error_message: "Token has expired or is invalid" });
    expect(verifyOtp).toHaveBeenCalledTimes(1);
  });
});

/**
 * Fake Supabase auth client exposing `signInWithPassword` + `signUp` for the
 * test-mode fixed-password sign-in. `signInError` drives the first call; `signUp`
 * controls the fallback (its returned session / error). Client-boundary mocking.
 */
function makeFakeTestPasswordClient(opts: { signInError?: unknown; signUpSession?: unknown; signUpError?: unknown }) {
  const signInWithPassword = vi.fn().mockResolvedValue({ data: {}, error: opts.signInError ?? null });
  const signUp = vi
    .fn()
    .mockResolvedValue({ data: { session: opts.signUpSession ?? null }, error: opts.signUpError ?? null });
  return { client: { auth: { signInWithPassword, signUp } } as never, signInWithPassword, signUp };
}

describe("signInWithTestPassword", () => {
  it("signs an EXISTING test user in with the fixed code and never calls signUp", async () => {
    // WHY: the deterministic-password contract — a returning test email must sign
    // back in via signInWithPassword (password = the fixed code), NOT create a
    // duplicate. Fails if signUp is called when sign-in already succeeded.
    const { client, signInWithPassword, signUp } = makeFakeTestPasswordClient({ signInError: null });

    const result = await signInWithTestPassword("1234@gmail.com", TEST_AUTH_CODE, client);

    expect(result).toEqual({ ok: true });
    expect(signInWithPassword).toHaveBeenCalledTimes(1);
    expect(signInWithPassword.mock.calls[0][0]).toEqual({ email: "1234@gmail.com", password: TEST_AUTH_CODE });
    expect(signUp).not.toHaveBeenCalled();
  });

  it("creates a NEW test user (signUp returns a live session) when sign-in fails", async () => {
    // WHY: a fresh throwaway email has no account — sign-in errors, and signUp must
    // create it AND return a session (only happens with email confirmation OFF).
    const { client, signUp } = makeFakeTestPasswordClient({
      signInError: { message: "Invalid login credentials" },
      signUpSession: { user: { id: "test-uid" } },
    });

    const result = await signInWithTestPassword("new@gmail.com", TEST_AUTH_CODE, client);

    expect(result).toEqual({ ok: true });
    expect(signUp).toHaveBeenCalledTimes(1);
  });

  it("returns ok:false when signUp yields NO session (email confirmation still ON)", async () => {
    // WHY: if 'Confirm email' is left ON, signUp succeeds without a session — the
    // user would appear stuck. This must fail loud with a fix hint, not silently ok.
    const { client } = makeFakeTestPasswordClient({
      signInError: { message: "Invalid login credentials" },
      signUpSession: null,
    });

    const result = await signInWithTestPassword("new@gmail.com", TEST_AUTH_CODE, client);

    expect(result.ok).toBe(false);
  });

  it("returns ok:false and calls NOTHING for a non-matching code (Rule 12)", async () => {
    // WHY: the password must stay deterministic — only the fixed code is accepted,
    // rejected locally with zero API calls so a typo can't mint a second password.
    const { client, signInWithPassword, signUp } = makeFakeTestPasswordClient({ signInError: null });

    const result = await signInWithTestPassword("1234@gmail.com", "000000", client);

    expect(result.ok).toBe(false);
    expect(signInWithPassword).not.toHaveBeenCalled();
    expect(signUp).not.toHaveBeenCalled();
  });

  it("returns ok:false and calls NOTHING for an invalid email (Rule 12)", async () => {
    // WHY: an obviously bad address must fail locally before any network call.
    const { client, signInWithPassword } = makeFakeTestPasswordClient({ signInError: null });

    const result = await signInWithTestPassword("not-an-email", TEST_AUTH_CODE, client);

    expect(result.ok).toBe(false);
    expect(signInWithPassword).not.toHaveBeenCalled();
  });
});

/**
 * Fake Supabase auth client whose `auth.signOut` resolves (or rejects). Same
 * client-boundary mocking as {@link makeFakeAuthClient}.
 */
function makeFakeSignOutClient(result: { error: unknown } | Error) {
  const signOutFn = result instanceof Error ? vi.fn().mockRejectedValue(result) : vi.fn().mockResolvedValue(result);
  return { client: { auth: { signOut: signOutFn } } as never, signOutFn };
}

describe("signOut", () => {
  it("calls auth.signOut exactly once and returns ok", async () => {
    // WHY: the happy path is the account-sheet contract — sign-out must reach
    // Supabase exactly once so the persisted session is actually cleared before
    // the UI routes back to onboarding.
    const { client, signOutFn } = makeFakeSignOutClient({ error: null });

    const result = await signOut(client);

    expect(result).toEqual({ ok: true });
    expect(signOutFn).toHaveBeenCalledTimes(1);
  });

  it("maps a Supabase error to ok:false with the error message", async () => {
    // WHY: a failed sign-out must surface as an inline error — routing to
    // onboarding with a still-live session would look signed-out while staying
    // signed in (Rule 12: fail loud).
    const { client } = makeFakeSignOutClient({ error: { message: "network down" } });

    const result = await signOut(client);

    expect(result).toEqual({ ok: false, error_message: "network down" });
  });

  it("maps a thrown (rejected) signOut to ok:false instead of crashing", async () => {
    // WHY (edge): supabase-js rejects on transport-level failures; the wrapper
    // must convert the rejection to the discriminated union the UI switches on.
    const { client } = makeFakeSignOutClient(new Error("fetch failed"));

    const result = await signOut(client);

    expect(result).toEqual({ ok: false, error_message: "fetch failed" });
  });
});
