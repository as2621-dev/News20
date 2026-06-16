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
import type { Archetype, ContentSource, ContentSourceType, SourcePriority, UserContentSource } from "@/types/source";

/** The `content_sources` table name — single source of truth for the table ref. */
const CONTENT_SOURCES_TABLE = "content_sources";

/** The `user_content_sources` follow-junction table name. */
const USER_CONTENT_SOURCES_TABLE = "user_content_sources";

/** The public-read `archetypes` reference table name (migration 0009). */
const ARCHETYPES_TABLE = "archetypes";

/**
 * The exact `archetypes` column projection {@link getArchetypes} requests — every
 * field of {@link Archetype}, pinning the row shape to the type (not `*`).
 */
const ARCHETYPE_COLUMNS = "archetype_id,archetype_slug,archetype_label,archetype_vector";

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
 * Read the whole public-read `archetypes` reference catalog (migration 0009 seed,
 * `supabase/seed/archetypes.sql`). The 5c recommendation flow feeds these rows to
 * the PURE {@link mapToArchetype} (SP1), which scores the user's rolled-up
 * interest vector against each `archetype_vector` and returns the nearest match.
 *
 * Reads are anon-PUBLIC (the `archetypes_public_read` policy, migration 0009), so
 * an anon onboarding browse loads them without a session — same posture as the
 * {@link listSourcesByArchetype} catalog read. The 12-row draft set is tiny, so
 * this is an unfiltered, unpaged read (no limit needed). Errors surface (Rule 12).
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns Every {@link Archetype} row (slug + label + 8-key vector) for the matcher.
 * @throws If the query fails (errors are surfaced, never swallowed — Rule 12).
 *
 * @example
 * const archetypes = await getArchetypes();
 * const match = mapToArchetype(userVector, archetypes); // SP1 — nearest archetype
 */
