/**
 * Source-search client (Phase 5c SP3a, LOGIC HALF) — the typed fetch client the
 * future search-and-add modal (`SourceSearchModal`, supplied later as HTML) calls
 * to find sources to add that are NOT in the curated catalog.
 *
 * The static-export SPA cannot hold the YouTube Data API key, so the live
 * external search runs on the FastAPI worker (`agents/worker/main.py`,
 * `POST /api/sources/search`). This module is a THIN HTTP client over that
 * endpoint — same posture as `src/lib/qa/askQuestion.ts` (the worker is the
 * external-API authority; this just fetches + validates the shape).
 *
 * **Debounce lives in the UI, NOT here.** Per the SP3a scope split, this is a
 * pure async fetch client — the 300ms debounce + stale-guard belong to the
 * future modal. Keeping this debounce-agnostic makes it trivially testable and
 * reusable.
 *
 * `is_already_added` is annotated HERE, client-side, against the caller's
 * RLS-scoped follow set (the worker has no per-user follow context — mirrors how
 * SP1's `getRecommendedSources` annotates). The match is by `external_id` (the
 * platform id), joined through `content_sources`, so a search hit that the user
 * already follows from the catalog shows "Added". An ANON browse degrades to
 * all-false WITHOUT throwing (onboarding searches before/around sign-in).
 *
 * @example
 * const { results, search_ok } = await searchSources({ query: "Lex Fridman", kind: "youtube_channel" });
 * results[0].source_name;        // "Lex Fridman"
 * results[0].is_already_added;   // true if already followed
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { ContentSourceType } from "@/types/source";

/**
 * The worker-searchable subset of {@link ContentSourceType}. `personality` is a
 * client-side catalog read (SP1), not a live external search, so it is excluded
 * from the search endpoint — matching the worker's `SourceSearchRequest.kind`.
 */
export type SearchableSourceType = Exclude<ContentSourceType, "personality">;

/** One raw result row as the worker's `SourceSearchResult` returns it. */
export interface WorkerSourceSearchResult {
  /** Display name of the source (channel/podcast title, or X handle). */
  source_name: string;
  /** Stable platform id for dedup/follow (channel id, `itunes-{id}`, or lower-cased handle). */
  external_id: string;
  /** The axis this result lives on (always the searched `kind`). */
  content_source_type: SearchableSourceType;
  /** Avatar/artwork URL, or `null`. */
  thumbnail_url: string | null;
  /** Short blurb (channel description, episode count, or `@handle`), or `null`. */
  description: string | null;
  /** Subscriber/follower count when the provider exposes it, else `null`. */
  subscriber_count: number | null;
  /** X-only: `true` when stored as a pending free-text follow (no live X enrichment). */
  is_pending: boolean;
}

/** A worker result annotated with whether the caller already follows it. */
export interface SourceSearchResult extends WorkerSourceSearchResult {
  /** True when this source's `external_id` is already in the user's follow set. */
  is_already_added: boolean;
}

/** The annotated search outcome the future modal renders. */
export interface SourceSearchOutcome {
  /** The follow-annotated results (possibly empty — a genuine "no matches"). */
  results: SourceSearchResult[];
  /**
   * False when the search could not run (worker missing key / upstream error /
   * transport failure). The UI distinguishes this ("search unavailable") from an
   * empty-but-successful search ("no matches") — Rule 12, never a silent empty.
   */
  search_ok: boolean;
}

/** Arguments for {@link searchSources}. */
export interface SearchSourcesArgs {
  /** The free-text query (channel/podcast name, or an X handle/URL). */
  query: string;
  /** The axis to search ({@link SearchableSourceType}). */
  kind: SearchableSourceType;
  /** Optional Supabase client (injected in tests). Defaults to the shared browser client. */
  client?: SupabaseClient;
  /** Injectable fetch (defaults to the global `fetch`; tests pass a mock). */
  fetchImpl?: typeof fetch;
}

