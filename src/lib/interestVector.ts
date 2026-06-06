/**
 * Interest-vector roll-up (Phase 5c SP4, LOGIC HALF) — read a user's persisted
 * follows and roll them up into the 8-category {@link InterestVector} that
 * {@link mapToArchetype} (SP1) consumes.
 *
 * This is the missing SP1 ⇄ SP4 hand-off: SP1's `mapToArchetype` is a PURE
 * function over a user vector + the candidate archetype rows, but nothing yet
 * BUILDS that user vector from the DB. This module does exactly that — it reads
 * the two persisted follow axes the recursive picker (Phase 5/5a) writes:
 *
 *   - `user_interest_profile` — per-interest weighted rows (`profile_weight`),
 *     each pointing at an `interests` taxonomy node (joined here for the slug).
 *   - `user_entity_follows`   — per-entity weighted rows (`follow_weight`), whose
 *     `entity_id` is itself a path-derived slug (`ai/.../openai`) — its root
 *     segment IS the pinned-key category, so no extra join is needed.
 *
 * Both axes are summed into ONE vector over the 8 PINNED archetype keys
 * (`ai|geopolitics|business|environment|politics|tech|sport|arts` — the exact
 * {@link ARCHETYPE_CATEGORY_KEYS} cosine math runs over). The vector is NOT
 * normalized (cosine is magnitude-invariant — SP1 relies on this), so raw summed
 * weights flow straight in.
 *
 * ── The slug → pinned-key mapping (the load-bearing decision) ────────────────
 * The picker/screen taxonomy and the 8 PINNED archetype keys are DIFFERENT axes
 * (SP1 note): `agents/pipeline/categories.py` `SLUG_TO_CATEGORY` maps interest
 * roots into the 8 SCREEN categories (`world_politics|tech_science|markets|sport|
 * culture|…`), which are NOT the pinned keys. No mapping into the pinned 8 was
 * documented anywhere, so {@link INTEREST_ROOT_TO_PINNED_KEY} +
 * {@link AI_INTEREST_ROOT} below define it. See the `// Reason:` comments and the
 * SP4a execution report for the full table + rationale.
 *
 * Same client pattern as `src/lib/sources.ts` / `sourceRecommendations.ts`: an
 * optional injected `client` defaulting to the shared browser client; the read is
 * owner-scoped (RLS pins both per-user tables to `auth.uid()`) and DEGRADES
 * GRACEFULLY to a ZERO vector for a signed-out / brand-new user — which makes
 * `mapToArchetype` fall back to `balanced-generalist` (its all-zero → fallback
 * path), exactly the new-user "no strong signal" semantics.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import type { ArchetypeCategoryKey, InterestVector } from "@/lib/archetypeMatch";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** The owner-scoped weighted interest table (migration 0003). */
const USER_INTEREST_PROFILE_TABLE = "user_interest_profile";

/** The owner-scoped weighted entity-follow table (migration 0007). */
const USER_ENTITY_FOLLOWS_TABLE = "user_entity_follows";

/**
 * The interest ROOT slug whose subtree (`tech.ai`, `tech.ai.llms`) is the `ai`
 * PINNED key rather than `tech`. AI is a top-level pinned key (and a top-level
 * ENTITY category, `ai/...`), but in the `interests` taxonomy it lives only as a
 * sub-node of the `tech` root. So a `tech.ai*` interest must roll up to `ai`, not
 * `tech` — otherwise an AI-frontier topic user would score `tech` and never the
 * `ai` dimension `ai-frontier-tech` weights heavily. Reason: align the topic axis
 * with the entity axis (`ai/...`) so both contribute to the SAME pinned key.
 */
const AI_INTEREST_ROOT = "tech";

/** The second slug segment that promotes a `tech.*` interest to the `ai` key. */
const AI_INTEREST_SUBROOT = "ai";