export async function getArchetypes(client: SupabaseClient = getSupabaseBrowserClient()): Promise<Archetype[]> {
  logger.info("get_archetypes_started", {});

  const { data, error } = await client.from(ARCHETYPES_TABLE).select(ARCHETYPE_COLUMNS).returns<Archetype[]>();

  if (error) {
    logger.error("get_archetypes_failed", {
      error_message: error.message,
      fix_suggestion:
        "Confirm migration 0009 applied, supabase/seed/archetypes.sql ran, and archetypes allows anon SELECT.",
    });
    throw new Error(
      `Failed to read archetypes: ${error.message}. ` +
        "fix_suggestion: confirm migration 0009 applied and the archetypes seed ran.",
    );
  }

  const rows = data ?? [];
  logger.info("get_archetypes_completed", { returned: rows.length });
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
 * Read the authed user's followed sources joined with their catalog details, for
 * display (the Settings "Sources you're following" list). Composes
 * {@link getUserSources} (the owner-scoped follow junction) with a public-read
 * {@link CONTENT_SOURCES_TABLE} fetch of those `source_id`s, dropping any
 * `priority='off'` (muted) follow and ordering by `popularity_score desc` to match
 * the catalog browse.
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The followed {@link ContentSource} rows (name, avatar, type, tags).
 * @throws If unauthenticated or either read fails (surfaced — Rule 12).
 *
 * @example
 * const mine = await getFollowedSources();
 * mine[0].source_name; // the most popular source the user follows
 */
export async function getFollowedSources(
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<ContentSource[]> {
  const follows = await getUserSources(client);
  const followedIds = follows.filter((follow) => follow.source_priority !== "off").map((follow) => follow.source_id);
  if (followedIds.length === 0) {
    logger.info("get_followed_sources_completed", { returned: 0, reason: "no_active_follows" });
    return [];
  }

  const { data, error } = await client
    .from(CONTENT_SOURCES_TABLE)
    .select(CONTENT_SOURCE_COLUMNS)
    .in("source_id", followedIds)
    .order("popularity_score", { ascending: false })
    .returns<ContentSource[]>();

  if (error) {
    logger.error("get_followed_sources_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm migration 0009 applied and content_sources allows the read.",
    });
    throw new Error(
      `Failed to read followed sources: ${error.message}. ` +
        "fix_suggestion: confirm migration 0009 applied and content_sources is readable.",
    );
  }

  const rows = data ?? [];
  logger.info("get_followed_sources_completed", { returned: rows.length });
  return rows;
}

/** A followed source joined with its 3-state ingestion priority (for the Sources surface). */
export interface FollowedSourceWithPriority extends ContentSource {
  /** The follow's `source_priority` — `off` renders as "Paused", anything else as "Active". */
  source_priority: SourcePriority;
}

/**
 * Read the authed user's followed sources joined with their catalog details AND
 * their follow priority — UNLIKE {@link getFollowedSources}, this KEEPS `off`
 * (paused) follows so the "Sources · What you follow" surface can render them with
 * a working active/paused toggle. Composes {@link getUserSources} (the priorities)
 * with a public-read catalog fetch of every followed `source_id`.
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The followed sources (incl. paused) with their `source_priority`, popularity-ordered.
 * @throws If unauthenticated or either read fails (surfaced — Rule 12).
 *
 * @example
 * const mine = await getFollowedSourcesWithPriority();
 * mine.filter((s) => s.source_priority !== "off"); // the active ones
 */
export async function getFollowedSourcesWithPriority(
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<FollowedSourceWithPriority[]> {
  const follows = await getUserSources(client);
  if (follows.length === 0) {
    return [];
  }
  const priorityBySourceId = new Map<string, SourcePriority>(
    follows.map((follow) => [follow.source_id, follow.source_priority]),
  );

  const { data, error } = await client
    .from(CONTENT_SOURCES_TABLE)
    .select(CONTENT_SOURCE_COLUMNS)
    .in("source_id", [...priorityBySourceId.keys()])
    .order("popularity_score", { ascending: false })
    .returns<ContentSource[]>();

  if (error) {
    logger.error("get_followed_sources_with_priority_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm migration 0009 applied and content_sources allows the read.",
    });
    throw new Error(
      `Failed to read followed sources: ${error.message}. ` +
        "fix_suggestion: confirm migration 0009 applied and content_sources is readable.",
    );
  }

  return (data ?? []).map((source) => ({
    ...source,
    source_priority: priorityBySourceId.get(source.source_id) ?? DEFAULT_SOURCE_PRIORITY,
  }));
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
 * The catalog-row fields a user-added (non-curated) source carries when promoted
 * from a live search result into `content_sources`. Mirrors the worker's
 * `WorkerSourceSearchResult` (`src/lib/sourceSearch.ts`) so the future
 * search-and-add modal can pass a search hit straight through.
 */
export interface UserAddedSourceInput {
  /** The axis this source lives on ({@link ContentSourceType}). */
  content_source_type: ContentSourceType;
  /** The platform id (YouTube channel id, `itunes-{id}`, lower-cased `@handle`). */
  external_id: string;
  /** The display name. */
  source_name: string;
  /** Optional blurb (channel/episode description, or the `@handle`). */
  source_description?: string | null;
  /** Optional avatar/artwork URL. */
  thumbnail_url?: string | null;
  /** Optional subscriber/follower count when the provider exposes it. */
  subscriber_count?: number | null;
  /**
   * X-only: `true` when the `@handle` could NOT be resolved by the live X resolver
   * and is stored as a PENDING free-text follow. Phase 5d enriches/polls it later.
   * Persisted as `platform_metadata.is_pending` so the ingestion seam can find it.
   */
  is_pending?: boolean;
}

/** What {@link upsertUserAddedSource} resolves to — the catalog id it followed. */
export interface UserAddedSourceResult {
  /** The `content_sources.source_id` the source upserted/resolved to (now followed). */
  source_id: string;
  /** True when this call inserted a NEW catalog row (vs. matched an existing one). */
  was_inserted: boolean;
}

/**
 * The `content_sources` column the upsert dedups on — the `(content_source_type,
 * external_id)` unique constraint (`uq_content_source_type_external`, migration
 * 0009). Re-adding the same external id resolves to the existing row, not a dupe.
 */
const CONTENT_SOURCE_UPSERT_CONFLICT = "content_source_type,external_id";

/**
 * Promote a NON-curated source (from a live worker search result) into the catalog
 * and follow it for the authed user — the SP3a search-and-add hand-off.
 *
 * Search results carry only an `external_id` (the platform id), but
 * {@link followSource} needs a catalog `source_id`. This bridges the gap:
 *
 *  1. Upsert a `content_sources` row with `is_curated=false`, dedup-keyed on the
 *     `(content_source_type, external_id)` unique constraint — so re-adding the
 *     SAME `external_id` resolves to the EXISTING row instead of duplicating it
 *     (idempotent). The upsert RETURNS the row so we recover its `source_id`
 *     without a second round-trip.
 *  2. {@link followSource} the resolved `source_id` for the authed user (RLS-scoped
 *     owner write, idempotent on the `(user_id, source_id)` PK, default priority).
 *
 * A PENDING `x_account` (an `@handle` the live X resolver could not resolve) is
 * stored with `platform_metadata.is_pending = true` so Phase 5d's ingestion can
 * find and enrich/poll it later (SP3a hand-off gap #2).
 *
 * RLS-safe: `content_sources` has NO anon/authed write policy (only the
 * service-role bypasses RLS), so an UNAUTHENTICATED caller is rejected app-side
 * FIRST (loud, actionable — Rule 12) before any write. An authed user-added row is
 * a shared-catalog insert under the authed key; the per-user FOLLOW that follows
 * is owner-scoped.
 *
 * @param input - The {@link UserAddedSourceInput} (a search hit's fields).
 * @param priority - Initial follow priority (default `everything` via {@link followSource}).
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The {@link UserAddedSourceResult} — the followed `source_id` (+ insert flag).
 * @throws If unauthenticated, or if the catalog upsert / follow fails (surfaced — Rule 12).
 *
 * @example
 * // Add a searched channel not in the catalog, then follow it:
 * const { source_id } = await upsertUserAddedSource({
 *   content_source_type: "youtube_channel",
 *   external_id: "UC_xyz",
 *   source_name: "Some Indie Channel",
 * });
 *
 * @example
 * // Follow an unresolved @handle → stored pending for Phase 5d enrichment:
 * await upsertUserAddedSource({
 *   content_source_type: "x_account",
 *   external_id: "@somehandle",
 *   source_name: "@somehandle",
 *   is_pending: true,
 * });
 */
export async function upsertUserAddedSource(
  input: UserAddedSourceInput,
  priority: SourcePriority = DEFAULT_SOURCE_PRIORITY,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<UserAddedSourceResult> {
  // Resolve auth BEFORE any write — a user-added follow is an authed action; reject
  // the anon path loudly here rather than leaning on an opaque RLS rejection.
  const authedUserId = await requireAuthedUserId(client);
  logger.info("upsert_user_added_source_started", {
    user_id: authedUserId,
    content_source_type: input.content_source_type,
    external_id: input.external_id,
    is_pending: input.is_pending === true,
  });

  // Reason: a pending x_account marks platform_metadata.is_pending so Phase 5d can
  // find unresolved handles to enrich/poll. Non-pending rows carry no marker (null
  // metadata) — we only attach the flag when it is meaningfully `true`.
  const platformMetadata = input.is_pending === true ? { is_pending: true } : null;

  const { data, error } = await client
    .from(CONTENT_SOURCES_TABLE)
    .upsert(
      {
        content_source_type: input.content_source_type,
        external_id: input.external_id,
        source_name: input.source_name,
        source_description: input.source_description ?? null,
        thumbnail_url: input.thumbnail_url ?? null,
        subscriber_count: input.subscriber_count ?? null,
        platform_metadata: platformMetadata,
        // A user-added source is NOT part of the curated catalog (5c recommendation
        // grids read curated rows; this row exists to anchor the follow + ingestion).
        is_curated: false,
      },
      { onConflict: CONTENT_SOURCE_UPSERT_CONFLICT },
    )
    .select("source_id")
    .single<{ source_id: string }>();

  if (error || !data) {
    logger.error("upsert_user_added_source_failed", {
      external_id: input.external_id,
      error_message: error?.message ?? "no_row_returned",
      fix_suggestion:
        "Confirm migration 0009 applied, the (content_source_type, external_id) unique constraint exists, " +
        "and content_sources permits the authed upsert (service-role/policy).",
    });
    throw new Error(
      `Failed to upsert user-added source "${input.external_id}": ${error?.message ?? "no row returned"}. ` +
        "fix_suggestion: confirm migration 0009 applied and content_sources permits the upsert.",
    );
  }

  // Now follow the resolved catalog row (owner-scoped, idempotent on the PK).
  await followSource(data.source_id, priority, client);

  logger.info("upsert_user_added_source_completed", {
    user_id: authedUserId,
    source_id: data.source_id,
    external_id: input.external_id,
    is_pending: input.is_pending === true,
  });
  // Reason: PostgREST's upsert does not tell us insert-vs-update; we cannot cheaply
  // distinguish without a pre-read, so `was_inserted` is best-effort `true` only
  // when no row pre-existed — left false here (the caller doesn't branch on it; the
  // FOLLOW idempotency is what matters). Documented to avoid a misleading guarantee.
  return { source_id: data.source_id, was_inserted: false };
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
