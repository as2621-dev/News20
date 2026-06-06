import { describe, expect, it, vi } from "vitest";
import { mapToArchetype } from "@/lib/archetypeMatch";
import { ENTITY_ROOT_TO_PINNED_KEY, INTEREST_ROOT_TO_PINNED_KEY, rollUpInterestVector } from "@/lib/interestVector";
import type { Archetype } from "@/types/source";

/**
 * Phase 5c SP4a — interest-vector roll-up (the SP1 ⇄ SP4 hand-off).
 *
 * WHY these tests exist (Rule 9 — encode the product contract, not call shapes):
 *  - The roll-up's WHOLE job is to feed `mapToArchetype` a vector that lands the
 *    user on the RIGHT archetype. A wrong slug→pinned-key mapping is a SILENT
 *    miscategorization: an AI+tech user would see random sources, not AI ones. So
 *    the headline test rolls a real-shaped AI+tech profile up and asserts the
 *    INTEGRATED result (roll-up → mapToArchetype) is exactly `ai-frontier-tech`.
 *  - A brand-new / signed-out user MUST get the balanced-generalist fallback, not
 *    a narrow archetype — so an empty/anon roll-up yields a zero vector that maps
 *    to `balanced-generalist`.
 *  - Both axes (topic profile + entity follows) MUST contribute, and the
 *    `tech.ai*` → `ai` exception MUST hold (it aligns the topic axis with the
 *    `ai/...` entity axis — without it the `ai` dimension never lights up).
 *  - A genuine read error MUST surface (Rule 12); only the no-session case is
 *    swallowed.
 *
 * Mocks Supabase at the client boundary (CLAUDE.md mocking strategy), mirroring
 * tests/lib/sources.test.ts + tests/lib/sourceRecommendations.test.ts.
 */

const AUTHED_USER_ID = "user-uuid-rollup";

/** The real draft-12 archetypes (subset that the assertions need to win against). */
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
    archetype_id: "a8",
    archetype_slug: "sports-fan",
    archetype_label: "Sports Fan",
    archetype_vector: { ai: 0, geopolitics: 0, business: 0, environment: 0, politics: 0, tech: 0, sport: 1.0, arts: 0 },
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

/** One profile row as the embedded-select read returns it (slug joined from interests). */
interface ProfileRow {
  profile_weight: number;
  interests: { interest_slug: string } | { interest_slug: string }[] | null;
}

/** One entity-follow row as the read returns it. */
interface EntityRow {
  entity_id: string;
  follow_weight: number;
}

/**
 * Build a fake Supabase client serving the two roll-up reads:
 *  - `from("user_interest_profile").select(embed).eq().returns()` → profile rows
 *  - `from("user_entity_follows").select().eq().returns()`        → entity rows
 * plus `auth.getUser()`. A per-table `error` injects a surfaced failure.
 */
function makeRollUpClient(options: {
  user: { id: string } | null;
  profileRows?: ProfileRow[];
  entityRows?: EntityRow[];
  profileError?: { message: string };
  entityError?: { message: string };
}) {
  const getUser = vi.fn().mockResolvedValue({ data: { user: options.user }, error: null });
  const eqCalls: Record<string, Array<[string, unknown]>> = { user_interest_profile: [], user_entity_follows: [] };

  function from(table: string) {
    const result =
      table === "user_interest_profile"
        ? { data: options.profileRows ?? [], error: options.profileError ?? null }
        : { data: options.entityRows ?? [], error: options.entityError ?? null };
    return {
      select: () => ({
        eq: (column: string, value: unknown) => {
          eqCalls[table]?.push([column, value]);
          return { returns: vi.fn().mockResolvedValue(result) };
        },
      }),
    };
  }

  return { client: { auth: { getUser }, from } as never, getUser, eqCalls };
}