/**
 * Map an interest taxonomy ROOT slug → its PINNED archetype-category key.
 *
 * Keys are the depth-0 `interests` roots actually seeded (`supabase/seed/
 * interests.sql`): `world, business, tech, sport, health, entertainment, climate,
 * lifestyle, crypto, science` — plus the segment-accent aliases the locked screen
 * map names (`geopolitics, markets, wildcard`) so the map matches that taxonomy
 * verbatim and stays forward-compatible. Values are the 8 PINNED keys
 * (`ai|geopolitics|business|environment|politics|tech|sport|arts`).
 *
 * Reason (the non-obvious rolls): the pinned axis has `geopolitics`, `politics`
 * and `environment` keys with no 1:1 interest root, and NO `health`/`crypto`/
 * `markets` pinned key — so we fold by semantics, mirroring the interest segment
 * accents (`interests.sql`):
 *   - `world` → `geopolitics`  ("World & Politics" carries the `geopolitics`
 *     segment accent; the pinned axis splits geopolitics vs politics, but the
 *     seeded interest root is the geopolitics-accented world bucket).
 *   - `climate` → `environment` ("Climate & Environment" → the environment key).
 *   - `business`/`markets`/`crypto` → `business` (the markets-accented roots all
 *     fold into the one `business` pinned key — there is no markets/crypto key).
 *   - `tech`/`science` → `tech` (hard-science folds into tech; `tech.ai*` is the
 *     {@link AI_INTEREST_ROOT} exception → `ai`).
 *   - `health` → `tech` (no health key; health/biotech is closest to the tech/
 *     science axis the archetypes model).
 *   - `entertainment`/`lifestyle`/`wildcard` → `arts` (culture long-tail → arts).
 *   - `sport` → `sport` (direct).
 * A root NOT in this map is dropped from the vector (logged once) rather than
 * mis-bucketed — a wrong bucket is a silent miscategorization (Rule 12).
 */
export const INTEREST_ROOT_TO_PINNED_KEY: Readonly<Record<string, ArchetypeCategoryKey>> = {
  // World & Politics → geopolitics
  world: "geopolitics",
  geopolitics: "geopolitics",
  // Environment
  climate: "environment",
  // Business (markets/crypto fold in — no separate pinned key)
  business: "business",
  markets: "business",
  crypto: "business",
  // Tech & Science (tech.ai* is the AI exception, handled in pinnedKeyForInterestSlug)
  tech: "tech",
  science: "tech",
  health: "tech",
  // Sport
  sport: "sport",
  // Arts & Culture (entertainment/lifestyle/wildcard long-tail)
  entertainment: "arts",
  lifestyle: "arts",
  wildcard: "arts",
};

/**
 * Map an ENTITY root segment → its PINNED archetype-category key. Entity ids are
 * path-derived slugs (`ai/.../openai`), and the seeded registry's top-level
 * segments are EXACTLY the pinned keys that have entity coverage (`ai, arts,
 * business, geopolitics, sport, tech` — `supabase/seed/entities.sql`). So this is
 * mostly an identity map, declared explicitly (not assumed) so an unexpected root
 * is dropped + logged, never silently mis-bucketed.
 */
export const ENTITY_ROOT_TO_PINNED_KEY: Readonly<Record<string, ArchetypeCategoryKey>> = {
  ai: "ai",
  arts: "arts",
  business: "business",
  geopolitics: "geopolitics",
  sport: "sport",
  tech: "tech",
  // Forward-compatible: future registry roots that match the pinned axis.
  environment: "environment",
  politics: "politics",
};

/** One `user_interest_profile` row with its joined slug (the read projection). */
interface InterestProfileVectorRow {
  /** `profile_weight numeric` — the per-interest weight (depth-scaled, §1). */
  profile_weight: number;
  /** Embedded `interests(interest_slug)` — the dotted taxonomy slug, or null. */
  interests: { interest_slug: string } | { interest_slug: string }[] | null;
}

/** One `user_entity_follows` row contributing to the roll-up. */
interface EntityFollowVectorRow {
  /** `entity_id text` — the path-derived slug; its root segment is the category. */
  entity_id: string;
  /** `follow_weight numeric` — the §7 intent weight (custom > more ≥ seed). */
  follow_weight: number;
}

