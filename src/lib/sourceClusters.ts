/**
 * Category-keyed cluster read + no-dup resolver (Phase FSR-M6a SP1) — the
 * TypeScript-side read of M1's catalog/cluster contract.
 *
 * M1 (`agents/catalog/cluster_resolver.py::resolve_category_clusters` +
 * `cluster_query.py::clusters_for_category` + migration 0022) owns the cluster
 * schema, the seed, and the canonical PYTHON resolver. This module is the
 * client-side TS read of the SAME contract: there is NO server runtime on device
 * (Capacitor static export, no live ranking RPC), so the resolver runs client-side
 * over public-read Supabase rows under RLS — it cannot call the Python resolver.
 *
 * It MIRRORS the Python resolver's semantics EXACTLY (so the offline fixture cases
 * match M1's no-dup cases), per the M1 `CatalogRepo` contract documented in
 * `cluster_query.py`:
 *   (a) filter clusters to `cluster_category == category` AND `is_curated`, ordered
 *       by `(cluster_sort_order, cluster_slug)` (stable tie-break);
 *   (b) NO-DUP SET: a personality PRESENT in this category (a personality member of
 *       any cluster in it) suppresses its bundled YouTube/X `content_sources` rows —
 *       `youtube_channel_ids` matched vs `youtube_channel` `external_id`, `aliases`
 *       matched vs `x_account` `external_id`. The personality card already
 *       represents them (PRD Decision #7 — the load-bearing trust contract);
 *   (c) per cluster, in `member_sort_order`, expand each member ref, SKIPPING a
 *       missing / un-curated row, SKIPPING a suppressed (no-dup) source row, and —
 *       FIRST-CLUSTER-WINS — skipping any followable already rendered in an EARLIER
 *       cluster of this category (so a followable in two clusters renders once);
 *   (d) DROP a cluster left with zero rendered members.
 *
 * The CATEGORY SOURCE POOL (`sources` passed to {@link resolveCategoryClusters})
 * MUST include the personality-bundled rows by `topic_tags` overlap — NOT only the
 * rows referenced as members — or the no-dup match goes blind to a bundled row that
 * leaks in elsewhere (the `cluster_query.py` "load-bearing subtlety").
 * {@link getClustersForCategories} honors this by fetching the full per-category
 * `content_sources`/`personalities` pools, exactly as M1's live `CatalogRepo` impl
 * must.
 *
 * Same client pattern as `src/lib/sources.ts`/`sourceRecommendations.ts`: the read
 * takes an optional `client` (injected in tests) defaulting to the shared browser
 * anon client; catalog/cluster reads are anon-PUBLIC (the `*_public_read` policies,
 * migrations 0009 + 0022). The pure {@link resolveCategoryClusters} touches no
 * I/O — it is the fixture-tested mirror of the Python resolver.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { ResolvedFollowSet } from "@/lib/clusterSelection";
import { logger } from "@/lib/logger";
import { followPersonality, followSource } from "@/lib/sources";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { ContentSource, Personality } from "@/types/source";

/** The `source_clusters` table name (migration 0022). */
const SOURCE_CLUSTERS_TABLE = "source_clusters";

/** The `source_cluster_members` table name (migration 0022). */
const SOURCE_CLUSTER_MEMBERS_TABLE = "source_cluster_members";

/** The `personalities` table name (migration 0009). */
const PERSONALITIES_TABLE = "personalities";

/** The `content_sources` table name (migration 0009). */
const CONTENT_SOURCES_TABLE = "content_sources";

/**
 * The no-dup match is axis-specific (mirrors the Python resolver's `_YOUTUBE_AXIS`
 * / `_X_AXIS`): a personality's `youtube_channel_ids` suppress only `youtube_channel`
 * source rows, its `aliases` suppress only `x_account` rows.
 */
const YOUTUBE_AXIS = "youtube_channel";
const X_AXIS = "x_account";

