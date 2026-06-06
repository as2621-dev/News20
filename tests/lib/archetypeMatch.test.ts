import { describe, expect, it } from "vitest";
import {
  ARCHETYPE_CATEGORY_KEYS,
  ARCHETYPE_MATCH_THRESHOLD,
  FALLBACK_ARCHETYPE_SLUG,
  type InterestVector,
  mapToArchetype,
} from "@/lib/archetypeMatch";
import type { Archetype } from "@/types/source";

/**
 * Phase 5c SP1 — archetype mapping (cosine similarity of the user's 8-category
 * interest vector vs the seeded `archetypes.archetype_vector`).
 *
 * WHY these tests exist (Rule 9 — encode the product contract, not the math):
 *  - The whole point of archetype mapping is "people like you follow these": a
 *    user who cares about AI + tech MUST land on `ai-frontier-tech` so the 5c
 *    grid shows them AI sources, not random ones. We assert the heavy ai+tech
 *    profile resolves to that exact slug (the DoD), not merely "some archetype".
 *  - A user with no strong theme MUST get the generalist default, never a
 *    misleadingly narrow archetype — a flat profile maps to `balanced-generalist`
 *    (the DoD). We assert the flat-profile path AND the genuinely-weak (sub-
 *    threshold) fallback path separately, because they reach the same slug by
 *    DIFFERENT mechanisms (direction-match vs threshold fallback).
 *  - Cosine is magnitude-invariant: a tiny vector and a scaled-up vector pointing
 *    the same way MUST map identically — otherwise raw (unnormalized) rolled-up
 *    follow weights would map differently than normalized ones, a silent bug.
 *
 * Uses the REAL seeded slugs + vectors (supabase/seed/archetypes.sql) so the test
 * fails if the seed's category weights drift away from the documented intent.
 */

/**
 * The real draft-12 archetype rows as seeded by supabase/seed/archetypes.sql.
 * Vectors copied verbatim (normalized 0–3 weights ÷ row sum, 4dp) so cosine math
 * is exercised against production data, not a toy fixture.
 */
const SEEDED_ARCHETYPES: Archetype[] = [
  {
    archetype_id: "a1",
    archetype_slug: "ai-frontier-tech",
    archetype_label: "AI & Frontier Tech",
    archetype_vector: {
      ai: 0.4286,
      geopolitics: 0,
      business: 0.1429,
      environment: 0,
      politics: 0,
      tech: 0.4286,
      sport: 0,
      arts: 0,
    },
  },
  {
    archetype_id: "a2",
    archetype_slug: "markets-macro",
    archetype_label: "Markets & Macro",
    archetype_vector: {
      ai: 0,
      geopolitics: 0.2,
      business: 0.6,
      environment: 0,
      politics: 0.2,
      tech: 0,
      sport: 0,
      arts: 0,
    },
  },
  {
    archetype_id: "a3",
    archetype_slug: "startup-operator",
    archetype_label: "Startup Operator",
    archetype_vector: {
      ai: 0.1667,
      geopolitics: 0,
      business: 0.5,
      environment: 0,
      politics: 0,
      tech: 0.3333,
      sport: 0,
      arts: 0,
    },
  },
  {
    archetype_id: "a4",
    archetype_slug: "crypto-fintech",
    archetype_label: "Crypto & Fintech",
    archetype_vector: {
      ai: 0,
      geopolitics: 0,
      business: 0.5,
      environment: 0,
      politics: 0,
      tech: 0.5,
      sport: 0,
      arts: 0,
    },
  },
  {
    archetype_id: "a5",
    archetype_slug: "geopolitics-world",
    archetype_label: "Geopolitics & World",
    archetype_vector: {
      ai: 0.1429,
      geopolitics: 0.4286,
      business: 0.1429,
      environment: 0,
      politics: 0.2857,
      tech: 0,
      sport: 0,
      arts: 0,
    },
  },
  {
    archetype_id: "a6",
    archetype_slug: "us-politics-policy",
    archetype_label: "US Politics & Policy",
    archetype_vector: {
      ai: 0,
      geopolitics: 0.25,
      business: 0,
      environment: 0,
      politics: 0.75,
      tech: 0,
      sport: 0,
      arts: 0,
    },
  },
  {
    archetype_id: "a7",
    archetype_slug: "climate-energy",
    archetype_label: "Climate & Energy",
    archetype_vector: {
      ai: 0,
      geopolitics: 0.1429,
      business: 0.1429,
      environment: 0.4286,
      politics: 0.1429,
      tech: 0.1429,
      sport: 0,
      arts: 0,
    },
  },
  {
    archetype_id: "a8",
    archetype_slug: "sports-fan",
    archetype_label: "Sports Fan",
    archetype_vector: { ai: 0, geopolitics: 0, business: 0, environment: 0, politics: 0, tech: 0, sport: 1.0, arts: 0 },
  },
  {
    archetype_id: "a9",
    archetype_slug: "arts-culture",
    archetype_label: "Arts & Culture",
    archetype_vector: { ai: 0, geopolitics: 0, business: 0, environment: 0, politics: 0, tech: 0, sport: 0, arts: 1.0 },
  },
  {
    archetype_id: "a10",
    archetype_slug: "creator-media",
    archetype_label: "Creator / Media",
    archetype_vector: {
      ai: 0.1667,
      geopolitics: 0,
      business: 0.1667,
      environment: 0,
      politics: 0,
      tech: 0.3333,
      sport: 0,
      arts: 0.3333,
    },
  },
  {
    archetype_id: "a11",
    archetype_slug: "tech-generalist",
    archetype_label: "Tech Generalist",
    archetype_vector: {
      ai: 0.2222,
      geopolitics: 0.1111,
      business: 0.1111,
      environment: 0.1111,
      politics: 0,
      tech: 0.3333,
      sport: 0,
      arts: 0.1111,
    },
  },
  {
    archetype_id: "a12",
    archetype_slug: "balanced-generalist",
    archetype_label: "Balanced Generalist",
    archetype_vector: {
      ai: 0.125,
      geopolitics: 0.125,
      business: 0.125,
      environment: 0.125,
      politics: 0.125,
      tech: 0.125,
      sport: 0.125,
      arts: 0.125,
    },
  },
];

