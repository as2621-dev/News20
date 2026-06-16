/**
 * Email magic-link auth wrapper (Phase 1e SP2).
 *
 * Thin, typed layer over `supabase.auth.signInWithOtp` so the UI never touches
 * the raw client. Validates the email with Zod BEFORE any network call (Rule 12:
 * an invalid email must fail loud locally and NEVER hit the API), then requests a
 * passwordless magic link that redirects back to the static-export `/callback`
 * page where `detectSessionInUrl` establishes the session.
 *
 * No secrets logged: we log the auth event names and a coarse failure reason, but
 * never the email body beyond a presence flag, and never the OTP/session tokens.
 */

import type { Session, SupabaseClient } from "@supabase/supabase-js";
import { z } from "zod";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** Email-only schema — the single source of truth for "is this address valid". */
const emailSchema = z.string().trim().email();

/**
 * Test-only auth bypass flag. When `NEXT_PUBLIC_AUTH_TEST_MODE === "true"` the
 * onboarding flow swaps the email/magic-link gate for a fixed-password sign-in
 * (see {@link signInWithTestPassword}) so throwaway emails can be onboarded
 * without the built-in mailer's 2/hour cap. NEXT_PUBLIC_* vars are inlined at
 * build time — a build is required to flip this. Leave UNSET in production.
 */
export const TEST_AUTH_MODE = process.env.NEXT_PUBLIC_AUTH_TEST_MODE === "true";

/**
 * The fixed code that doubles as the Supabase password in {@link TEST_AUTH_MODE}.
 * It MUST be deterministic: the same value is used as the password on every
 * sign-in, so an existing test user signs back in instead of failing.
 */
export const TEST_AUTH_CODE = "123456";

/** Digit count of {@link TEST_AUTH_CODE} — drives the test-mode code input length. */
export const TEST_AUTH_CODE_LENGTH = TEST_AUTH_CODE.length;

/**
 * Cheap "is this a valid email" check the onboarding UI uses to validate the
 * address locally before advancing (test mode has no magic-link send to lean on).
 *
 * @param email - The user-entered address.
 * @returns True when the address passes {@link emailSchema}.
 */
export function isLikelyEmail(email: string): boolean {
  return emailSchema.safeParse(email).success;
}

/**
 * Length of the emailed one-time code. MUST match the Supabase project's
 * `mailer_otp_length` auth setting (currently 8) — if that setting changes, change
 * this constant with it or every code will be rejected locally.
 */
export const OTP_CODE_LENGTH = 8;

/** Digits-only code of exactly {@link OTP_CODE_LENGTH} — validated before any API call. */
const otpCodeSchema = z
  .string()
  .trim()
  .regex(new RegExp(`^\\d{${OTP_CODE_LENGTH}}$`));

/** Result of a magic-link request: a discriminated union the UI switches on. */
export type SendMagicLinkResult = { ok: true } | { ok: false; error_message: string };

/** Result of an email OTP-code verification: a discriminated union the UI switches on. */
export type VerifyEmailOtpResult = { ok: true } | { ok: false; error_message: string };

/**
 * Resolve the absolute URL the magic link should redirect back to.
 *
 * Prefers the live browser origin; falls back to NEXT_PUBLIC_APP_URL when
 * `window` is undefined (SSR / static prerender) so the call is still well-formed.
 *
 * @returns The absolute `/callback` URL for `emailRedirectTo`.
 */
function resolveEmailRedirectTo(): string {
  const origin =
    typeof window !== "undefined" && window.location?.origin
      ? window.location.origin
      : (process.env.NEXT_PUBLIC_APP_URL ?? "");
  return `${origin}/callback`;
}

/**
 * Send a passwordless email magic-link sign-in.
 *
 * Validates the email locally first; an invalid address returns `{ ok: false }`
 * WITHOUT calling Supabase (Rule 12). A valid address calls `signInWithOtp` once.
 *
 * @param email - The user-entered email address.
 * @param client - Optional Supabase client (injected in tests; defaults to the
 *   shared browser client).
 * @returns `{ ok: true }` once the link is requested, else `{ ok: false, error_message }`.
 *
 * @example
 * const result = await sendMagicLink("reader@example.com");
 * if (result.ok) {
 *   // render the "check your inbox" state
 * }
 */
