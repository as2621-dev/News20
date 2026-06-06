/**
 * Source recommendations (Phase 5c SP1) — turn a user's matched archetype(s)
 * into an instant, balanced, popularity-ranked grid of catalog sources for ONE
 * axis (YouTube channels / podcasts / X accounts / personalities).
 *
 * This is step 3 of the recommendation flow (`reference/archetypes.md` §1): after
 * {@link mapToArchetype} picks the user's archetype(s), this reads the public
 * `content_sources` catalog (via the Phase 5b data layer, `src/lib/sources.ts`),
 * **round-robin merges** the per-archetype top-K so a multi-archetype user gets a
 * visibly balanced grid (ported from TL;DW `api/sources/recommended/route.ts`
 * :148-189, reuse-map §5), applies a small **sub-niche boost** to re-order within
 * popularity ties (open question #1: small linear boost for v1), and annotates
 * each result with `is_already_added` against the user's existing follows.
 *
 * Same client pattern as `src/lib/sources.ts`: every fn takes an optional
 * `client` (injected in tests) defaulting to the shared browser anon client —
 * there is NO server runtime on device (Capacitor static export), so this ships
 * as client-side Supabase reads under RLS. Catalog reads are anon-public; the
 * follow read is owner-scoped and DEGRADES GRACEFULLY to "no follows" when the
 * user is still anon (onboarding browses the catalog before/around sign-in), so
 * an anon browse never throws — `is_already_added` is simply `false` for all.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getUserSources, listSourcesByArchetype } from "@/lib/sources";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { ContentSource, ContentSourceType } from "@/types/source";

/** Default number of recommendations returned for a screen (one axis). */
const DEFAULT_RECOMMENDATION_LIMIT = 12;

/**
 * Per-archetype over-fetch multiplier. We pull `limit × this` per archetype so
 * the round-robin merge + sub-niche re-rank still have enough candidates after
 * cross-archetype dedup, then trim to `limit`. Modest — keeps the catalog read cheap.
 */
const PER_ARCHETYPE_OVERFETCH = 2;

/**
 * Linear popularity boost per matched sub-niche tag (open question #1 → small
 * linear boost for v1).
 *
 * Reason: a sub-niche pick (e.g. "AI chips") should nudge matching sources up
 * WITHOUT overriding the archetype's popularity ordering — `popularity_score` is
 * 0–100, so 0.1 per matched `topic_tag` is deliberately tiny: it only breaks ties
 * / re-orders near-equals (a source needs ~10 matched sub-niches to move one
 * popularity point), exactly the "modest re-weight" the plan asks for. Bigger
 * re-rank strength is a tuning knob left to a later phase, not v1.
 */
const SUB_NICHE_BOOST_PER_MATCH = 0.1;

/** Options for {@link getRecommendedSources}. */
export interface RecommendedSourcesOptions {
  /**
   * The archetype slugs to recommend for, in priority order (typically the top-1
   * or top-2 from {@link mapToArchetype}). Each gets its own per-archetype top-K
   * list, round-robin merged so the grid is balanced across them.
   */
  archetypes: string[];
  /**
   * The user's picked sub-niche tags (8-category-aligned `topic_tags`). Sources
   * whose `topic_tags` overlap these get a small popularity boost. Optional.
   */
  subNiches?: string[];
  /** Max recommendations to return (default {@link DEFAULT_RECOMMENDATION_LIMIT}). */
  limit?: number;
  /** Optional Supabase client (injected in tests). Defaults to the shared browser client. */
  client?: SupabaseClient;
}

/** A catalog source annotated with whether the user already follows it. */
export interface RecommendedSource extends ContentSource {
  /** True when this source is already in the user's `user_content_sources` follow set. */
  is_already_added: boolean;
}

