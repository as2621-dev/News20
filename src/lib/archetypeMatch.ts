/**
 * Archetype mapping (Phase 5c SP1) — turn a user's 8-category interest vector
 * into the nearest named **archetype** by cosine similarity against the seeded
 * `archetypes.archetype_vector` rows (migration 0009, `supabase/seed/archetypes.sql`).
 *
 * This is step 2 of the recommendation flow (`reference/archetypes.md` §1): the
 * recursive interest picker (Phase 5/phase-1e) produces a category vector over
 * the 8 pinned categories; this module maps it to the closest archetype so the
 * 5c source-recommendation screens render an instant, "people-like-you" grid via
 * {@link getRecommendedSources}. Below a similarity threshold the user is treated
 * as having no strong archetype and falls back to `balanced-generalist` (the flat
 * default, `reference/archetypes.md` §3 row 12).
 *
 * Pure, deterministic, no I/O: it takes the user's vector + the candidate
 * archetype rows and returns the match. The Supabase read of `archetypes` lives
 * in the caller (5c flow) — keeping this a pure function makes the cosine math
 * trivially unit-testable (CLAUDE.md mocking strategy / Rule 9).
 */

import { logger } from "@/lib/logger";
import type { Archetype } from "@/types/source";

/**
 * The 8 pinned interest categories (axis C1, `reference/ranking-spec.md` /
 * `reference/archetypes.md` §2), in canonical lowercase order. Cosine similarity
 * is computed over EXACTLY these keys so a user vector and an archetype vector
 * are always compared on the same 8 dimensions, regardless of key insertion
 * order in either JSON object.
 */
export const ARCHETYPE_CATEGORY_KEYS = [
  "ai",
  "geopolitics",
  "business",
  "environment",
  "politics",
  "tech",
  "sport",
  "arts",
] as const;

/** One of the 8 pinned category keys. */
export type ArchetypeCategoryKey = (typeof ARCHETYPE_CATEGORY_KEYS)[number];

/**
 * A user's interest vector over the 8 pinned categories. A partial map is
 * allowed — a missing key is treated as `0` (the user expressed no interest in
 * that category). Values need NOT be normalized: cosine similarity is
 * magnitude-invariant, so raw rolled-up follow weights work directly.
 */
export type InterestVector = Partial<Record<ArchetypeCategoryKey, number>>;

/**
 * The stable slug of the fallback archetype — the flat "no strong signal"
 * default (`reference/archetypes.md` §3 row 12). Used when the top cosine score
 * is below {@link ARCHETYPE_MATCH_THRESHOLD} OR when no scorable archetype exists.
 */
export const FALLBACK_ARCHETYPE_SLUG = "balanced-generalist";

/**
 * The minimum cosine similarity for the nearest archetype to "stick". Below
 * this, the user is mapped to {@link FALLBACK_ARCHETYPE_SLUG}.
 *
 * Reason: cosine ranges 0–1 for these all-non-negative vectors. 0.5 is the
 * midpoint and a sensible "more aligned than not" bar — a strongly themed user
 * (e.g. heavy ai+tech) scores well above it against their archetype. A flat /
 * uniform profile maps to `balanced-generalist` WITHOUT needing the fallback:
 * it is the only archetype that is itself uniform, so a flat user vector points
 * in exactly its direction (cosine = 1.0) and it wins the race over the themed
 * archetypes. The threshold therefore catches the OTHER case — a genuinely weak
 * / sparse vector whose nearest themed archetype is still a poor fit (cosine
 * < 0.5) — and steers it to the generalist fallback rather than a misleading
 * narrow archetype. See the DoD tests for both paths.
 */
export const ARCHETYPE_MATCH_THRESHOLD = 0.5;

/** The result of mapping a user's interest vector to an archetype. */
export interface ArchetypeMatch {
  /** The matched `archetypes.archetype_slug` (the stable key). */
  archetype_id: string;
  /** The matched archetype's human-readable label, or the slug if unknown. */
  archetype_label: string;
  /**
   * The cosine similarity (0–1) of the user's vector to the matched archetype's
   * vector. When the match fell back, this is the WINNING (pre-fallback) score so
   * callers can see how weak the signal was.
   */
  archetype_score: number;
  /** Whether the result is the {@link FALLBACK_ARCHETYPE_SLUG} fallback. */
  is_fallback: boolean;
}

/**
 * Cosine similarity of two vectors over the 8 pinned category keys.
 *
 * `cos(θ) = (a · b) / (‖a‖ · ‖b‖)`. Each vector is read key-by-key over
 * {@link ARCHETYPE_CATEGORY_KEYS} (missing key → 0). Returns `0` when either
 * vector has zero magnitude (an all-zero user vector or a degenerate archetype
 * vector) — an undefined cosine is treated as "no similarity", never `NaN`.
 *
 * @param userVector - The user's (possibly partial, possibly unnormalized) vector.
 * @param archetypeVector - An archetype's normalized weight map.
 * @returns The cosine similarity in `[0, 1]` for these non-negative vectors.
 */
