/**
 * Content-source data layer (Phase 5b SP4) — the typed, client-side Supabase
 * reads/writes for the source-axis catalog (migration `0009_content_sources.sql`).
 *
 * **Same client pattern as `src/lib/follows.ts` + `src/lib/entities.ts`:** every
 * exported fn takes an optional `client` (injected in tests) defaulting to the
 * shared browser anon client — there is NO Next.js server runtime on device
 * (Capacitor static export), so the REST surface ships as client-side Supabase
 * reads under RLS.
 *
 * Reads of `content_sources` are PUBLIC (anon SELECT via the
 * `content_sources_public_read` policy — anon onboarding browses the catalog).
 * Writes/reads of `user_content_sources` are OWNER-SCOPED: the
 * `user_content_sources_owner_all` policy (migration 0009) pins every row to
 * `auth.uid()`, so a caller can only ever touch their own follows. We
 * additionally resolve the user id app-side so an UNAUTHENTICATED follow throws
 * a loud, actionable error (Rule 12) rather than relying on RLS to reject an
 * anon write with an opaque PostgREST message.
 *
 * Per CLAUDE.md C3, this is follow-as-SOURCE (`user_content_sources` →
 * ingestion), distinct from follow-as-FILTER (`follows` → ranking) in
 * `src/lib/follows.ts`.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { ContentSource, ContentSourceType, SourcePriority, UserContentSource } from "@/types/source";

/** The `content_sources` table name — single source of truth for the table ref. */
const CONTENT_SOURCES_TABLE = "content_sources";

/** The `user_content_sources` follow-junction table name. */
const USER_CONTENT_SOURCES_TABLE = "user_content_sources";

/**
 * The exact `content_sources` column projection {@link listSourcesByArchetype}
 * requests — every field of {@link ContentSource}, in DDL order. Keeping it
 * explicit (not `*`) pins the row shape to the type and keeps the payload minimal.
 */
const CONTENT_SOURCE_COLUMNS =
  "source_id,content_source_type,external_id,source_name,source_description,thumbnail_url," +
  "subscriber_count,platform_metadata,personas,topic_tags,popularity_score,is_curated,last_fetched_at";

/** The `user_content_sources` column projection {@link getUserSources} requests. */
const USER_CONTENT_SOURCE_COLUMNS = "user_id,source_id,source_priority,added_via";

/** Default page size for the catalog browse (mirrors `entities.ts` `DEFAULT_LIMIT`). */
const DEFAULT_LIMIT = 20;

/** The follow default: a freshly-followed source ingests everything until tuned. */
const DEFAULT_SOURCE_PRIORITY: SourcePriority = "everything";

/**
 * Resolve the authed user's id, or throw a loud, actionable error when signed
 * out. Unlike `follows.ts` (where follow degrades to a no-op on the anon path),
 * a source follow is an explicit authed action with no graceful no-op semantics
 * — surfacing the unauth state (Rule 12) beats a silent miss or an opaque RLS
 * rejection.
 *
 * @param client - The Supabase client to read the session from.
 * @returns The authed `user_id` (= `auth.uid()`).
 * @throws If unauthenticated (or the session read fails) — never returns null.
 */
async function requireAuthedUserId(client: SupabaseClient): Promise<string> {
  const { data, error } = await client.auth.getUser();
  if (error || !data.user) {
    logger.error("source_follow_requires_auth", {
      error_message: error?.message ?? "no_active_session",
      fix_suggestion: "Sign in (email magic-link) before following a source — anon users have no follow rows.",
    });
    throw new Error(
      "Cannot mutate source follows while signed out. " +
        "fix_suggestion: sign in (email magic-link) before following a source.",
    );
  }
  return data.user.id;
}

/**
 * Browse the public catalog for sources matching a set of archetype personas on a
 * single axis, ranked by popularity (spec §2.2/§3.2; powers the 5c onboarding
 * recommendation grid).
 *
 * Filters `content_sources` where `personas && $personas` (ANY-overlap, served by
 * the `idx_content_sources_personas` GIN index) AND `content_source_type = kind`,
 * orders by `popularity_score desc` (served by `idx_content_sources_popularity`),
 * and limits the result. An empty `personas` array can never overlap a
 * `NOT NULL` `personas text[]`, so it short-circuits to `[]` without a round-trip.
 *
 * @param personas - Archetype slugs to match (ANY-overlap). Empty → `[]`.
 * @param kind - The single source axis to browse ({@link ContentSourceType}).
 * @param limit - Max rows to return (default {@link DEFAULT_LIMIT}).
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The matching {@link ContentSource} rows, popularity-desc ordered.
 * @throws If the query fails (errors are surfaced, never swallowed — Rule 12).
 *
 * @example
 * const channels = await listSourcesByArchetype(["ai-frontier-tech"], "youtube_channel", 12);
 * channels[0].source_name; // the most popular AI channel for that archetype
 */