export async function sendMagicLink(
  email: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<SendMagicLinkResult> {
  const parsed = emailSchema.safeParse(email);
  if (!parsed.success) {
    // Reason: Rule 12 — fail loud locally; do NOT spend an API call on a value we
    // already know is invalid. The UI shows an inline error from this branch.
    logger.warn("magic_link_invalid_email", {
      has_at_symbol: email.includes("@"),
      fix_suggestion: "Enter a valid email address before requesting a link.",
    });
    return { ok: false, error_message: "Enter a valid email address." };
  }

  const emailRedirectTo = resolveEmailRedirectTo();
  logger.info("magic_link_send_started", { email_redirect_to: emailRedirectTo });
  try {
    const { error } = await client.auth.signInWithOtp({
      email: parsed.data,
      options: { emailRedirectTo },
    });
    if (error) {
      logger.error("magic_link_send_failed", {
        error_message: error.message,
        fix_suggestion: "Verify the Supabase email provider is enabled and the redirect URL is allowlisted.",
      });
      return { ok: false, error_message: error.message };
    }
    logger.info("magic_link_send_completed", {});
    return { ok: true };
  } catch (exc) {
    const error_message = exc instanceof Error ? exc.message : "Unknown error sending magic link.";
    logger.error("magic_link_send_failed", {
      error_message,
      fix_suggestion: "Check network connectivity and the NEXT_PUBLIC_SUPABASE_* env vars.",
    });
    return { ok: false, error_message };
  }
}

/**
 * Verify the one-time code from the magic-link email and establish a session.
 *
 * The same email that carries the magic link also carries an {@link OTP_CODE_LENGTH}-digit
 * code (`{{ .Token }}` in the Supabase template). Verifying it signs the user in
 * entirely in-app — the path the Capacitor iOS shell relies on, since a magic link
 * tapped in a mail client opens the browser, never the native WebView. Both inputs
 * are validated locally first (Rule 12: an obviously bad code must fail loud
 * WITHOUT spending an API call — Supabase rate-limits OTP attempts).
 *
 * On success supabase-js stores the session (`persistSession`) and fires
 * `onAuthStateChange`, so flow code listening for a session advances on its own.
 *
 * @param email - The email address the code was sent to.
 * @param code - The user-entered code from the email.
 * @param client - Optional Supabase client (injected in tests; defaults to the
 *   shared browser client).
 * @returns `{ ok: true }` once the session is established, else `{ ok: false, error_message }`.
 *
 * @example
 * const result = await verifyEmailOtp("reader@example.com", "12345678");
 * if (result.ok) {
 *   // session established — onAuthStateChange has fired
 * }
 */
export async function verifyEmailOtp(
  email: string,
  code: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<VerifyEmailOtpResult> {
  const parsedEmail = emailSchema.safeParse(email);
  const parsedCode = otpCodeSchema.safeParse(code);
  if (!parsedEmail.success || !parsedCode.success) {
    logger.warn("otp_verify_invalid_input", {
      email_valid: parsedEmail.success,
      code_valid: parsedCode.success,
      fix_suggestion: `Enter the ${OTP_CODE_LENGTH}-digit code exactly as it appears in the email.`,
    });
    return { ok: false, error_message: `Enter the ${OTP_CODE_LENGTH}-digit code from the email.` };
  }

  logger.info("otp_verify_started", {});
  try {
    const { error } = await client.auth.verifyOtp({
      email: parsedEmail.data,
      token: parsedCode.data,
      type: "email",
    });
    if (error) {
      logger.error("otp_verify_failed", {
        error_message: error.message,
        fix_suggestion: "The code may be expired or mistyped — request a fresh magic link and retry.",
      });
      return { ok: false, error_message: error.message };
    }
    logger.info("otp_verify_completed", {});
    return { ok: true };
  } catch (exc) {
    const error_message = exc instanceof Error ? exc.message : "Unknown error verifying the code.";
    logger.error("otp_verify_failed", {
      error_message,
      fix_suggestion: "Check network connectivity and the NEXT_PUBLIC_SUPABASE_* env vars.",
    });
    return { ok: false, error_message };
  }
}

/**
 * TEST-MODE ONLY sign-in: fixed-password auth that bypasses email verification.
 *
 * Active only when {@link TEST_AUTH_MODE}. Uses {@link TEST_AUTH_CODE} as the
 * Supabase password so a REAL session (and therefore a real `auth.uid()`) is
 * established — unlike a faked client session, this keeps every RLS-scoped write
 * (`user_interest_profile`, `user_entity_follows`, `users.user_onboarded_at`)
 * working. Tries `signInWithPassword` first (returning test user), and on failure
 * falls back to `signUp` (new test user) — which returns a live session
 * immediately ONLY when the project has email confirmation disabled.
 *
 * Both inputs are validated locally first (Rule 12): the code must equal
 * {@link TEST_AUTH_CODE} so the password stays deterministic across sign-ins.
 * The code/password is NEVER logged.
 *
 * @param email - The (possibly throwaway) email to onboard.
 * @param code - The user-entered code; must equal {@link TEST_AUTH_CODE}.
 * @param client - Optional Supabase client (injected in tests).
 * @returns `{ ok: true }` once a session is established, else `{ ok: false, error_message }`.
 *
 * @example
 * const result = await signInWithTestPassword("1234@gmail.com", "123456");
 * if (result.ok) {
 *   // real session established — onAuthStateChange has fired
 * }
 */
export async function signInWithTestPassword(
  email: string,
  code: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<VerifyEmailOtpResult> {
  const parsedEmail = emailSchema.safeParse(email);
  if (!parsedEmail.success) {
    logger.warn("test_auth_invalid_email", {
      has_at_symbol: email.includes("@"),
      fix_suggestion: "Enter a valid email address before continuing.",
    });
    return { ok: false, error_message: "Enter a valid email address." };
  }
  if (code !== TEST_AUTH_CODE) {
    logger.warn("test_auth_invalid_code", {
      fix_suggestion: `Test mode expects the fixed code ${TEST_AUTH_CODE}.`,
    });
    return { ok: false, error_message: `Enter the test code ${TEST_AUTH_CODE}.` };
  }

  logger.info("test_auth_sign_in_started", {});
  try {
    // Reason: a returning test user already has password TEST_AUTH_CODE — sign in
    // directly. supabase-js returns "Invalid login credentials" for both a wrong
    // password and an unknown email, so any error here means "try to create it".
    const signInResult = await client.auth.signInWithPassword({
      email: parsedEmail.data,
      password: TEST_AUTH_CODE,
    });
    if (!signInResult.error) {
      logger.info("test_auth_sign_in_completed", { created: false });
      return { ok: true };
    }

    // New test email: create it. With email confirmation disabled this returns a
    // live session and fires onAuthStateChange + the handle_new_user trigger.
    const signUpResult = await client.auth.signUp({
      email: parsedEmail.data,
      password: TEST_AUTH_CODE,
    });
    if (signUpResult.error) {
      logger.error("test_auth_sign_up_failed", {
        error_message: signUpResult.error.message,
        fix_suggestion: "Confirm the test Supabase project has Email provider enabled and 'Confirm email' turned OFF.",
      });
      return { ok: false, error_message: signUpResult.error.message };
    }
    if (!signUpResult.data.session) {
      // No session despite no error → email confirmation is still ON.
      logger.error("test_auth_sign_up_no_session", {
        fix_suggestion: "Disable 'Confirm email' in Supabase Auth settings so test sign-up returns a session.",
      });
      return {
        ok: false,
        error_message: "Test sign-up needs email confirmation disabled in Supabase.",
      };
    }
    logger.info("test_auth_sign_in_completed", { created: true });
    return { ok: true };
  } catch (exc) {
    const error_message = exc instanceof Error ? exc.message : "Unknown error during test sign-in.";
    logger.error("test_auth_sign_in_failed", {
      error_message,
      fix_suggestion: "Check network connectivity and the NEXT_PUBLIC_SUPABASE_* env vars.",
    });
    return { ok: false, error_message };
  }
}

/**
 * Read the current authenticated session, if any.
 *
 * Used by the magic-link callback page to confirm `detectSessionInUrl` has
 * established a session on mount.
 *
 * @param client - Optional Supabase client (defaults to the shared browser client).
 * @returns The active {@link Session}, or `null` when signed out.
 */
export async function getCurrentSession(client: SupabaseClient = getSupabaseBrowserClient()): Promise<Session | null> {
  const { data } = await client.auth.getSession();
  return data.session;
}

/** Result of a sign-out request: a discriminated union the UI switches on. */
export type SignOutResult = { ok: true } | { ok: false; error_message: string };

/**
 * Sign the current user out and clear the persisted session.
 *
 * On success supabase-js clears the stored session and fires
 * `onAuthStateChange`; the caller is responsible for routing back to the
 * onboarding flow.
 *
 * @param client - Optional Supabase client (injected in tests; defaults to the
 *   shared browser client).
 * @returns `{ ok: true }` once signed out, else `{ ok: false, error_message }`.
 *
 * @example
 * const result = await signOut();
 * if (result.ok) {
 *   router.replace("/onboarding");
 * }
 */
export async function signOut(client: SupabaseClient = getSupabaseBrowserClient()): Promise<SignOutResult> {
  logger.info("sign_out_started", {});
  try {
    const { error } = await client.auth.signOut();
    if (error) {
      logger.error("sign_out_failed", {
        error_message: error.message,
        fix_suggestion: "Check network connectivity; a stale local session can be cleared by reinstalling the app.",
      });
      return { ok: false, error_message: error.message };
    }
    logger.info("sign_out_completed", {});
    return { ok: true };
  } catch (exc) {
    const error_message = exc instanceof Error ? exc.message : "Unknown error signing out.";
    logger.error("sign_out_failed", {
      error_message,
      fix_suggestion: "Check network connectivity and the NEXT_PUBLIC_SUPABASE_* env vars.",
    });
    return { ok: false, error_message };
  }
}