/** The `user_content_sources` follow-junction table name (matches `sources.ts`). */
const USER_CONTENT_SOURCES_TABLE = "user_content_sources";

/**
 * Resolve the source-search worker base URL. Empty string (the default) makes the
 * request a same-origin relative path, right when a proxy/dev rewrite fronts the
 * worker; set `NEXT_PUBLIC_QA_API_BASE_URL` to the deployed worker origin for the
 * Capacitor static build (no same-origin server). Reuses the SAME env var as the
 * Q&A client (`askQuestion.ts`) — it is the one News20 worker origin.
 *
 * @returns The base URL with any trailing slash stripped, or `""` for same-origin.
 */
function getWorkerBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_QA_API_BASE_URL ?? "";
  return base.replace(/\/+$/, "");
}

/**
 * Narrow one unknown JSON element to a valid {@link WorkerSourceSearchResult}.
 * A malformed element is dropped (not faked) so a bad row can never render.
 *
 * @param value - A raw element of the worker's `results` array.
 * @param kind - The searched axis (the result's `content_source_type` must match).
 * @returns The validated result, or `null` when the shape is invalid.
 */
function parseWorkerResult(value: unknown, kind: SearchableSourceType): WorkerSourceSearchResult | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.source_name !== "string" ||
    typeof candidate.external_id !== "string" ||
    candidate.content_source_type !== kind
  ) {
    return null;
  }
  return {
    source_name: candidate.source_name,
    external_id: candidate.external_id,
    content_source_type: kind,
    thumbnail_url: typeof candidate.thumbnail_url === "string" ? candidate.thumbnail_url : null,
    description: typeof candidate.description === "string" ? candidate.description : null,
    subscriber_count: typeof candidate.subscriber_count === "number" ? candidate.subscriber_count : null,
    is_pending: candidate.is_pending === true,
  };
}

/**
 * Read the caller's followed `external_id`s for ONE axis, degrading to an EMPTY
 * set when signed out (the anon onboarding search path). Joins
 * `user_content_sources → content_sources(external_id, content_source_type)` so
 * the platform-id match works against search results (which carry `external_id`,
 * not the catalog `source_id`). RLS scopes the read to `auth.uid()`; a genuine
 * query error is surfaced (Rule 12) — only the signed-out case is swallowed.
 *
 * @param client - The Supabase client to read follows with.
 * @param kind - The axis to filter the joined follows by.
 * @returns The set of followed `external_id`s on that axis (empty when anon).
 * @throws If the authed follow read fails for a real reason (not "signed out").
 */
async function resolveAlreadyAddedExternalIds(
  client: SupabaseClient,
  kind: SearchableSourceType,
): Promise<Set<string>> {
  // Resolve auth state first so an anon search never errors on the follow read.
  const { data: authData, error: authError } = await client.auth.getUser();
  if (authError || !authData.user) {
    logger.info("source_search_anon_browse", {
      reason: "no_active_session",
      note: "is_already_added defaults to false for all rows until sign-in.",
    });
    return new Set<string>();
  }

  // Join through content_sources to recover each follow's platform external_id,
  // filtered to the searched axis (mirrors the donor's user_sources!inner join).
  const { data, error } = await client
    .from(USER_CONTENT_SOURCES_TABLE)
    .select("content_sources!inner(external_id, content_source_type)")
    .eq("user_id", authData.user.id)
    .eq("content_sources.content_source_type", kind);

  if (error) {
    logger.error("source_search_follow_read_failed", {
      kind,
      error_message: error.message,
      fix_suggestion: "Confirm migration 0009 applied and user_content_sources↔content_sources is readable under RLS.",
    });
    throw new Error(
      `Failed to read followed ${kind} sources: ${error.message}. ` +
        "fix_suggestion: confirm migration 0009 applied and the follow join is readable under RLS.",
    );
  }

  const followedExternalIds = new Set<string>();
  for (const row of data ?? []) {
    // PostgREST returns the embedded relation as an object (or array). Normalize.
    const embedded = (row as { content_sources?: unknown }).content_sources;
    const sources = Array.isArray(embedded) ? embedded : embedded ? [embedded] : [];
    for (const source of sources) {
      const externalId = (source as { external_id?: unknown }).external_id;
      if (typeof externalId === "string") {
        followedExternalIds.add(externalId);
      }
    }
  }
  return followedExternalIds;
}

