/**
 * Interest taxonomy data-access (Phase 1e SP3) — typed reads over the public-read
 * `interests` tree (migration 0003) using the anon Supabase client.
 *
 * Sibling of `src/lib/feed/supabaseFeed.ts`: same shape — a small module that
 * queries the anon client and maps rows to a verbose typed object, with an
 * injectable `client` default for tests. The chip UI uses **lazy child
 * expansion** — it reads only depth-0 categories up front
 * ({@link fetchRootInterests}), then fetches a node's direct children
 * ({@link fetchChildInterests}) when that chip is tapped, so the tree never loads
 * all at once.
 *
 * SP3 is read-only. No write to `user_interest_profile` happens here — selection
 * is held in-memory by `InterestChips` and persisted by SP4.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * One node of the `interests` taxonomy tree, mirroring the migration-0003
 * `interests` columns the chip UI needs. `interest_segment_slug` /
 * `interest_search_query` are nullable in the schema; `interest_kind` is
 * `'taxonomy'` for seeded nodes (`'custom'` is reserved for the pending
 * free-text selections SP4 persists, never read from this table).
 */
export interface Interest {
  interest_id: string;
  parent_interest_id: string | null;
  interest_slug: string;
  interest_label: string;
  depth_level: number;
  interest_segment_slug: string | null;
  interest_search_query: string | null;
  interest_kind: string;
}

/** The columns selected from `interests` — kept in sync with {@link Interest}. */
const INTEREST_SELECT =
  "interest_id,parent_interest_id,interest_slug,interest_label,depth_level," +
  "interest_segment_slug,interest_search_query,interest_kind";

/** A raw `interests` row as PostgREST returns it for {@link INTEREST_SELECT}. */
interface InterestRow {
  interest_id: string;
  parent_interest_id: string | null;
  interest_slug: string;
  interest_label: string;
  depth_level: number;
  interest_segment_slug: string | null;
  interest_search_query: string | null;
  interest_kind: string;
}

/** Map a raw row to the typed {@link Interest} (identity today; a seam for drift). */
function mapInterestRow(row: InterestRow): Interest {
  return {
    interest_id: row.interest_id,
    parent_interest_id: row.parent_interest_id,
    interest_slug: row.interest_slug,
    interest_label: row.interest_label,
    depth_level: row.depth_level,
    interest_segment_slug: row.interest_segment_slug,
    interest_search_query: row.interest_search_query,
    interest_kind: row.interest_kind,
  };
}

/**
 * Fetch the depth-0 root categories the chip onboarding opens with.
 *
 * Returns active nodes whose `parent_interest_id IS NULL`, ordered by
 * `interest_sort_order` (the seed's intended chip order).
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the
 *   shared browser anon client.
 * @returns The root interests in `interest_sort_order`.
 * @throws If the query fails (errors are surfaced, never swallowed — Rule 12).
 *
 * @example
 * const roots = await fetchRootInterests();
 * roots[0].depth_level; // 0
 */
export async function fetchRootInterests(client: SupabaseClient = getSupabaseBrowserClient()): Promise<Interest[]> {
  logger.info("fetch_root_interests_started", {});
  const { data, error } = await client
    .from("interests")
    .select(INTEREST_SELECT)
    .is("parent_interest_id", null)
    .eq("interest_is_active", true)
    .order("interest_sort_order", { ascending: true })
    .returns<InterestRow[]>();

  if (error) {
    logger.error("fetch_root_interests_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm migration 0003 applied, interests are seeded, and RLS allows anon SELECT.",
    });
    throw new Error(
      `Failed to load root interests from Supabase: ${error.message}. ` +
        "fix_suggestion: confirm migration 0003 applied, interests are seeded, and RLS allows anon SELECT.",
    );
  }

  const interests = (data ?? []).map(mapInterestRow);
  logger.info("fetch_root_interests_completed", { total: interests.length });
  return interests;
}

/**
 * Fetch the direct children of one interest — the lazy expansion a chip tap
 * triggers (depth-0 tap → its depth-1 children; depth-1 tap → its depth-2
 * children).
 *
 * Filters strictly by `parent_interest_id = parentInterestId` so only the tapped
 * node's children are returned; active rows only, ordered by
 * `interest_sort_order`.
 *
 * @param parentInterestId - The `interest_id` of the tapped node.
 * @param client - Optional Supabase client (injected in tests). Defaults to the
 *   shared browser anon client.
 * @returns The direct children in `interest_sort_order` (empty if the node is a leaf).
 * @throws If the query fails (errors are surfaced, never swallowed — Rule 12).
 *
 * @example
 * const children = await fetchChildInterests(sportId);
 * children.every((child) => child.parent_interest_id === sportId); // true
 */