/** The `source_clusters` column projection (every field {@link ClusterRow} reads). */
const CLUSTER_COLUMNS = "cluster_id,cluster_slug,cluster_label,cluster_category,cluster_sort_order,is_curated";

/** The `source_cluster_members` column projection. */
const CLUSTER_MEMBER_COLUMNS = "cluster_id,source_id,personality_id,member_sort_order";

/** The `personalities` column projection (the no-dup match + card fields). */
const PERSONALITY_COLUMNS =
  "personality_id,display_name,aliases,bio,photo_url,youtube_channel_ids,personas,topic_tags,popularity_score,is_curated";

/** The `content_sources` column projection (mirrors `sources.ts` `CONTENT_SOURCE_COLUMNS`). */
const CONTENT_SOURCE_COLUMNS =
  "source_id,content_source_type,external_id,source_name,source_description,thumbnail_url," +
  "subscriber_count,platform_metadata,personas,topic_tags,popularity_score,is_curated,last_fetched_at";

/** A row of `source_clusters` (migration 0022) — mirrors the Python `ClusterRow`. */
export interface ClusterRow {
  cluster_id: string;
  cluster_slug: string;
  cluster_label: string;
  /** One of the 8 topic roots (the resolver filters on it). */
  cluster_category: string;
  cluster_sort_order: number;
  is_curated: boolean;
}

/**
 * A row of `source_cluster_members` (migration 0022) — EXACTLY ONE of `source_id`
 * XOR `personality_id` (the table's XOR check). Mirrors the Python `ClusterMemberRef`.
 */
export interface ClusterMemberRef {
  cluster_id: string;
  source_id: string | null;
  personality_id: string | null;
  member_sort_order: number;
}

/** A rendered followable — enough to render a member tile. Mirrors `ResolvedClusterMember`. */
export interface ResolvedClusterMember {
  /** `source` (an individual catalog row) | `personality` (a named-creator card). */
  kind: "source" | "personality";
  /** The underlying `source_id` / `personality_id` — the dedup key + the follow target. */
  followable_id: string;
  /** The card/tile label. */
  display_name: string;
  /** Ranking signal carried into the tile. */
  popularity_score: number;
}

/** A cluster with its ordered, deduped, no-dup-honored members. Mirrors `ResolvedCluster`. */
export interface ResolvedCluster {
  cluster_slug: string;
  cluster_label: string;
  /** One of the 8 topic roots. */
  cluster_category: string;
  cluster_sort_order: number;
  /** Non-empty — empty clusters are NOT emitted (rule d). */
  members: ResolvedClusterMember[];
}

/**
 * Resolve ONE category's clusters to ordered, deduped, no-dup-honored members —
 * the pure TS mirror of `agents/catalog/cluster_resolver.py::resolve_category_clusters`.
 *
 * No DB / network / clock — it operates on the rows the caller already fetched, so
 * it is fixture-testable against the SAME no-dup cases as the Python resolver
 * (Rule 9 — the no-dup leak is the load-bearing test).
 *
 * @param category - One of the 8 topic roots — clusters are filtered to it.
 * @param clusters - All `source_clusters` rows (any category — filtered here).
 * @param members - All `source_cluster_members` refs (any cluster — grouped here).
 * @param sources - The category's candidate `content_sources` POOL (MUST include the
 *   personality-bundled rows by `topic_tags` overlap — see module note).
 * @param personalities - The category's candidate `personalities` pool.
 * @returns The category's curated, non-empty clusters in render order.
 */
