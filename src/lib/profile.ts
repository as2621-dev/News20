/**
 * User-profile persistence for the Settings surface (migration 0012).
 *
 * One owner-scoped row per auth user in `user_profiles` holding the editable
 * display name shown in the Settings header / Name row. This module is the
 * ONLY client-side reader/writer of `user_profiles`.
 *
 * **Same client pattern as `src/lib/follows.ts`:** every exported fn takes an
 * optional `client` (injected in tests) defaulting to the shared browser anon
 * client. Reads/writes are RLS-scoped to the authed user (`user_profiles_owner_all`),
 * and the user id is also resolved app-side so an unauthenticated caller
 * degrades gracefully (no throw, no row).
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** The `user_profiles` table name — single source of truth for table refs. */
const USER_PROFILES_TABLE = "user_profiles";

/** Max display-name length — MUST match ck_profile_display_name_length (0012). */
export const PROFILE_DISPLAY_NAME_MAX_LENGTH = 80;

/** Result of a display-name save: ok, or a user-presentable failure. */
export type SaveDisplayNameResult = { ok: true } | { ok: false; error_message: string };

/**
 * Resolve the authed user's id, or `null` when signed out.
 *
 * @param client - The Supabase client to read the session from.
 * @returns The authed `user_id` (= `auth.uid()`), or `null` if unauthenticated.
 */
async function resolveAuthedUserId(client: SupabaseClient): Promise<string | null> {
  const { data, error } = await client.auth.getUser();
  if (error || !data.user) {
    return null;
  }
  return data.user.id;
}

/**
 * Read the authed user's saved display name.
 *
 * Returns `null` when signed out, when no profile row exists yet (the caller
 * falls back to deriving a name from the email), or on a read error — Settings
 * stays usable either way.
 *
 * @param client - Optional Supabase client (injected in tests; defaults to the
 *   shared browser anon client).
 * @returns The saved display name, or `null`.
 *
 * @example
 * const savedName = await getProfileDisplayName(); // "Riya Sharma" | null
 */
export async function getProfileDisplayName(
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<string | null> {
  const authedUserId = await resolveAuthedUserId(client);
  if (!authedUserId) {
    return null;
  }

  const { data, error } = await client
    .from(USER_PROFILES_TABLE)
    .select("profile_display_name")
    .eq("profile_user_id", authedUserId)
    .maybeSingle<{ profile_display_name: string }>();

  if (error) {
    // Reason: best-effort read; Settings falls back to the email-derived name
    // rather than crashing the surface.
    logger.error("profile_name_read_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm migration 0012 applied and user_profiles_owner_all allows the authed SELECT.",
    });
    return null;
  }

  return data?.profile_display_name ?? null;
}

/**
 * Save (upsert) the authed user's display name.
 *
 * Trims the input and rejects empty / over-length names app-side (mirroring the
 * `ck_profile_display_name_length` DB check) so the user gets a readable error
 * instead of a constraint violation. A signed-out caller writes nothing.
 *
 * @param displayName - The new display name (will be trimmed).
 * @param client - Optional Supabase client (injected in tests).
 * @returns `{ ok: true }` on success; `{ ok: false, error_message }` otherwise.
 *
 * @example
 * const result = await saveProfileDisplayName("Riya Sharma");
 * if (!result.ok) showError(result.error_message);
 */
export async function saveProfileDisplayName(
  displayName: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<SaveDisplayNameResult> {
  const trimmedDisplayName = displayName.trim();
  if (trimmedDisplayName.length === 0) {
    return { ok: false, error_message: "Name can't be empty." };
  }
  if (trimmedDisplayName.length > PROFILE_DISPLAY_NAME_MAX_LENGTH) {
    return { ok: false, error_message: `Name must be ${PROFILE_DISPLAY_NAME_MAX_LENGTH} characters or fewer.` };
  }

  const authedUserId = await resolveAuthedUserId(client);
  if (!authedUserId) {
    logger.warn("profile_name_save_skipped_unauthenticated", {
      fix_suggestion: "Sign in before editing the profile name — anon users have no profile row.",
    });
    return { ok: false, error_message: "Sign in to edit your name." };
  }

  const { error } = await client.from(USER_PROFILES_TABLE).upsert(
    {
      profile_user_id: authedUserId,
      profile_display_name: trimmedDisplayName,
      profile_updated_at: new Date().toISOString(),
    },
    { onConflict: "profile_user_id" },
  );

  if (error) {
    logger.error("profile_name_save_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm migration 0012 applied and user_profiles_owner_all allows the authed UPSERT.",
    });
    return { ok: false, error_message: "Couldn't save your name — try again." };
  }

  logger.info("profile_name_saved", { name_length: trimmedDisplayName.length });
  return { ok: true };
}
