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

/** Result of a magic-link request: a discriminated union the UI switches on. */
export type SendMagicLinkResult = { ok: true } | { ok: false; error_message: string };

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