export function resolveCategoryClusters(
  category: string,
  clusters: ClusterRow[],
  members: ClusterMemberRef[],
  sources: ContentSource[],
  personalities: Personality[],
): ResolvedCluster[] {
  const sourcesById = new Map(sources.map((s) => [s.source_id, s]));
  const personalitiesById = new Map(personalities.map((p) => [p.personality_id, p]));

  // (a) Filter to this category + curated, ordered by sort then slug (stable tie).
  const categoryClusters = clusters
    .filter((c) => c.cluster_category === category && c.is_curated)
    .sort((a, b) => a.cluster_sort_order - b.cluster_sort_order || a.cluster_slug.localeCompare(b.cluster_slug));
  const categoryClusterIds = new Set(categoryClusters.map((c) => c.cluster_id));

  // Group members by cluster, ordered within a cluster by member_sort_order. Only
  // members of THIS category's clusters matter.
  const membersByCluster = new Map<string, ClusterMemberRef[]>();
  for (const m of members) {
    if (categoryClusterIds.has(m.cluster_id)) {
      const list = membersByCluster.get(m.cluster_id) ?? [];
      list.push(m);
      membersByCluster.set(m.cluster_id, list);
    }
  }
  for (const list of membersByCluster.values()) {
    list.sort((a, b) => a.member_sort_order - b.member_sort_order);
  }

  // (b) NO-DUP SET — source_ids suppressed because a PRESENT personality bundles
  // them. A personality is "present in this category" iff it is a personality member
  // of any cluster in this category (PRD Decision #7 — the trust contract).
  const presentPersonalityIds = new Set<string>();
  for (const list of membersByCluster.values()) {
    for (const m of list) {
      if (m.personality_id !== null) {
        presentPersonalityIds.add(m.personality_id);
      }
    }
  }
  const bundledYoutubeIds = new Set<string>();
  const bundledXAliases = new Set<string>();
  for (const pid of presentPersonalityIds) {
    const p = personalitiesById.get(pid);
    if (!p?.is_curated) {
      continue;
    }
    for (const yt of p.youtube_channel_ids) {
      bundledYoutubeIds.add(yt);
    }
    for (const alias of p.aliases) {
      bundledXAliases.add(alias);
    }
  }
  const suppressedSourceIds = new Set<string>();
  for (const s of sources) {
    if (
      (s.content_source_type === YOUTUBE_AXIS && bundledYoutubeIds.has(s.external_id)) ||
      (s.content_source_type === X_AXIS && bundledXAliases.has(s.external_id))
    ) {
      suppressedSourceIds.add(s.source_id);
    }
  }

  // (c)+(d) Render members per cluster in order, applying skips + first-cluster-wins
  // dedup. `renderedFollowableIds` spans the whole category: once a followable
  // renders, later clusters drop it — the FIRST cluster (step a order) wins.
  const renderedFollowableIds = new Set<string>();
  const resolved: ResolvedCluster[] = [];
  for (const cluster of categoryClusters) {
    const renderedMembers: ResolvedClusterMember[] = [];
    for (const m of membersByCluster.get(cluster.cluster_id) ?? []) {
      if (m.source_id !== null) {
        // skip suppressed (no-dup) source rows.
        if (suppressedSourceIds.has(m.source_id)) {
          continue;
        }
        const row = sourcesById.get(m.source_id);
        // skip missing / un-curated.
        if (!row?.is_curated) {
          continue;
        }
        // first-cluster-wins: skip a followable already rendered.
        if (renderedFollowableIds.has(m.source_id)) {
          continue;
        }
        renderedFollowableIds.add(m.source_id);
        renderedMembers.push({
          kind: "source",
          followable_id: row.source_id,
          display_name: row.source_name,
          popularity_score: row.popularity_score,
        });
      } else if (m.personality_id !== null) {
        const prow = personalitiesById.get(m.personality_id);
        if (!prow?.is_curated) {
          continue;
        }
        if (renderedFollowableIds.has(m.personality_id)) {
          continue;
        }
        renderedFollowableIds.add(m.personality_id);
        renderedMembers.push({
          kind: "personality",
          followable_id: prow.personality_id,
          display_name: prow.display_name,
          popularity_score: prow.popularity_score,
        });
      }
    }
    // drop a cluster left empty after all skips/dedup — never surfaced.
    if (renderedMembers.length > 0) {
      resolved.push({
        cluster_slug: cluster.cluster_slug,
        cluster_label: cluster.cluster_label,
        cluster_category: cluster.cluster_category,
        cluster_sort_order: cluster.cluster_sort_order,
        members: renderedMembers,
      });
    }
  }
  return resolved;
}