/**
 * Resolve the PINNED key for an interest slug, honoring the `tech.ai*` → `ai`
 * exception. Returns `null` when the root is unmapped (dropped, not mis-bucketed).
 *
 * @param interestSlug - A dotted taxonomy slug (`sport.cricket.india`, `tech.ai.llms`).
 * @returns The pinned category key, or `null` when the root is unknown.
 */
function pinnedKeyForInterestSlug(interestSlug: string): ArchetypeCategoryKey | null {
  if (!interestSlug) {
    return null;
  }
  const segments = interestSlug.split(".");
  const rootSegment = segments[0];
  // Reason: the AI subtree (`tech.ai`, `tech.ai.llms`) is the `ai` pinned key, not
  // `tech` — align it with the `ai/...` entity axis so both feed the same dimension.
  if (rootSegment === AI_INTEREST_ROOT && segments[1] === AI_INTEREST_SUBROOT) {
    return "ai";
  }
  return INTEREST_ROOT_TO_PINNED_KEY[rootSegment] ?? null;
}

/**
 * Resolve the PINNED key for an entity id from its leading path segment. Returns
 * `null` when the root is unmapped (dropped, not mis-bucketed).
 *
 * @param entityId - A path-derived entity slug (`ai/foundation-models-llms/.../openai`).
 * @returns The pinned category key, or `null` when the root is unknown.
 */
function pinnedKeyForEntityId(entityId: string): ArchetypeCategoryKey | null {
  if (!entityId) {
    return null;
  }
  const rootSegment = entityId.split("/")[0];
  return ENTITY_ROOT_TO_PINNED_KEY[rootSegment] ?? null;
}

/**
 * Normalize PostgREST's embedded relation (object OR single-element array OR null)
 * to the slug string, or `null` when absent. PostgREST returns a to-one embed as
 * an object, but the typed client can surface it as a one-element array — handle both.
 */
function extractInterestSlug(embedded: InterestProfileVectorRow["interests"]): string | null {
  if (!embedded) {
    return null;
  }
  const node = Array.isArray(embedded) ? embedded[0] : embedded;
  return node && typeof node.interest_slug === "string" ? node.interest_slug : null;
}

/**
 * Add a weighted contribution into the accumulating vector (a missing key starts
 * at 0). Mutates `vector` in place for a tight single-pass roll-up.
 */
function addWeight(vector: InterestVector, key: ArchetypeCategoryKey, weight: number): void {
  vector[key] = (vector[key] ?? 0) + weight;
}

/**
 * Roll a user's persisted follows up into the 8-category {@link InterestVector}
 * that {@link mapToArchetype} consumes.
 *
 * Reads BOTH owner-scoped axes under RLS — `user_interest_profile` (joined to
 * `interests` for the slug) and `user_entity_follows` — maps each row's
 * slug/entity-root to its PINNED key ({@link INTEREST_ROOT_TO_PINNED_KEY} /
 * {@link ENTITY_ROOT_TO_PINNED_KEY}, with the `tech.ai*` → `ai` exception), and
 * SUMS the per-row weights (`profile_weight` / `follow_weight`) into one vector.
 * The result is NOT normalized (cosine is magnitude-invariant).
 *
 * Empty / signed-out / brand-new user → an EMPTY (zero-magnitude) vector. Fed to
 * `mapToArchetype` that yields `balanced-generalist` (the no-strong-signal
 * fallback), so a new user still gets a sensible balanced recommendation grid.
 *
 * The read DEGRADES gracefully when signed out (anon onboarding browses before/
 * around sign-in): no auth → zero vector, NO throw. A genuine query error IS
 * surfaced (Rule 12) — only the no-session case is swallowed.
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The summed {@link InterestVector} over the 8 pinned keys (empty for a new/anon user).
 * @throws If either authed read fails for a real reason (surfaced, never swallowed — Rule 12).
 *
 * @example
 * // A user heavy in AI + tech follows:
 * const vector = await rollUpInterestVector();
 * // vector ≈ { ai: 4.0, tech: 1.5 }  → mapToArchetype → "ai-frontier-tech"
 *
 * @example
 * // A brand-new (or signed-out) user:
 * const empty = await rollUpInterestVector();
 * // empty === {}  → mapToArchetype → "balanced-generalist"
 */
