/**
 * Follow persistence (Phase 3d SP3) for the audio-first reel.
 *
 * A "follow" is a persistent, owner-scoped row in the `follows` table that says
 * "more of this story's subniche in tomorrow's feed". The daily profile-update
 * job (SP2) reads this set and boosts the followed story's matched interest
 * node(s). This module is the ONLY client-side writer/reader of `follows`.
 *
 * **Same client pattern as `src/lib/feed/supabaseFeed.ts`:** every exported fn
 * takes an optional `client` (injected in tests) defaulting to the shared
 * browser anon client. Writes/reads are RLS-scoped to the authed user — the
 * `follows_owner_all` policy (migration 0005) pins every row to `auth.uid()`, so
 * a caller can only ever touch their own follows. We additionally resolve the
 * user id app-side so an UNAUTHENTICATED caller degrades gracefully (no throw,
 * no row) rather than relying on RLS to reject an anon write.
 *
 * The `follow_story_id` column is `text` and references `stories.story_id`
 * (migration 0001); the reel passes that story id (carried on `Story.digest_id`,
 * which `supabaseFeed.mapStoryRow` sets to `story_id`).
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** The `follows` table name — single source of truth for the column/table refs. */
const FOLLOWS_TABLE = "follows";

/**
 * Resolve the authed user's id, or `null` when signed out.
 *
 * Wraps `auth.getUser()` so callers can degrade gracefully on the no-session
 * path (the reel is usable signed-out; follow simply becomes a no-op then).
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
 * Read the set of `story_id`s the authed user currently follows.
 *
 * A single batched read used to HYDRATE the reel's followed-state on feed load
 * (one round-trip beats N `isFollowing` calls). Returns an empty set when signed
 * out — the reel then shows nothing as followed, which is correct.
 *
 * @param client - Optional Supabase client (injected in tests; defaults to the
 *   shared browser anon client).
 * @returns A set of followed `story_id`s (empty when unauthenticated or on error).
 *
 * @example
 * const followed = await getFollowedStoryIds();
 * const isS1Followed = followed.has("s1");
 */
export async function getFollowedStoryIds(client: SupabaseClient = getSupabaseBrowserClient()): Promise<Set<string>> {
  const authedUserId = await resolveAuthedUserId(client);
  if (!authedUserId) {
    return new Set<string>();
  }

  const { data, error } = await client
    .from(FOLLOWS_TABLE)
    .select("follow_story_id")
    .eq("follow_user_id", authedUserId)
    .returns<{ follow_story_id: string }[]>();

  if (error) {
    // Reason: hydration is best-effort; a failed read should leave the reel
    // usable (nothing shown as followed) rather than crash the home surface.
    logger.error("follows_hydrate_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm migration 0005 applied and the follows_owner_all RLS policy allows the authed SELECT.",
    });
    return new Set<string>();
  }

  return new Set<string>((data ?? []).map((row) => row.follow_story_id));
}

/**
 * Check whether the authed user currently follows a single story.
 *
 * Prefer {@link getFollowedStoryIds} for hydrating the whole reel; this is the
 * single-story read for callers that only need one. Returns `false` when signed
 * out or on error.
 *
 * @param storyId - The `stories.story_id` to check.
 * @param client - Optional Supabase client (injected in tests).
 * @returns `true` if a `follows` row exists for (authed user, story), else `false`.
 *
 * @example
 * const isFollowed = await isFollowing("s1");
 */
export async function isFollowing(
  storyId: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<boolean> {
  const authedUserId = await resolveAuthedUserId(client);
  if (!authedUserId) {
    return false;
  }

  const { data, error } = await client
    .from(FOLLOWS_TABLE)
    .select("follow_id")
    .eq("follow_user_id", authedUserId)
    .eq("follow_story_id", storyId)
    .maybeSingle();

  if (error) {
    logger.error("follows_is_following_failed", {
      story_id: storyId,
      error_message: error.message,
      fix_suggestion: "Confirm migration 0005 applied and the follows_owner_all RLS policy allows the authed SELECT.",
    });
    return false;
  }

  return data !== null;
}

/**
 * Toggle the authed user's follow on a story: insert if absent, delete if
 * present. Returns the NEW followed state (`true` = now following).
 *
 * Idempotent against the `uq_follow_user_story` unique constraint — a duplicate
 * insert is a no-op the caller treats as "still following". Returns `false`
 * (and writes nothing) when signed out, so the UI can degrade gracefully.
 *
 * Implementation reads the current state first (so we know which way to flip),
 * then applies the inverse. Both writes are owner-scoped: the inserted row pins
 * `follow_user_id` to the authed id (also enforced by `follows_owner_all` RLS),
 * and the delete filters on it, so a caller can never toggle another user's row.
 *
 * @param storyId - The `stories.story_id` to follow/unfollow.
 * @param client - Optional Supabase client (injected in tests).
 * @returns The new followed state: `true` if now following, `false` if not
 *   (including the unauthenticated / write-failure cases).
 *
 * @example
 * const nowFollowing = await toggleFollow("s1"); // true on first tap
 * const stillFollowing = await toggleFollow("s1"); // false on the second tap
 */
export async function toggleFollow(
  storyId: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<boolean> {
  const authedUserId = await resolveAuthedUserId(client);
  if (!authedUserId) {
    // Reason: follow is an authed surface; signed out we no-op (no crash) and
    // report "not following" so the optimistic UI reconciles back to off.
    logger.warn("follow_toggle_skipped_unauthenticated", {
      story_id: storyId,
      fix_suggestion: "Sign in (email magic-link) before following — anon users have no follows row.",
    });
    return false;
  }

  const currentlyFollowing = await isFollowing(storyId, client);

  if (currentlyFollowing) {
    const { error } = await client
      .from(FOLLOWS_TABLE)
      .delete()
      .eq("follow_user_id", authedUserId)
      .eq("follow_story_id", storyId);
    if (error) {
      logger.error("follow_unfollow_failed", {
        story_id: storyId,
        error_message: error.message,
        fix_suggestion: "Confirm the follows_owner_all RLS policy allows the authed DELETE.",
      });
      // Reason: delete failed → still following; report the unchanged state so
      // the optimistic UI reconciles back to "on".
      return true;
    }
    logger.info("follow_removed", { story_id: storyId });
    return false;
  }

  const { error } = await client.from(FOLLOWS_TABLE).insert({ follow_user_id: authedUserId, follow_story_id: storyId });
  if (error) {
    logger.error("follow_add_failed", {
      story_id: storyId,
      error_message: error.message,
      fix_suggestion: "Confirm migration 0005 applied and the follows_owner_all RLS policy allows the authed INSERT.",
    });
    // Reason: insert failed → not following; report the unchanged state so the
    // optimistic UI reconciles back to "off".
    return false;
  }
  logger.info("follow_added", { story_id: storyId });
  return true;
}