function cosineSimilarity(userVector: InterestVector, archetypeVector: Record<string, number>): number {
  let dotProduct = 0;
  let userMagnitudeSquared = 0;
  let archetypeMagnitudeSquared = 0;

  for (const categoryKey of ARCHETYPE_CATEGORY_KEYS) {
    const userWeight = userVector[categoryKey] ?? 0;
    // Reason: archetype_vector is `Record<string, number>` from jsonb; a missing
    // key (shouldn't happen — seed asserts all 8) coerces to 0, never NaN.
    const archetypeWeight = Number(archetypeVector[categoryKey] ?? 0);
    dotProduct += userWeight * archetypeWeight;
    userMagnitudeSquared += userWeight * userWeight;
    archetypeMagnitudeSquared += archetypeWeight * archetypeWeight;
  }

  const denominator = Math.sqrt(userMagnitudeSquared) * Math.sqrt(archetypeMagnitudeSquared);
  if (denominator === 0) {
    return 0;
  }
  return dotProduct / denominator;
}

/**
 * Map a user's 8-category interest vector to the nearest archetype by cosine
 * similarity, falling back to `balanced-generalist` below
 * {@link ARCHETYPE_MATCH_THRESHOLD}.
 *
 * Pure function — the caller supplies the candidate `archetypes` rows (read
 * client-side from the public-read `archetypes` table). The fallback is resolved
 * from the SAME `archetypes` list (so the caller gets the real label/id); if the
 * fallback row is absent from the list, the result still carries
 * {@link FALLBACK_ARCHETYPE_SLUG} as `archetype_id` with the slug as its label.
 *
 * @param interestVector - The user's vector over the 8 pinned categories (partial OK).
 * @param archetypes - The candidate archetype rows to score against.
 * @returns The nearest {@link ArchetypeMatch}, or the fallback when no row clears the threshold.
 *
 * @example
 * const match = mapToArchetype({ ai: 0.6, tech: 0.4 }, seededArchetypes);
 * match.archetype_id; // "ai-frontier-tech"
 * match.is_fallback;  // false
 *
 * @example
 * const flat = mapToArchetype({ ai: 1, geopolitics: 1, business: 1, environment: 1,
 *                               politics: 1, tech: 1, sport: 1, arts: 1 }, seededArchetypes);
 * flat.archetype_id; // "balanced-generalist"
 */
export function mapToArchetype(interestVector: InterestVector, archetypes: Archetype[]): ArchetypeMatch {
  logger.info("map_to_archetype_started", {
    category_count: Object.keys(interestVector).length,
    candidate_count: archetypes.length,
  });

  let bestArchetype: Archetype | null = null;
  let bestScore = -1;

  for (const archetype of archetypes) {
    const score = cosineSimilarity(interestVector, archetype.archetype_vector);
    if (score > bestScore) {
      bestScore = score;
      bestArchetype = archetype;
    }
  }

  // Below-threshold (or no candidates / zero magnitude) → balanced-generalist.
  // Resolve the fallback from the SAME candidate list so the real label/id flows
  // through; degrade to the bare slug if the seed list omitted it.
  if (bestArchetype === null || bestScore < ARCHETYPE_MATCH_THRESHOLD) {
    const fallbackRow = archetypes.find((row) => row.archetype_slug === FALLBACK_ARCHETYPE_SLUG);
    const fallbackScore = bestScore < 0 ? 0 : bestScore;
    logger.info("map_to_archetype_fallback", {
      archetype_id: FALLBACK_ARCHETYPE_SLUG,
      best_score: fallbackScore,
      threshold: ARCHETYPE_MATCH_THRESHOLD,
      reason: bestArchetype === null ? "no_candidates" : "below_threshold",
    });
    return {
      archetype_id: fallbackRow?.archetype_slug ?? FALLBACK_ARCHETYPE_SLUG,
      archetype_label: fallbackRow?.archetype_label ?? FALLBACK_ARCHETYPE_SLUG,
      archetype_score: fallbackScore,
      is_fallback: true,
    };
  }

  logger.info("map_to_archetype_completed", {
    archetype_id: bestArchetype.archetype_slug,
    archetype_score: bestScore,
  });
  return {
    archetype_id: bestArchetype.archetype_slug,
    archetype_label: bestArchetype.archetype_label,
    archetype_score: bestScore,
    is_fallback: false,
  };
}
