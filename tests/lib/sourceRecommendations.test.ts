import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Phase 5c SP1 — source recommendations (archetype → balanced, popularity-ranked,
 * follow-annotated source grid for one axis).
 *
 * WHY these tests exist (Rule 9 — encode the product contract, not the call shape):
 *  - A MULTI-archetype user must get a grid BALANCED across their archetypes, not
 *    one archetype's whole top-K then the other's. The round-robin is the entire
 *    point ("people like you" must blend both tastes), so we assert the actual
 *    INTERLEAVING (A1, B1, A2, B2…), not just a non-empty list.
 *  - Within each archetype the order must stay popularity-desc — the data layer
 *    already sorts, so we assert the merged order respects each list's rank.
 *  - `is_already_added` must be TRUE for sources the user follows and FALSE
 *    otherwise — a wrong flag shows "Add" on a followed source (dup follow) or
 *    "Added" on a new one (user can't follow it). We assert both, AND that an
 *    ANON browse (no session) degrades to all-false WITHOUT throwing (onboarding
 *    browses the catalog before sign-in).
 *  - A source serving BOTH archetypes must appear ONCE (dedup) — a duplicate tile
 *    is a visible bug.
 *  - The sub-niche boost must only RE-ORDER near-equals, never override the
 *    archetype/popularity ranking (open-Q#1: modest v1 boost).
 *
 * Mocks the Phase 5b data layer (@/lib/sources) at the module boundary + the
 * client's auth.getUser(), per CLAUDE.md mocking strategy — no real Supabase.
 */

import type { ContentSource } from "@/types/source";

// Mock the Phase 5b data layer the recommender composes over. Hoisted by Vitest.
const listSourcesByArchetypeMock = vi.fn();
const getUserSourcesMock = vi.fn();
vi.mock("@/lib/sources", () => ({
  listSourcesByArchetype: (...args: unknown[]) => listSourcesByArchetypeMock(...args),
  getUserSources: (...args: unknown[]) => getUserSourcesMock(...args),
}));

// Import AFTER the mock is registered so the SUT binds the mocked helpers.
const { getRecommendedSources } = await import("@/lib/sourceRecommendations");

/** Build a content_sources row (catalog browse shape). */
function makeSource(overrides: Partial<ContentSource> = {}): ContentSource {
  return {
    source_id: "src-1",
    content_source_type: "youtube_channel",
    external_id: "UC_x",
    source_name: "Channel",
    source_description: null,
    thumbnail_url: null,
    subscriber_count: null,
    platform_metadata: null,
    personas: ["ai-frontier-tech"],
    topic_tags: ["ai"],
    popularity_score: 50,
    is_curated: true,
    last_fetched_at: null,
    ...overrides,
  };
}

/** A fake client exposing only auth.getUser() (the recommender's auth probe). */
function makeClient(user: { id: string } | null) {
  return { auth: { getUser: vi.fn().mockResolvedValue({ data: { user }, error: null }) } } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  // Default: anon browse (no session). Individual tests override for authed.
  getUserSourcesMock.mockResolvedValue([]);
});

describe("getRecommendedSources — round-robin across multiple archetypes (the DoD)", () => {
  it("interleaves per-archetype popularity-ranked lists (A1, B1, A2, B2 …) balanced across archetypes", async () => {
    // WHY: a 2-archetype user must see a blended grid, not all of A then all of B.
    // We give each archetype its own popularity-desc list and assert the EXACT
    // interleaving — the balance property the round-robin guarantees.
    const aiList = [
      makeSource({ source_id: "ai-1", source_name: "AI One", popularity_score: 99 }),
      makeSource({ source_id: "ai-2", source_name: "AI Two", popularity_score: 95 }),
      makeSource({ source_id: "ai-3", source_name: "AI Three", popularity_score: 90 }),
    ];
    const mktList = [
      makeSource({ source_id: "mkt-1", source_name: "Markets One", personas: ["markets-macro"], popularity_score: 70 }),
      makeSource({ source_id: "mkt-2", source_name: "Markets Two", personas: ["markets-macro"], popularity_score: 60 }),
    ];
    // The recommender calls the data layer once per archetype, in order.
    listSourcesByArchetypeMock
      .mockResolvedValueOnce(aiList) // for ["ai-frontier-tech"]
      .mockResolvedValueOnce(mktList); // for ["markets-macro"]

    const result = await getRecommendedSources("youtube_channel", {
      archetypes: ["ai-frontier-tech", "markets-macro"],
      limit: 12,
      client: makeClient({ id: "u-1" }),
    });

    // Round-robin: rank-0 of A, rank-0 of B, rank-1 of A, rank-1 of B, rank-2 of A.
    expect(result.map((row) => row.source_id)).toEqual(["ai-1", "mkt-1", "ai-2", "mkt-2", "ai-3"]);
    // Each archetype was queried separately (single-element persona overlap).
    expect(listSourcesByArchetypeMock.mock.calls[0][0]).toEqual(["ai-frontier-tech"]);
    expect(listSourcesByArchetypeMock.mock.calls[1][0]).toEqual(["markets-macro"]);
  });

  it("dedups a source serving BOTH archetypes to a single tile (first encounter wins)", async () => {
    // WHY: a source tagged to both archetypes appears in both per-archetype lists;
    // it must render ONCE (a duplicate tile is a visible bug). First encounter (the
    // higher-priority archetype's rank) wins.
    const shared = makeSource({
      source_id: "shared-1",
      source_name: "Shared",
      personas: ["ai-frontier-tech", "markets-macro"],
    });
    const aiList = [shared, makeSource({ source_id: "ai-2", popularity_score: 80 })];
    const mktList = [shared, makeSource({ source_id: "mkt-2", personas: ["markets-macro"], popularity_score: 70 })];
    listSourcesByArchetypeMock.mockResolvedValueOnce(aiList).mockResolvedValueOnce(mktList);

    const result = await getRecommendedSources("youtube_channel", {
      archetypes: ["ai-frontier-tech", "markets-macro"],
      client: makeClient({ id: "u-1" }),
    });

    const sharedCount = result.filter((row) => row.source_id === "shared-1").length;
    expect(sharedCount).toBe(1);
    // Round-robin walk: rank-0 → shared (from A; B's rank-0 is also shared, skipped
    // as seen). rank-1 → A's ai-2, then B's mkt-2. So the deduped order is
    // [shared-1, ai-2, mkt-2] — the shared source is consumed at A's rank-0.
    expect(result.map((row) => row.source_id)).toEqual(["shared-1", "ai-2", "mkt-2"]);
  });

  it("respects the limit, trimming the round-robin head", async () => {
    // WHY: a screen shows a fixed number of tiles. Over-supply must be trimmed to
    // `limit` AFTER the balanced merge so the cut is balanced, not all-from-A.
    const aiList = [
      makeSource({ source_id: "ai-1", popularity_score: 99 }),
      makeSource({ source_id: "ai-2", popularity_score: 90 }),
    ];
    const mktList = [
      makeSource({ source_id: "mkt-1", personas: ["markets-macro"], popularity_score: 70 }),
      makeSource({ source_id: "mkt-2", personas: ["markets-macro"], popularity_score: 60 }),
    ];
    listSourcesByArchetypeMock.mockResolvedValueOnce(aiList).mockResolvedValueOnce(mktList);

    const result = await getRecommendedSources("youtube_channel", {
      archetypes: ["ai-frontier-tech", "markets-macro"],
      limit: 2,
      client: makeClient({ id: "u-1" }),
    });

    expect(result.map((row) => row.source_id)).toEqual(["ai-1", "mkt-1"]);
  });

  it("single archetype skips the merge (the list IS the popularity order)", async () => {
    const aiList = [
      makeSource({ source_id: "ai-1", popularity_score: 99 }),
      makeSource({ source_id: "ai-2", popularity_score: 90 }),
      makeSource({ source_id: "ai-3", popularity_score: 80 }),
    ];
    listSourcesByArchetypeMock.mockResolvedValueOnce(aiList);

    const result = await getRecommendedSources("youtube_channel", {
      archetypes: ["ai-frontier-tech"],
      client: makeClient({ id: "u-1" }),
    });

    expect(result.map((row) => row.source_id)).toEqual(["ai-1", "ai-2", "ai-3"]);
  });

  it("returns [] for an empty archetypes set without hitting the catalog (edge case)", async () => {
    const result = await getRecommendedSources("podcast", {
      archetypes: [],
      client: makeClient({ id: "u-1" }),
    });

    expect(result).toEqual([]);
    expect(listSourcesByArchetypeMock).not.toHaveBeenCalled();
  });
});

describe("getRecommendedSources — is_already_added annotation", () => {
  it("sets is_already_added=true for followed sources and false for the rest (authed)", async () => {
    // WHY: a wrong flag either lets the user re-follow (dup) or blocks following a
    // new source. We follow ai-1 only and assert exactly that row is flagged.
    const aiList = [
      makeSource({ source_id: "ai-1", popularity_score: 99 }),
      makeSource({ source_id: "ai-2", popularity_score: 90 }),
    ];
    listSourcesByArchetypeMock.mockResolvedValueOnce(aiList);
    getUserSourcesMock.mockResolvedValueOnce([
      { user_id: "u-1", source_id: "ai-1", source_priority: "everything", added_via: "onboarding" },
    ]);

    const result = await getRecommendedSources("youtube_channel", {
      archetypes: ["ai-frontier-tech"],
      client: makeClient({ id: "u-1" }),
    });

    expect(result.find((row) => row.source_id === "ai-1")?.is_already_added).toBe(true);
    expect(result.find((row) => row.source_id === "ai-2")?.is_already_added).toBe(false);
  });

  it("degrades to all-false on an ANON browse (no session) without throwing", async () => {
    // WHY: onboarding browses the catalog before sign-in. The follow read MUST NOT
    // throw the signed-out error here — it degrades to "no follows" so the anon
    // grid still renders (every tile addable). getUserSources must NOT be called.
    const aiList = [makeSource({ source_id: "ai-1" })];
    listSourcesByArchetypeMock.mockResolvedValueOnce(aiList);

    const result = await getRecommendedSources("youtube_channel", {
      archetypes: ["ai-frontier-tech"],
      client: makeClient(null), // anon
    });

    expect(result[0].is_already_added).toBe(false);
    expect(getUserSourcesMock).not.toHaveBeenCalled();
  });
});

describe("getRecommendedSources — sub-niche boost (open-Q#1: modest linear v1)", () => {
  it("nudges a sub-niche-matching source above an equal-popularity non-matching one", async () => {
    // WHY: a user who picked the "ai" sub-niche should see ai-tagged sources first
    // AMONG EQUALS. Two equal-popularity sources, one ai-tagged: the ai one must
    // lead after the boost. (The boost is tiny — it only breaks near-ties.)
    const list = [
      makeSource({ source_id: "generic", source_name: "Generic", topic_tags: ["tech"], popularity_score: 80 }),
      makeSource({ source_id: "ai-niche", source_name: "AI Niche", topic_tags: ["ai"], popularity_score: 80 }),
    ];
    listSourcesByArchetypeMock.mockResolvedValueOnce(list);

    const result = await getRecommendedSources("youtube_channel", {
      archetypes: ["ai-frontier-tech"],
      subNiches: ["ai"],
      client: makeClient({ id: "u-1" }),
    });

    // The ai-tagged source jumps ahead of the equal-popularity generic one.
    expect(result.map((row) => row.source_id)).toEqual(["ai-niche", "generic"]);
  });

  it("does NOT override a real popularity gap (the boost stays modest)", async () => {
    // WHY: the boost must not let a niche match leapfrog a far-more-popular source —
    // a 0.1-per-tag boost cannot overcome a 10-point popularity gap. Order holds.
    const list = [
      makeSource({ source_id: "popular", topic_tags: ["tech"], popularity_score: 90 }),
      makeSource({ source_id: "niche", topic_tags: ["ai"], popularity_score: 80 }),
    ];
    listSourcesByArchetypeMock.mockResolvedValueOnce(list);

    const result = await getRecommendedSources("youtube_channel", {
      archetypes: ["ai-frontier-tech"],
      subNiches: ["ai"],
      client: makeClient({ id: "u-1" }),
    });

    expect(result.map((row) => row.source_id)).toEqual(["popular", "niche"]);
  });

  it("leaves order unchanged when no sub-niches are passed", async () => {
    const list = [
      makeSource({ source_id: "a", popularity_score: 80 }),
      makeSource({ source_id: "b", popularity_score: 80 }),
    ];
    listSourcesByArchetypeMock.mockResolvedValueOnce(list);

    const result = await getRecommendedSources("youtube_channel", {
      archetypes: ["ai-frontier-tech"],
      client: makeClient({ id: "u-1" }),
    });

    expect(result.map((row) => row.source_id)).toEqual(["a", "b"]);
  });
});