export async function fetchChildInterests(
  parentInterestId: string,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<Interest[]> {
  logger.info("fetch_child_interests_started", { parent_interest_id: parentInterestId });
  const { data, error } = await client
    .from("interests")
    .select(INTEREST_SELECT)
    .eq("parent_interest_id", parentInterestId)
    .eq("interest_is_active", true)
    .order("interest_sort_order", { ascending: true })
    .returns<InterestRow[]>();

  if (error) {
    logger.error("fetch_child_interests_failed", {
      parent_interest_id: parentInterestId,
      error_message: error.message,
      fix_suggestion: "Confirm migration 0003 applied, interests are seeded, and RLS allows anon SELECT.",
    });
    throw new Error(
      `Failed to load child interests for "${parentInterestId}" from Supabase: ${error.message}. ` +
        "fix_suggestion: confirm migration 0003 applied, interests are seeded, and RLS allows anon SELECT.",
    );
  }

  const interests = (data ?? []).map(mapInterestRow);
  logger.info("fetch_child_interests_completed", {
    parent_interest_id: parentInterestId,
    total: interests.length,
  });
  return interests;
}

/**
 * The locked category accent per `segments.segment_slug` (mirrors src/lib/feedBuckets.ts
 * + blip-library.css tokens): the SINGLE place the Sources interest chips resolve a
 * category dot color. A slug not in the map (or a null segment) → no dot color.
 */
const SEGMENT_ACCENT_HEX: Readonly<Record<string, string>> = {
  ai: "#3B82F6", // AI — brand primary blue
  geopolitics: "#EF4444", // Geopolitics — red
  business: "#22C55E", // Business — green
  environment: "#34D399", // Environment — emerald
  politics: "#A78BFA", // Politics — purple
  tech: "#22D3EE", // Tech — cyan
  sport: "#F59E0B", // Sport — amber
  arts: "#E8B7BC", // Arts — rose
};

/** One of the authed user's selected interests, shaped for the Sources "Interests" chips. */
export interface UserInterestChip {
  /** The `interests.interest_id` (stable key). */
  interestId: string;
  /** The interest's display label. */
  label: string;
  /** Tree depth (0 = root category) — roots sort first so colored category chips lead. */
  depthLevel: number;
  /** The locked category accent hex, or null when the interest carries no segment. */
  accentHex: string | null;
}

/** A `user_interest_profile ⋈ interests` row as PostgREST returns it for the chip read. */
interface UserInterestProfileRow {
  interests: {
    interest_id: string;
    interest_label: string;
    depth_level: number;
    interest_segment_slug: string | null;
  } | null;
}

/**
 * Read the authed user's selected interests for display as chips on the Sources
 * surface — `user_interest_profile` embedded with the joined `interests` node.
 * Owner-scoped under RLS (the browser client carries the session). A signed-out
 * read returns `[]` (the chips simply don't render) rather than throwing, since
 * the Sources surface must still paint for an anon/degraded session (Rule 12).
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The user's interests as {@link UserInterestChip}[], roots first.
 *
 * @example
 * const chips = await getUserInterests();
 * chips[0].accentHex; // "#22C55E" for a Markets pick
 */
export async function getUserInterests(
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<UserInterestChip[]> {
  const { data: authData, error: authError } = await client.auth.getUser();
  if (authError || !authData.user) {
    logger.info("get_user_interests_signed_out", { reason: authError?.message ?? "no_session" });
    return [];
  }

  const { data, error } = await client
    .from("user_interest_profile")
    .select("interests(interest_id,interest_label,depth_level,interest_segment_slug)")
    .eq("profile_user_id", authData.user.id)
    .returns<UserInterestProfileRow[]>();

  if (error) {
    logger.error("get_user_interests_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm migration 0003 applied and user_interest_profile↔interests is readable under owner RLS.",
    });
    return [];
  }

  const chips: UserInterestChip[] = [];
  for (const row of data ?? []) {
    if (!row.interests) {
      continue;
    }
    const { interest_id, interest_label, depth_level, interest_segment_slug } = row.interests;
    chips.push({
      interestId: interest_id,
      label: interest_label,
      depthLevel: depth_level,
      accentHex: interest_segment_slug ? (SEGMENT_ACCENT_HEX[interest_segment_slug] ?? null) : null,
    });
  }
  // Roots (depth 0) first so the colored category chips lead the row.
  chips.sort((a, b) => a.depthLevel - b.depthLevel);
  logger.info("get_user_interests_completed", { total: chips.length });
  return chips;
}
