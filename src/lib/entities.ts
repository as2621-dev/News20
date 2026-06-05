/**
 * Entity registry data layer (Phase 5 SP2, recursive interest picker) — the typed
 * client-side reads that power the picker's **Show more** (pagination) and **Add
 * your own** (search) affordances against the seeded `entities` registry from
 * migration 0007.
 *
 * Standalone data-access sibling of `src/lib/feed/supabaseFeed.ts` and
 * `src/lib/onboardingProfile.ts`: small functions that read through the shared
 * browser anon client under RLS (`entities` is public-read, mirroring the content
 * tables), with an injectable `client` default for tests. There is NO Next.js
 * server runtime on device (Capacitor static export), so spec §6's REST endpoints
 * (`GET /api/entities/list|search`) ship here as client-side Supabase reads — a
 * keyset query for `list` and the `search_entities` Postgres RPC for `search`
 * (Rule 7: the spec frames REST, News20 ships client-side Supabase per phase-1e).
 *
 * Both functions return the spec §6 result shape `{ id, label, ticker?, kind }`,
 * mapping the `entity_*` columns (decision: `entity_id→id`, `entity_label→label`,
 * `entity_ticker→ticker` (OMITTED when null), `entity_kind→kind`).
 *
 * ── Pagination model (listEntities) ─────────────────────────────────────────
 * Keyset, not offset — ordered by `entity_id` and seeking with `.gt('entity_id',
 * cursor)`, served by SP1's `idx_entities_parent_kind (entity_parent_slug,
 * entity_kind, entity_id)`. Keyset is overlap-free under inserts (the registry is
 * append-only seed data) and terminates: `nextCursor` is the last row's
 * `entity_id` only when a FULL page returned, else `null`.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * The `entity_kind` enum (migration 0007) — the closed §2/§5 taxonomy. The UI uses
 * `kind` for affordances (companies render a ticker, etc.). Kept in lockstep with
 * the DB enum; a drift here surfaces as a mapping mismatch in the SP2 tests.
 */
export type EntityKind =
  | "company"
  | "team"
  | "person"
  | "league"
  | "org"
  | "asset"
  | "event"
  | "brand"
  | "franchise"
  | "conflict"
  | "genre"
  | "product";

/**
 * One registry entry in the spec §6 result shape. `ticker` is OMITTED (not `null`)
 * when the entity has none — only companies carry one. This is exactly the payload
 * SP3's `FollowChip`/`FollowSet` consume for Show-more rows and Add-your-own hits.
 */
export interface EntityResult {
  id: string;
  label: string;
  ticker?: string;
  kind: EntityKind;
}

/** A page of registry entries plus the keyset cursor to fetch the next page. */
export interface EntityPage {
  /** The mapped entities for this page (≤ `limit`). */
  results: EntityResult[];
  /**
   * The `entity_id` to pass as `cursor` for the next page, or `null` when this is
   * the last page (a partial/empty page returned → no more rows).
   */
  nextCursor: string | null;
}

/** Default page size for both list and search (spec §6 uses `limit=20`). */
const DEFAULT_LIMIT = 20;

/** A raw `entities` row as PostgREST / the `search_entities` RPC returns it. */
interface EntityRow {
  entity_id: string;
  entity_label: string;
  entity_ticker: string | null;
  entity_kind: EntityKind;
}

/** The exact column projection both reads request (keeps the row shape minimal). */
const ENTITY_COLUMNS = "entity_id,entity_label,entity_ticker,entity_kind";

/**
 * Map one DB row into the spec §6 result shape. `ticker` is set ONLY when the
 * column is non-null/non-empty, so consumers can branch on its presence rather
 * than on a sentinel.
 */
function mapEntityRow(row: EntityRow): EntityResult {
  const result: EntityResult = {
    id: row.entity_id,
    label: row.entity_label,
    kind: row.entity_kind,
  };
  if (row.entity_ticker) {
    result.ticker = row.entity_ticker;
  }
  return result;
}

/** Parameters for {@link listEntities}. */
export interface ListEntitiesParams {
  /** The expandable parent node id (`entity_parent_slug` scope) to page under. */
  parent: string;
  /** Optional `entity_kind` filter (e.g. only `company` rows under an Earnings set). */
  kind?: EntityKind;
  /** Keyset cursor: the previous page's `nextCursor` (`entity_id`). Omit for page 1. */
  cursor?: string;
  /** Max rows to fetch (default {@link DEFAULT_LIMIT}). */
  limit?: number;
}

/**
 * Keyset-paginate a parent node's child entities (spec §6 `list`; powers **Show
 * more**). Filters `entity_parent_slug = parent` (+ `entity_kind = kind` when
 * given), orders by `entity_id`, and seeks past `cursor` with `.gt`, served by
 * `idx_entities_parent_kind`. Returns `{ results, nextCursor }` where `nextCursor`
 * is the last row's `entity_id` ONLY when a full page came back (else `null`).
 *
 * @param params - {@link ListEntitiesParams} (`parent` required; `kind`/`cursor`/`limit` optional).
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns An {@link EntityPage} — mapped §6 rows + the next keyset cursor (or `null`).
 * @throws If the query fails (errors are surfaced, never swallowed — Rule 12).
 *
 * @example
 * const page1 = await listEntities({ parent: "business/corporate-news/earnings", kind: "company" });
 * const page2 = page1.nextCursor ? await listEntities({ parent: "...", cursor: page1.nextCursor }) : null;
 */