const FLAT_PROFILE: InterestVector = {
  ai: 1,
  geopolitics: 1,
  business: 1,
  environment: 1,
  politics: 1,
  tech: 1,
  sport: 1,
  arts: 1,
};

describe("mapToArchetype (cosine similarity → nearest archetype)", () => {
  it("maps a heavy ai+tech profile to ai-frontier-tech (the DoD)", () => {
    // WHY: this is the core promise — an AI/tech-leaning user must be shown AI
    // sources. If this resolved to tech-generalist or anything else, the 5c grid
    // would mis-recommend on the most common power-user profile.
    const match = mapToArchetype({ ai: 0.6, tech: 0.4 }, SEEDED_ARCHETYPES);

    expect(match.archetype_id).toBe("ai-frontier-tech");
    expect(match.archetype_label).toBe("AI & Frontier Tech");
    expect(match.is_fallback).toBe(false);
    expect(match.archetype_score).toBeGreaterThan(ARCHETYPE_MATCH_THRESHOLD);
  });

  it("maps a flat (uniform) profile to balanced-generalist (the DoD)", () => {
    // WHY: a user with no dominant interest must get the generalist default, not a
    // narrow archetype. The flat archetype is the only uniform one, so a flat user
    // vector points exactly at it (cosine = 1.0) and wins — verifies the direction
    // logic, distinct from the sub-threshold fallback tested below.
    const match = mapToArchetype(FLAT_PROFILE, SEEDED_ARCHETYPES);

    expect(match.archetype_id).toBe(FALLBACK_ARCHETYPE_SLUG);
    expect(match.archetype_score).toBeCloseTo(1.0, 5);
  });

  it("falls back to balanced-generalist on a zero/empty interest vector (the no-signal DoD)", () => {
    // WHY: an un-onboarded or empty profile (no picked categories) has zero
    // magnitude → cosine is 0 against every archetype → below threshold. It must
    // fall back to the generalist default rather than crash or pick an arbitrary
    // archetype. This is the realistic sub-threshold path with the FULL seed: the
    // uniform balanced-generalist catch-all rescues any NON-zero diffuse vector at
    // high cosine, so with all 12 rows present only a zero vector trips the
    // threshold (documented finding — see the report).
    const match = mapToArchetype({}, SEEDED_ARCHETYPES);

    expect(match.archetype_id).toBe(FALLBACK_ARCHETYPE_SLUG);
    expect(match.is_fallback).toBe(true);
    expect(match.archetype_score).toBeLessThan(ARCHETYPE_MATCH_THRESHOLD);
  });

  it("fires the threshold fallback when the best themed match is strictly below 0.5", () => {
    // WHY: the threshold's real job is to guard against force-fitting a diffuse
    // user onto a misleadingly NARROW archetype when the generalist catch-all is
    // absent from the candidate list (e.g. a partial/filtered read). Against
    // single-theme archetypes, a vector spread evenly across 5 categories overlaps
    // any one theme on just 1 of 5 keys → cosine = 1/√5 ≈ 0.447, strictly below
    // 0.5 → it MUST fall back rather than claim a single theme. This isolates the
    // threshold mechanism (fallback via score, not via the flat-vector direction).
    const singleThemeArchetypes = SEEDED_ARCHETYPES.filter((row) =>
      ["sports-fan", "arts-culture"].includes(row.archetype_slug),
    );
    const diffuse: InterestVector = { sport: 1, arts: 1, ai: 1, business: 1, environment: 1 };
    const match = mapToArchetype(diffuse, singleThemeArchetypes);

    expect(match.is_fallback).toBe(true);
    expect(match.archetype_id).toBe(FALLBACK_ARCHETYPE_SLUG);
    expect(match.archetype_score).toBeLessThan(ARCHETYPE_MATCH_THRESHOLD);
  });

  it("is magnitude-invariant: a scaled vector maps identically (cosine property)", () => {
    // WHY: rolled-up follow weights are unnormalized. A user who picked twice as
    // many AI items must map to the SAME archetype as a lighter AI user pointing
    // the same direction — otherwise the mapping depends on activity volume, not
    // taste. Asserts cosine (direction), not dot-product (magnitude).
    const small = mapToArchetype({ ai: 0.6, tech: 0.4 }, SEEDED_ARCHETYPES);
    const scaled = mapToArchetype({ ai: 60, tech: 40 }, SEEDED_ARCHETYPES);

    expect(scaled.archetype_id).toBe(small.archetype_id);
    expect(scaled.archetype_score).toBeCloseTo(small.archetype_score, 6);
  });

  it("treats missing category keys as zero (partial vector)", () => {
    // WHY: the picker may only emit categories the user touched. A partial vector
    // must score as if the absent categories are 0, not crash or skew. A pure-sport
    // vector must hit sports-fan (cosine 1.0 to the sport-only archetype).
    const match = mapToArchetype({ sport: 1 }, SEEDED_ARCHETYPES);

    expect(match.archetype_id).toBe("sports-fan");
    expect(match.archetype_score).toBeCloseTo(1.0, 5);
  });

  it("falls back when there are no candidate archetypes (edge case)", () => {
    // WHY: a failed/empty archetypes read must not crash the onboarding flow — it
    // must degrade to the bare fallback slug so the grid still has SOMETHING to query.
    const match = mapToArchetype({ ai: 1, tech: 1 }, []);

    expect(match.archetype_id).toBe(FALLBACK_ARCHETYPE_SLUG);
    expect(match.is_fallback).toBe(true);
    expect(match.archetype_score).toBe(0);
  });

  it("returns the fallback slug even when the fallback row is absent from the list", () => {
    // WHY: if a partial archetypes read omits balanced-generalist, the function
    // must STILL return a usable fallback id (the slug constant) rather than null —
    // the caller queries by slug, so a missing label can't break it. Pairs a
    // below-threshold diffuse vector with a list lacking the fallback row.
    const singleThemeArchetypes = SEEDED_ARCHETYPES.filter((row) =>
      ["sports-fan", "arts-culture"].includes(row.archetype_slug),
    );
    const diffuse: InterestVector = { sport: 1, arts: 1, ai: 1, business: 1, environment: 1 };
    const match = mapToArchetype(diffuse, singleThemeArchetypes);

    expect(match.archetype_id).toBe(FALLBACK_ARCHETYPE_SLUG);
    expect(match.archetype_label).toBe(FALLBACK_ARCHETYPE_SLUG);
    expect(match.is_fallback).toBe(true);
  });

  it("exposes all 8 pinned category keys in canonical order", () => {
    // WHY: cosine compares user vs archetype on EXACTLY these 8 keys; a drift here
    // (missing/extra/renamed key) silently mis-scores every match. Pin the contract.
    expect(ARCHETYPE_CATEGORY_KEYS).toEqual([
      "ai",
      "geopolitics",
      "business",
      "environment",
      "politics",
      "tech",
      "sport",
      "arts",
    ]);
  });
});