/**
 * Resolve the authed user's existing follow set as a `Set<source_id>`, degrading
 * to an EMPTY set when the user is signed out (the anon onboarding browse path).
 *
 * Reuses the Phase 5b {@link getUserSources} helper (no raw SQL). That helper
 * throws loudly when signed out (a source FOLLOW is an authed action); a READ for
 * annotation, however, must not crash an anon browse — so we swallow ONLY the
 * signed-out case (treating it as "no follows") and re-throw any real query error
 * (Rule 12: never hide a genuine failure).
 *
 * @param client - The Supabase client to read follows with.
 * @returns The set of `source_id`s the user already follows (empty when anon).
 */
async function resolveAlreadyAddedSourceIds(client: SupabaseClient): Promise<Set<string>> {
  // Resolve auth state first so we never call the throwing helper while anon.
  const { data, error } = await client.auth.getUser();
  if (error || !data.user) {
    logger.info("recommended_sources_anon_browse", {
      reason: "no_active_session",
      note: "is_already_added defaults to false for all rows until sign-in.",
    });
    return new Set<string>();
  }

  const follows = await getUserSources(client);
  return new Set(follows.map((follow) => follow.source_id));
}

/**
 * Recommend catalog sources for ONE axis, balanced across the user's top
 * archetype(s), popularity-ranked, sub-niche-boosted, and follow-annotated.
 *
 * Per-archetype reads run in parallel (one {@link listSourcesByArchetype} call
 * each, single-element persona overlap so each list is THAT archetype's
 * popularity-desc top-K). The lists are **round-robin merged** — rank-1 of
 * archetype A, then rank-1 of B, then rank-2 of A, … — deduped by `source_id`
 * across archetypes (a source serving both archetypes appears once, at its first
 * encounter). The merged head is sub-niche re-ranked (stable) and trimmed to
 * `limit`, then each row is annotated with `is_already_added`.
 *
 * A single archetype skips the merge (the one list IS the order). An empty
 * `archetypes` array returns `[]` without a round-trip.
 *
 * @param kind - The single source axis to recommend ({@link ContentSourceType}).
 * @param options - {@link RecommendedSourcesOptions} (archetypes, subNiches, limit, client).
 * @returns The merged, ranked, follow-annotated {@link RecommendedSource} rows (≤ limit).
 * @throws If a catalog read fails, or the authed follow read fails (surfaced — Rule 12).
 *
 * @example
 * const channels = await getRecommendedSources("youtube_channel", {
 *   archetypes: ["ai-frontier-tech", "startup-operator"],
 *   subNiches: ["ai"],
 *   limit: 12,
 * });
 * channels[0].source_name;       // most popular AI channel
 * channels[0].is_already_added;  // false if the user hasn't followed it
 */
export async function getRecommendedSources(
  kind: ContentSourceType,
  options: RecommendedSourcesOptions,
): Promise<RecommendedSource[]> {
  const { archetypes, subNiches = [], limit = DEFAULT_RECOMMENDATION_LIMIT } = options;
  const client = options.client ?? getSupabaseBrowserClient();

  logger.info("get_recommended_sources_started", { kind, archetypes, sub_niche_count: subNiches.length, limit });

  if (archetypes.length === 0) {
    logger.info("get_recommended_sources_completed", { kind, returned: 0, reason: "empty_archetypes" });
    return [];
  }

  // Over-fetch per archetype so the merge + sub-niche re-rank + cross-archetype
  // dedup still leave ≥ limit candidates. Each call is single-archetype so its
  // list is that archetype's own popularity-desc ranking (the round-robin needs
  // per-archetype ranks, not a co-mingled overlap).
  const perArchetypeLimit = limit * PER_ARCHETYPE_OVERFETCH;
  const perArchetypeLists = await Promise.all(
    archetypes.map((archetypeSlug) => listSourcesByArchetype([archetypeSlug], kind, perArchetypeLimit, client)),
  );

  // Merge a CANDIDATE POOL larger than `limit` (the over-fetch headroom) so the
  // sub-niche re-rank can promote a slightly-lower-ranked but sub-niche-matching
  // source into the final `limit`, not just reorder the already-capped head.
  const candidatePoolSize = limit * PER_ARCHETYPE_OVERFETCH;
  const merged = roundRobinMerge(perArchetypeLists, candidatePoolSize);
  const ranked = applySubNicheBoost(merged, subNiches).slice(0, limit);

  const alreadyAddedSourceIds = await resolveAlreadyAddedSourceIds(client);
  const results: RecommendedSource[] = ranked.map((source) => ({
    ...source,
    is_already_added: alreadyAddedSourceIds.has(source.source_id),
  }));

  logger.info("get_recommended_sources_completed", {
    kind,
    returned: results.length,
    already_added: results.filter((row) => row.is_already_added).length,
  });
  return results;
}