/**
 * Read the resolved clusters for a set of chosen categories — the live read over
 * M1's public-read tables, piped through {@link resolveCategoryClusters} per
 * category (mirroring `cluster_query.py::clusters_for_category` once per category).
 *
 * For each chosen category it loads:
 *   - the category's `source_clusters` (filtered by `cluster_category`),
 *   - the `source_cluster_members` for those clusters,
 *   - the category's candidate `content_sources` POOL (`topic_tags` overlap — MUST
 *     include personality-bundled rows so the no-dup match can see them),
 *   - the category's candidate `personalities` pool (`topic_tags` overlap),
 * then resolves. A category with no clusters yields `[]` for that category.
 *
 * **Empty / un-seeded tables = `[]`, not a crash (Rule 12 surfaces a REAL read
 * error, but an empty seed is a valid "no clusters yet" — the SP1 note: STOP only if
 * the tables are ABSENT, which surfaces as a read error here).**
 *
 * @param categories - The user's chosen top-level category slugs.
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns A map of `category → ResolvedCluster[]` (only the chosen categories; a
 *   category with no curated clusters maps to `[]`).
 * @throws If any read fails (errors are surfaced, never swallowed — Rule 12).
 *
 * @example
 * const byCategory = await getClustersForCategories(["ai", "tech"]);
 * byCategory.get("ai")?.[0].cluster_label; // "Leading AI-lab researchers"
 */