/**
 * Search for addable sources on one axis and annotate each with whether the
 * caller already follows it.
 *
 * POSTs `{ query, kind }` to the worker `POST /api/sources/search`, validates the
 * envelope, then annotates each result with `is_already_added` against the
 * caller's RLS-scoped follow set (matched by `external_id`). Every transport /
 * non-200 / malformed-body failure degrades to `{ results: [], search_ok: false }`
 * — never throws on the search itself (the modal stays open), and an honest
 * `search_ok` flag distinguishes "unavailable" from "no matches" (Rule 12).
 *
 * Note: a FAILED authed follow read (a real DB error, not "signed out") IS
 * surfaced — a silent annotation miss would show "Add" on an already-followed
 * source (a duplicate-follow bug), so that failure must be loud.
 *
 * @param args - {@link SearchSourcesArgs} (query, kind, optional client + fetch).
 * @returns A {@link SourceSearchOutcome} with annotated results + `search_ok`.
 * @throws If the authed follow read fails for a real reason (not "signed out").
 *
 * @example
 * const { results } = await searchSources({ query: "@Reuters", kind: "x_account" });
 * results[0].is_pending; // true — stored as a pending free-text follow (no live X lookup)
 */
export async function searchSources(args: SearchSourcesArgs): Promise<SourceSearchOutcome> {
  const { query, kind } = args;
  const client = args.client ?? getSupabaseBrowserClient();
  const fetchImpl = args.fetchImpl ?? fetch;

  logger.info("search_sources_started", { kind, query_length: query.length });

  let workerResults: WorkerSourceSearchResult[];
  try {
    const response = await fetchImpl(`${getWorkerBaseUrl()}/api/sources/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, kind }),
    });

    if (!response.ok) {
      logger.error("search_sources_non_200", {
        kind,
        status: response.status,
        fix_suggestion:
          "Confirm the worker is deployed and NEXT_PUBLIC_QA_API_BASE_URL points at it; the search endpoint returns HTTP 200.",
      });
      return { results: [], search_ok: false };
    }

    const body: unknown = await response.json();
    if (typeof body !== "object" || body === null || !Array.isArray((body as { results?: unknown }).results)) {
      logger.error("search_sources_malformed_body", {
        kind,
        fix_suggestion: "Endpoint must return { results: SourceSearchResult[], search_ok: boolean }.",
      });
      return { results: [], search_ok: false };
    }
    // An explicit worker-side failure (missing key / upstream error) → unavailable.
    if ((body as { search_ok?: unknown }).search_ok === false) {
      logger.warn("search_sources_worker_unavailable", {
        kind,
        fix_suggestion: "The worker could not run the search (missing key / upstream error); surface as unavailable.",
      });
      return { results: [], search_ok: false };
    }
    workerResults = (body as { results: unknown[] }).results
      .map((entry) => parseWorkerResult(entry, kind))
      .filter((entry): entry is WorkerSourceSearchResult => entry !== null);
  } catch (error: unknown) {
    logger.error("search_sources_failed", {
      kind,
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion: "Check network connectivity and that the worker search endpoint is reachable over HTTPS.",
    });
    return { results: [], search_ok: false };
  }

  const alreadyAddedExternalIds = await resolveAlreadyAddedExternalIds(client, kind);
  const results: SourceSearchResult[] = workerResults.map((result) => ({
    ...result,
    is_already_added: alreadyAddedExternalIds.has(result.external_id),
  }));

  logger.info("search_sources_completed", {
    kind,
    returned: results.length,
    already_added: results.filter((row) => row.is_already_added).length,
  });
  return { results, search_ok: true };
}