export async function rollUpInterestVector(
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<InterestVector> {
  // Resolve auth FIRST so an anon onboarding browse returns a zero vector instead
  // of erroring on the owner-scoped reads (mirrors sourceRecommendations.ts).
  const { data: authData, error: authError } = await client.auth.getUser();
  if (authError || !authData.user) {
    logger.info("roll_up_interest_vector_anon", {
      reason: "no_active_session",
      note: "Returns a zero vector → mapToArchetype falls back to balanced-generalist.",
    });
    return {};
  }

  const userId = authData.user.id;
  logger.info("roll_up_interest_vector_started", { user_id: userId });

  const vector: InterestVector = {};
  let droppedRootCount = 0;

  // ── Topic axis: user_interest_profile ⋈ interests(interest_slug) ────────────
  // The embed recovers the dotted slug from the profile_interest_id FK in one read
  // (PostgREST to-one embed; precedent: sourceSearch.ts content_sources!inner).
  const { data: profileData, error: profileError } = await client
    .from(USER_INTEREST_PROFILE_TABLE)
    .select("profile_weight, interests(interest_slug)")
    .eq("profile_user_id", userId)
    .returns<InterestProfileVectorRow[]>();

  if (profileError) {
    logger.error("roll_up_interest_profile_read_failed", {
      error_message: profileError.message,
      fix_suggestion:
        "Confirm migration 0003 applied and user_interest_profile↔interests is readable under the owner RLS.",
    });
    throw new Error(
      `Failed to read interest profile for roll-up: ${profileError.message}. ` +
        "fix_suggestion: confirm migration 0003 applied and the owner read is permitted.",
    );
  }

  for (const row of profileData ?? []) {
    const slug = extractInterestSlug(row.interests);
    if (!slug) {
      continue;
    }
    const key = pinnedKeyForInterestSlug(slug);
    if (key === null) {
      droppedRootCount += 1;
      logger.warn("roll_up_interest_slug_unmapped", {
        interest_slug: slug,
        fix_suggestion: "Add the interest root to INTEREST_ROOT_TO_PINNED_KEY if it should score a pinned category.",
      });
      continue;
    }
    addWeight(vector, key, Number(row.profile_weight) || 0);
  }

  // ── Entity axis: user_entity_follows (entity_id root = pinned key) ───────────
  const { data: entityData, error: entityError } = await client
    .from(USER_ENTITY_FOLLOWS_TABLE)
    .select("entity_id, follow_weight")
    .eq("follow_user_id", userId)
    .returns<EntityFollowVectorRow[]>();

  if (entityError) {
    logger.error("roll_up_entity_follows_read_failed", {
      error_message: entityError.message,
      fix_suggestion: "Confirm migration 0007 applied and user_entity_follows is readable under the owner RLS.",
    });
    throw new Error(
      `Failed to read entity follows for roll-up: ${entityError.message}. ` +
        "fix_suggestion: confirm migration 0007 applied and the owner read is permitted.",
    );
  }

  for (const row of entityData ?? []) {
    const key = pinnedKeyForEntityId(row.entity_id);
    if (key === null) {
      droppedRootCount += 1;
      logger.warn("roll_up_entity_root_unmapped", {
        entity_id: row.entity_id,
        fix_suggestion: "Add the entity root to ENTITY_ROOT_TO_PINNED_KEY if it should score a pinned category.",
      });
      continue;
    }
    addWeight(vector, key, Number(row.follow_weight) || 0);
  }

  logger.info("roll_up_interest_vector_completed", {
    user_id: userId,
    profile_rows: (profileData ?? []).length,
    entity_rows: (entityData ?? []).length,
    scored_keys: Object.keys(vector).length,
    dropped_root_count: droppedRootCount,
  });
  return vector;
}