export async function listSourcesByArchetype(
  personas: string[],
  kind: ContentSourceType,
  limit: number = DEFAULT_LIMIT,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<ContentSource[]> {
  logger.info("list_sources_by_archetype_started", { personas, kind, limit });

  // An empty persona set overlaps nothing — short-circuit (a `&& '{}'` would
  // match zero rows anyway, but skipping the round-trip is cheaper and clearer).
  if (personas.length === 0) {
    logger.info("list_sources_by_archetype_completed", { kind, returned: 0, reason: "empty_personas" });
    return [];
  }

  const { data, error } = await client
    .from(CONTENT_SOURCES_TABLE)
    .select(CONTENT_SOURCE_COLUMNS)
    .overlaps("personas", personas)
    .eq("content_source_type", kind)
    .order("popularity_score", { ascending: false })
    .limit(limit)
    .returns<ContentSource[]>();

  if (error) {
    logger.error("list_sources_by_archetype_failed", {
      kind,
      error_message: error.message,
      fix_suggestion: "Confirm migration 0009 applied, the catalog seeder ran, and content_sources allows anon SELECT.",
    });
    throw new Error(
      `Failed to list ${kind} sources for personas [${personas.join(", ")}]: ${error.message}. ` +
        "fix_suggestion: confirm migration 0009 applied and content_sources is readable.",
    );
  }

  const rows = data ?? [];
  logger.info("list_sources_by_archetype_completed", { kind, returned: rows.length });
  return rows;
}

/**
 * Read the authed user's whole follow set from `user_content_sources` (RLS
 * returns only the caller's rows). Used to hydrate the control surface / drive
 * ingestion.
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The caller's {@link UserContentSource} follow rows (only their own — RLS).
 * @throws If unauthenticated, or if the query fails (surfaced, never swallowed — Rule 12).
 *
 * @example
 * const follows = await getUserSources();
 * follows.every((row) => row.user_id === myUserId); // true — RLS scopes to auth.uid()
 */
export async function getUserSources(
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<UserContentSource[]> {
  const authedUserId = await requireAuthedUserId(client);
  logger.info("get_user_sources_started", { user_id: authedUserId });

  // Reason: the owner predicate is redundant with RLS (the policy already pins
  // rows to auth.uid()), but pinning it explicitly mirrors follows.ts and makes
  // the owner-scoping legible at the call site (defense in depth, not reliance).
  const { data, error } = await client
    .from(USER_CONTENT_SOURCES_TABLE)
    .select(USER_CONTENT_SOURCE_COLUMNS)
    .eq("user_id", authedUserId)
    .returns<UserContentSource[]>();

  if (error) {
    logger.error("get_user_sources_failed", {
      error_message: error.message,
      fix_suggestion:
        "Confirm migration 0009 applied and the user_content_sources_owner_all RLS policy allows the authed SELECT.",
    });
    throw new Error(
      `Failed to read user content sources: ${error.message}. ` +
        "fix_suggestion: confirm migration 0009 applied and user_content_sources allows the authed SELECT.",
    );
  }

  const rows = data ?? [];
  logger.info("get_user_sources_completed", { user_id: authedUserId, returned: rows.length });
  return rows;
}

/**
 * Follow a source for the authed user: upsert one owner-scoped
 * `user_content_sources` row. Idempotent against the `(user_id, source_id)` PK —
 * re-following an already-followed source is a no-op write (no duplicate row).
 *
 * A fresh follow defaults to `priority='everything'` (ingest all) — the product
 * default until the user tunes it in the control surface (5e).
 *
 * @param sourceId - The `content_sources.source_id` to follow.
 * @param priority - The initial 3-state priority (default {@link DEFAULT_SOURCE_PRIORITY} = `everything`).
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns Nothing on success.
 * @throws If unauthenticated, or if the upsert fails (surfaced, never swallowed — Rule 12).
 *
 * @example
 * await followSource("src-uuid-1"); // follows at priority 'everything'
 * await followSource("src-uuid-2", "big_stuff"); // follows muted-to-highlights
 */
export async function followSource(
  sourceId: string,
  priority: SourcePriority = DEFAULT_SOURCE_PRIORITY,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<void> {
  const authedUserId = await requireAuthedUserId(client);
  logger.info("follow_source_started", { user_id: authedUserId, source_id: sourceId, priority });

  // Upsert on the (user_id, source_id) PK → idempotent re-follow. The inserted
  // row pins user_id to the authed id (also enforced by the owner-all RLS
  // WITH CHECK), and added_via records the follow origin for 5c/5e analytics.
  const { error } = await client.from(USER_CONTENT_SOURCES_TABLE).upsert(
    {
      user_id: authedUserId,
      source_id: sourceId,
      source_priority: priority,
      added_via: "data_layer",
    },
    { onConflict: "user_id,source_id" },
  );

  if (error) {
    logger.error("follow_source_failed", {
      source_id: sourceId,
      error_message: error.message,
      fix_suggestion:
        "Confirm migration 0009 applied and the user_content_sources_owner_all RLS policy allows the authed INSERT.",
    });
    throw new Error(
      `Failed to follow source "${sourceId}": ${error.message}. ` +
        "fix_suggestion: confirm migration 0009 applied and user_content_sources allows the authed upsert.",
    );
  }

  logger.info("follow_source_completed", { user_id: authedUserId, source_id: sourceId, priority });
}

/**
 * Unfollow a source for the authed user: delete the caller's
 * `user_content_sources` row. A no-op (no error) when no row exists — unfollowing
 * an unfollowed source is harmless. The delete filters on `user_id` (also pinned
 * by RLS), so a caller can never delete another user's follow.
 *
 * @param sourceId - The `content_sources.source_id` to unfollow.
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns Nothing on success.
 * @throws If unauthenticated, or if the delete fails (surfaced, never swallowed — Rule 12).
 *
 * @example
 * await unfollowSource("src-uuid-1"); // removes the follow row
 */
export async function unfollowSource(
  sourceId: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<void> {
  const authedUserId = await requireAuthedUserId(client);
  logger.info("unfollow_source_started", { user_id: authedUserId, source_id: sourceId });

  const { error } = await client
    .from(USER_CONTENT_SOURCES_TABLE)
    .delete()
    .eq("user_id", authedUserId)
    .eq("source_id", sourceId);

  if (error) {
    logger.error("unfollow_source_failed", {
      source_id: sourceId,
      error_message: error.message,
      fix_suggestion:
        "Confirm migration 0009 applied and the user_content_sources_owner_all RLS policy allows the authed DELETE.",
    });
    throw new Error(
      `Failed to unfollow source "${sourceId}": ${error.message}. ` +
        "fix_suggestion: confirm migration 0009 applied and user_content_sources allows the authed DELETE.",
    );
  }

  logger.info("unfollow_source_completed", { user_id: authedUserId, source_id: sourceId });
}

/**
 * Set the 3-state ingestion priority on the authed user's existing follow row
 * (the control-surface dial, 5e). Updates `source_priority` on the caller's
 * `(user_id, source_id)` row — a no-op (no error) when no follow row exists.
 *
 * @param sourceId - The `content_sources.source_id` whose follow to re-prioritize.
 * @param priority - The new {@link SourcePriority} enum value (`off|big_stuff|everything`).
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns Nothing on success.
 * @throws If unauthenticated, or if the update fails (surfaced, never swallowed — Rule 12).
 *
 * @example
 * await setSourcePriority("src-uuid-1", "big_stuff"); // mute to highlights-only
 * await setSourcePriority("src-uuid-1", "off"); // followed but fully muted
 */
export async function setSourcePriority(
  sourceId: string,
  priority: SourcePriority,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<void> {
  const authedUserId = await requireAuthedUserId(client);
  logger.info("set_source_priority_started", { user_id: authedUserId, source_id: sourceId, priority });

  const { error } = await client
    .from(USER_CONTENT_SOURCES_TABLE)
    .update({ source_priority: priority })
    .eq("user_id", authedUserId)
    .eq("source_id", sourceId);

  if (error) {
    logger.error("set_source_priority_failed", {
      source_id: sourceId,
      priority,
      error_message: error.message,
      fix_suggestion:
        "Confirm migration 0009 applied and the user_content_sources_owner_all RLS policy allows the authed UPDATE.",
    });
    throw new Error(
      `Failed to set priority "${priority}" on source "${sourceId}": ${error.message}. ` +
        "fix_suggestion: confirm migration 0009 applied and user_content_sources allows the authed UPDATE.",
    );
  }

  logger.info("set_source_priority_completed", { user_id: authedUserId, source_id: sourceId, priority });
}