/**
 * Round-robin merge per-archetype popularity-ranked lists into one balanced list,
 * deduped by `source_id` across archetypes (ported from TL;DW
 * `api/sources/recommended/route.ts:148-189`).
 *
 * Walks rank by rank: at each rank it takes that rank's row from every
 * archetype's list in order, skipping any already seen, until the merged list
 * reaches `limit` or every list is exhausted. A single list short-circuits to a
 * deduped copy (still trimmed to `limit`) — its order IS the popularity order.
 *
 * @param perArchetypeLists - One popularity-desc list per archetype (parallel to the archetypes arg).
 * @param limit - Max rows to emit.
 * @returns The interleaved, deduped, popularity-balanced rows (≤ limit).
 */
function roundRobinMerge(perArchetypeLists: ContentSource[][], limit: number): ContentSource[] {
  const seenSourceIds = new Set<string>();
  const merged: ContentSource[] = [];

  // The longest list bounds how many ranks we walk.
  const maxListLength = perArchetypeLists.reduce((longest, list) => Math.max(longest, list.length), 0);

  for (let rank = 0; rank < maxListLength && merged.length < limit; rank += 1) {
    for (const list of perArchetypeLists) {
      if (merged.length >= limit) {
        break;
      }
      const source = list[rank];
      if (!source || seenSourceIds.has(source.source_id)) {
        continue;
      }
      seenSourceIds.add(source.source_id);
      merged.push(source);
    }
  }

  return merged;
}

/**
 * Apply the small linear sub-niche boost and STABLE-sort the merged list by the
 * boosted score (desc). With no sub-niches the order is unchanged (the boost is
 * zero for every row, so the stable sort preserves the round-robin order).
 *
 * Reason: the boost re-orders within near-equals only ({@link SUB_NICHE_BOOST_PER_MATCH}
 * is tiny vs the 0–100 popularity scale), so the archetype balance + popularity
 * ranking dominate; a sub-niche pick just nudges its matching sources up among
 * ties. Sort is stable (index tiebreak) so equal-boost rows keep round-robin order.
 *
 * @param sources - The round-robin merged sources.
 * @param subNiches - The user's picked sub-niche tags (matched against `topic_tags`).
 * @returns A new array re-ordered by boosted score (stable on ties).
 */
function applySubNicheBoost(sources: ContentSource[], subNiches: string[]): ContentSource[] {
  if (subNiches.length === 0) {
    return sources;
  }
  const subNicheSet = new Set(subNiches);

  const scored = sources.map((source, index) => {
    const matchedTagCount = source.topic_tags.reduce((count, tag) => (subNicheSet.has(tag) ? count + 1 : count), 0);
    const boostedScore = source.popularity_score + matchedTagCount * SUB_NICHE_BOOST_PER_MATCH;
    return { source, boostedScore, index };
  });

  // Stable: higher boosted score first; ties keep the original (round-robin) order.
  scored.sort((a, b) => b.boostedScore - a.boostedScore || a.index - b.index);
  return scored.map((entry) => entry.source);
}