export async function listEntities(
  params: ListEntitiesParams,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<EntityPage> {
  const { parent, kind, cursor } = params;
  const limit = params.limit ?? DEFAULT_LIMIT;
  logger.info("list_entities_started", { parent, kind: kind ?? null, cursor: cursor ?? null, limit });

  let query = client
    .from("entities")
    .select(ENTITY_COLUMNS)
    .eq("entity_parent_slug", parent)
    .order("entity_id", { ascending: true })
    .limit(limit);

  if (kind) {
    query = query.eq("entity_kind", kind);
  }
  // Keyset seek: strictly-greater than the last id of the previous page → no
  // overlap, no row skipped (stable `entity_id` order over append-only seed data).
  if (cursor) {
    query = query.gt("entity_id", cursor);
  }

  const { data, error } = await query.returns<EntityRow[]>();

  if (error) {
    logger.error("list_entities_failed", {
      parent,
      error_message: error.message,
      fix_suggestion: "Confirm migration 0007 applied, entities seed ran, and entities allows anon SELECT.",
    });
    throw new Error(
      `Failed to list entities for parent "${parent}": ${error.message}. ` +
        "fix_suggestion: confirm migration 0007 applied and entities are readable.",
    );
  }

  const rows = data ?? [];
  const results = rows.map(mapEntityRow);
  // A full page means there MAY be more → hand back the last id as the cursor. A
  // short/empty page means the set is exhausted → null (terminates Show-more).
  const nextCursor = rows.length === limit ? rows[rows.length - 1].entity_id : null;

  logger.info("list_entities_completed", { parent, returned: results.length, has_next: nextCursor !== null });
  return { results, nextCursor };
}

/** Parameters for {@link searchEntities}. */
export interface SearchEntitiesParams {
  /** The free-text query the user typed in Add-your-own. */
  q: string;
  /** Optional `entity_kind` filter. */
  kind?: EntityKind;
  /** Optional parent-scope filter (`entity_parent_slug`). */
  parent?: string;
  /** Max matches to return (default {@link DEFAULT_LIMIT}). */
  limit?: number;
}

/**
 * Fuzzy-search the registry (spec §6 `search`; powers **Add your own**). Calls the
 * `search_entities` Postgres RPC (added to migration 0007), which matches
 * `entity_search_query ILIKE '%q%'` over the trigram GIN index and ranks by
 * `word_similarity`. A no-match query returns `[]` — a FIRST-CLASS result, so the
 * caller can fall back to storing the typed value as a free-text follow (spec §6).
 *
 * @param params - {@link SearchEntitiesParams} (`q` required; `kind`/`parent`/`limit` optional).
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The mapped §6 matches, or `[]` when nothing matches (a valid outcome).
 * @throws If the RPC fails (errors are surfaced, never swallowed — Rule 12).
 *
 * @example
 * const hits = await searchEntities({ q: "Nvidia" });
 * hits[0].ticker; // "NVDA"  — caller stores the resolved entity id
 * const miss = await searchEntities({ q: "zzzznotareal entity" }); // []  → store free text
 */
export async function searchEntities(
  params: SearchEntitiesParams,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<EntityResult[]> {
  const { q, kind, parent } = params;
  const limit = params.limit ?? DEFAULT_LIMIT;
  const trimmedQuery = q.trim();
  logger.info("search_entities_started", { q: trimmedQuery, kind: kind ?? null, parent: parent ?? null, limit });

  // An empty query can never resolve to an entity — short-circuit to free-text.
  if (trimmedQuery === "") {
    logger.info("search_entities_completed", { q: "", matched: 0 });
    return [];
  }

  // Parameterized RPC — the query string is bound, never concatenated into SQL, so
  // it is not injectable. Null `kind`/`parent` let the RPC skip those filters.
  const { data, error } = await client.rpc("search_entities", {
    q: trimmedQuery,
    k: kind ?? null,
    p: parent ?? null,
    lim: limit,
  });

  if (error) {
    logger.error("search_entities_failed", {
      q: trimmedQuery,
      error_message: error.message,
      fix_suggestion: "Confirm the search_entities RPC exists in migration 0007 and entities allows anon SELECT.",
    });
    throw new Error(
      `Failed to search entities for "${trimmedQuery}": ${error.message}. ` +
        "fix_suggestion: confirm the search_entities RPC was applied (migration 0007).",
    );
  }

  // Reason: supabase-js cannot infer an untyped RPC's row type — `.returns<>()` on a
  // function builder yields an error-guard union, not EntityRow[]. The RPC's
  // `returns table(...)` shape IS EntityRow[], so we cast at the boundary (the search
  // succeeded; `error` was checked above). Not `any` — a precise, justified cast.
  const rows = (data as EntityRow[] | null) ?? [];
  const results = rows.map(mapEntityRow);
  logger.info("search_entities_completed", { q: trimmedQuery, matched: results.length });
  return results;
}