describe("rollUpInterestVector (DB → 8-pinned-key vector)", () => {
  it("rolls an AI+tech profile up to a vector that mapToArchetype maps to ai-frontier-tech (the DoD)", async () => {
    // WHY: this is the SP1↔SP4 contract end-to-end. A user whose topic follows are
    // under tech.ai* (→ ai key) + tech (→ tech key) and whose entity follows are
    // ai/* labs MUST resolve to ai-frontier-tech — the whole point of the matcher.
    const { client } = makeRollUpClient({
      user: { id: AUTHED_USER_ID },
      profileRows: [
        { profile_weight: 2.0, interests: { interest_slug: "tech.ai.llms" } }, // → ai
        { profile_weight: 1.5, interests: { interest_slug: "tech" } }, // → tech
      ],
      entityRows: [
        { entity_id: "ai/foundation-models-llms/labs-models/openai", follow_weight: 2.0 }, // → ai
        { entity_id: "ai/ai-hardware-compute/companies-topics/nvidia", follow_weight: 1.0 }, // → ai
      ],
    });

    const vector = await rollUpInterestVector(client);

    // ai accumulates the AI topic (2.0) + both AI entities (3.0) = 5.0; tech = 1.5.
    expect(vector).toEqual({ ai: 5.0, tech: 1.5 });

    // Integrated assertion: the rolled-up vector lands on ai-frontier-tech.
    const match = mapToArchetype(vector, SEEDED_ARCHETYPES);
    expect(match.archetype_id).toBe("ai-frontier-tech");
    expect(match.is_fallback).toBe(false);
  });

  it("returns a zero vector for a brand-new user → mapToArchetype falls back to balanced-generalist", async () => {
    // WHY: a new user has no follows; they MUST get the balanced default, not a
    // narrow archetype. An empty roll-up (zero magnitude) is exactly the fallback.
    const { client } = makeRollUpClient({ user: { id: AUTHED_USER_ID }, profileRows: [], entityRows: [] });

    const vector = await rollUpInterestVector(client);

    expect(vector).toEqual({});
    const match = mapToArchetype(vector, SEEDED_ARCHETYPES);
    expect(match.archetype_id).toBe("balanced-generalist");
    expect(match.is_fallback).toBe(true);
  });

  it("returns a zero vector when signed out WITHOUT reading the per-user tables (anon browse)", async () => {
    // WHY: onboarding browses before/around sign-in — an anon roll-up must not
    // throw on the owner-scoped reads; it returns {} and the matcher falls back.
    const { client, getUser, eqCalls } = makeRollUpClient({ user: null });

    const vector = await rollUpInterestVector(client);

    expect(vector).toEqual({});
    expect(getUser).toHaveBeenCalledTimes(1);
    expect(eqCalls.user_interest_profile).toHaveLength(0);
    expect(eqCalls.user_entity_follows).toHaveLength(0);
  });

  it("sums BOTH axes and applies the tech.ai* → ai exception (not tech)", async () => {
    // WHY: the exception is load-bearing — without it tech.ai* would score tech and
    // the ai dimension would never light up. We assert tech.ai* lands on ai, while
    // a sibling sport topic + arts entity bucket correctly.
    const { client } = makeRollUpClient({
      user: { id: AUTHED_USER_ID },
      profileRows: [
        { profile_weight: 3.0, interests: { interest_slug: "tech.ai" } }, // → ai (exception)
        { profile_weight: 1.0, interests: { interest_slug: "sport.cricket.india" } }, // → sport
        { profile_weight: 1.0, interests: { interest_slug: "world" } }, // → geopolitics
        { profile_weight: 1.0, interests: { interest_slug: "climate" } }, // → environment
      ],
      entityRows: [
        { entity_id: "arts/music/artists/taylor-swift", follow_weight: 2.0 }, // → arts
      ],
    });

    const vector = await rollUpInterestVector(client);

    expect(vector).toEqual({ ai: 3.0, sport: 1.0, geopolitics: 1.0, environment: 1.0, arts: 2.0 });
  });

  it("drops an unmapped interest root rather than mis-bucketing it (edge case)", async () => {
    // WHY: an unknown root MUST NOT silently land in a wrong category (a wrong
    // bucket is a miscategorization). It is dropped (and logged), never guessed.
    const { client } = makeRollUpClient({
      user: { id: AUTHED_USER_ID },
      profileRows: [
        { profile_weight: 5.0, interests: { interest_slug: "totally-unknown-root.child" } }, // dropped
        { profile_weight: 2.0, interests: { interest_slug: "business.equities" } }, // → business
      ],
      entityRows: [],
    });

    const vector = await rollUpInterestVector(client);

    expect(vector).toEqual({ business: 2.0 });
  });

  it("normalizes a single-object PostgREST embed AND tolerates a null embed", async () => {
    // WHY: PostgREST surfaces a to-one embed as an object OR a one-element array;
    // and an orphaned row could carry a null embed. The reader must handle all three.
    const { client } = makeRollUpClient({
      user: { id: AUTHED_USER_ID },
      profileRows: [
        { profile_weight: 1.0, interests: [{ interest_slug: "tech" }] }, // array form → tech
        { profile_weight: 9.0, interests: null }, // null embed → skipped, not crashed
      ],
      entityRows: [],
    });

    const vector = await rollUpInterestVector(client);

    expect(vector).toEqual({ tech: 1.0 });
  });

  it("throws when the interest-profile read errors (surface, never swallow — Rule 12)", async () => {
    const { client } = makeRollUpClient({
      user: { id: AUTHED_USER_ID },
      profileError: { message: "permission denied" },
    });

    await expect(rollUpInterestVector(client)).rejects.toThrow(/Failed to read interest profile/i);
  });

  it("throws when the entity-follows read errors (surface, never swallow — Rule 12)", async () => {
    const { client } = makeRollUpClient({
      user: { id: AUTHED_USER_ID },
      profileRows: [],
      entityError: { message: "permission denied" },
    });

    await expect(rollUpInterestVector(client)).rejects.toThrow(/Failed to read entity follows/i);
  });
});

describe("slug → pinned-key mapping tables (completeness)", () => {
  it("maps every seeded interest root to a valid pinned key", () => {
    // WHY: every depth-0 interest root MUST resolve to a pinned key — a gap is a
    // dropped category (a real interest that never scores its archetype dimension).
    const SEEDED_INTEREST_ROOTS = [
      "world",
      "business",
      "tech",
      "sport",
      "health",
      "entertainment",
      "climate",
      "lifestyle",
      "crypto",
      "science",
    ];
    for (const root of SEEDED_INTEREST_ROOTS) {
      expect(INTEREST_ROOT_TO_PINNED_KEY[root]).toBeDefined();
    }
  });

  it("maps every seeded entity root to its identity pinned key", () => {
    // WHY: the entity registry's top-level segments ARE the pinned keys with entity
    // coverage; the map must cover them so entity follows always score a dimension.
    const SEEDED_ENTITY_ROOTS: Array<[string, string]> = [
      ["ai", "ai"],
      ["arts", "arts"],
      ["business", "business"],
      ["geopolitics", "geopolitics"],
      ["sport", "sport"],
      ["tech", "tech"],
    ];
    for (const [root, expected] of SEEDED_ENTITY_ROOTS) {
      expect(ENTITY_ROOT_TO_PINNED_KEY[root]).toBe(expected);
    }
  });
});