export async function getClustersForCategories(
  categories: string[],
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<Map<string, ResolvedCluster[]>> {
  logger.info("get_clusters_for_categories_started", { categories });

  const result = new Map<string, ResolvedCluster[]>();
  if (categories.length === 0) {
    logger.info("get_clusters_for_categories_completed", { returned_categories: 0, reason: "empty_categories" });
    return result;
  }

  // Resolve each category independently (mirrors the Python per-category contract).
  // Per-category reads run in parallel; the resolver is pure + sync.
  await Promise.all(
    categories.map(async (category) => {
      const resolved = await resolveOneCategory(category, client);
      result.set(category, resolved);
    }),
  );

  logger.info("get_clusters_for_categories_completed", {
    returned_categories: result.size,
    total_clusters: [...result.values()].reduce((sum, list) => sum + list.length, 0),
  });
  return result;
}

/** Fetch + resolve one category's clusters (the per-category half of {@link getClustersForCategories}). */
async function resolveOneCategory(category: string, client: SupabaseClient): Promise<ResolvedCluster[]> {
  // 1. The category's clusters.
  const { data: clusterData, error: clusterError } = await client
    .from(SOURCE_CLUSTERS_TABLE)
    .select(CLUSTER_COLUMNS)
    .eq("cluster_category", category)
    .returns<ClusterRow[]>();
  if (clusterError) {
    throwCatalogReadError("source_clusters", category, clusterError.message, "migration 0022");
  }
  const clusters = clusterData ?? [];

  // 2. The members of those clusters (skip the round-trip when no clusters).
  const clusterIds = clusters.map((c) => c.cluster_id);
  let members: ClusterMemberRef[] = [];
  if (clusterIds.length > 0) {
    const { data: memberData, error: memberError } = await client
      .from(SOURCE_CLUSTER_MEMBERS_TABLE)
      .select(CLUSTER_MEMBER_COLUMNS)
      .in("cluster_id", clusterIds)
      .returns<ClusterMemberRef[]>();
    if (memberError) {
      throwCatalogReadError("source_cluster_members", category, memberError.message, "migration 0022");
    }
    members = memberData ?? [];
  }

  // 3+4. The candidate source + personality pools (topic_tags overlap — MUST include
  // the personality-bundled rows so the no-dup match can see them).
  const [sources, personalities] = await Promise.all([
    fetchCategorySources(category, client),
    fetchCategoryPersonalities(category, client),
  ]);

  return resolveCategoryClusters(category, clusters, members, sources, personalities);
}

/** Read the category's candidate `content_sources` pool (`topic_tags` overlap). */
async function fetchCategorySources(category: string, client: SupabaseClient): Promise<ContentSource[]> {
  const { data, error } = await client
    .from(CONTENT_SOURCES_TABLE)
    .select(CONTENT_SOURCE_COLUMNS)
    .overlaps("topic_tags", [category])
    .returns<ContentSource[]>();
  if (error) {
    throwCatalogReadError("content_sources", category, error.message, "migration 0009");
  }
  return data ?? [];
}

/** Read the category's candidate `personalities` pool (`topic_tags` overlap). */
async function fetchCategoryPersonalities(category: string, client: SupabaseClient): Promise<Personality[]> {
  const { data, error } = await client
    .from(PERSONALITIES_TABLE)
    .select(PERSONALITY_COLUMNS)
    .overlaps("topic_tags", [category])
    .returns<Personality[]>();
  if (error) {
    throwCatalogReadError("personalities", category, error.message, "migration 0009");
  }
  return data ?? [];
}

/**
 * Commit a resolved opt-out follow set (SP2 {@link ResolvedFollowSet}) to the user's
 * follow tables (Phase FSR-M6a SP4) — content-source members via {@link followSource}
 * (→ `user_content_sources`), personality members via {@link followPersonality}
 * (→ `user_personalities`). Both primitives are idempotent on their PK, so a re-commit
 * of the same set is a no-op (the DoD's idempotent re-commit). An EMPTY follow set
 * writes nothing and does not error (the zero-cluster path — User Story 21).
 *
 * Writes EXACTLY the resolved set — the source/personality partition the selection
 * model produced. The deselected members are simply absent from the set, so they are
 * never written (the DoD: the follow set equals the resolved opt-out set, not "some
 * rows written"). Unfollowing previously-followed members on RE-commit is out of
 * scope here (onboarding is a first commit; the control surface owns later edits).
 *
 * @param followSet - The {@link ResolvedFollowSet} from `resolveFollowSet`.
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The counts written, for the caller's telemetry.
 * @throws If any follow write fails (surfaced — Rule 12); a partial failure throws on
 *   the first error (the primitives are idempotent, so a retry is safe).
 *
 * @example
 * await commitClusterFollowSet(resolveFollowSet(selection));
 */
export async function commitClusterFollowSet(
  followSet: ResolvedFollowSet,
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<{ sources_followed: number; personalities_followed: number }> {
  logger.info("commit_cluster_follow_set_started", {
    source_count: followSet.sources.length,
    personality_count: followSet.personalities.length,
  });

  // Sequential per axis keeps the failure surface simple (first error throws; the
  // primitives are idempotent so a retry re-runs safely). Onboarding volumes are
  // ~30–50 follows — a tight loop, not a perf concern.
  for (const sourceId of followSet.sources) {
    await followSource(sourceId, undefined, client);
  }
  for (const personalityId of followSet.personalities) {
    await followPersonality(personalityId, client);
  }

  logger.info("commit_cluster_follow_set_completed", {
    sources_followed: followSet.sources.length,
    personalities_followed: followSet.personalities.length,
  });
  return { sources_followed: followSet.sources.length, personalities_followed: followSet.personalities.length };
}

/** Surface a catalog read failure loudly (Rule 12) — never swallow into an empty result. */
function throwCatalogReadError(table: string, category: string, message: string, migration: string): never {
  logger.error("cluster_catalog_read_failed", {
    table,
    category,
    error_message: message,
    fix_suggestion: `Confirm ${migration} applied, the seed ran, and ${table} allows anon SELECT.`,
  });
  throw new Error(
    `Failed to read ${table} for category "${category}": ${message}. ` +
      `fix_suggestion: confirm ${migration} applied and ${table} is readable.`,
  );
}
