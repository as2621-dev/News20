import { describe, expect, it, vi } from "vitest";
import type { ResolvedFollowSet } from "@/lib/clusterSelection";
import {
  type ClusterMemberRef,
  type ClusterRow,
  commitClusterFollowSet,
  getClustersForCategories,
  resolveCategoryClusters,
} from "@/lib/sourceClusters";
import type { ContentSource, Personality } from "@/types/source";

/**
 * Phase FSR-M6a SP1 — category-keyed cluster read + no-dup resolver, the TS mirror
 * of `agents/catalog/cluster_resolver.py::resolve_category_clusters`.
 *
 * WHY these tests exist (Rule 9 — encode the load-bearing trust contract, not call
 * shapes). The no-dup rule is the spine of the source-first thesis (PRD Decision #7):
 * a person shown as a personality card must NEVER also leak in as their raw YouTube/X
 * channel row, or the onboarding grid shows the same human twice — the "randoms /
 * duplicates" failure the product promises to avoid. These cases MIRROR M1's Python
 * resolver cases so the TS read honors the SAME contract offline:
 *   - topic_tags ∩ category filter ("no randoms" — a non-overlapping source is excluded);
 *   - popularity-carried member order via cluster/member sort;
 *   - a PRESENT personality hides its bundled handle rows from BOTH grid and cluster
 *     (the load-bearing no-dup test — MUST fail if the handles leak back in);
 *   - an empty cluster is not returned;
 *   - a followable in two clusters of one category renders once (first-cluster-wins).
 */

/** Build a content_sources row, overriding only what a case cares about. */
function makeSource(overrides: Partial<ContentSource> = {}): ContentSource {
  return {
    source_id: "src-1",
    content_source_type: "youtube_channel",
    external_id: "UC_default",
    source_name: "Default Channel",
    source_description: null,
    thumbnail_url: null,
    subscriber_count: null,
    platform_metadata: null,
    personas: [],
    topic_tags: ["ai"],
    popularity_score: 50,
    is_curated: true,
    last_fetched_at: null,
    ...overrides,
  };
}

/** Build a personalities row, overriding only what a case cares about. */
function makePersonality(overrides: Partial<Personality> = {}): Personality {
  return {
    personality_id: "p-1",
    display_name: "Default Person",
    aliases: [],
    bio: null,
    photo_url: null,
    youtube_channel_ids: [],
    personas: [],
    topic_tags: ["ai"],
    popularity_score: 50,
    is_curated: true,
    ...overrides,
  };
}

/** Build a source_clusters row. */
function makeCluster(overrides: Partial<ClusterRow> = {}): ClusterRow {
  return {
    cluster_id: "c-1",
    cluster_slug: "cluster-1",
    cluster_label: "Cluster 1",
    cluster_category: "ai",
    cluster_sort_order: 0,
    is_curated: true,
    ...overrides,
  };
}

/** Build a source member ref. */
function srcMember(cluster_id: string, source_id: string, order: number): ClusterMemberRef {
  return { cluster_id, source_id, personality_id: null, member_sort_order: order };
}

/** Build a personality member ref. */
function personMember(cluster_id: string, personality_id: string, order: number): ClusterMemberRef {
  return { cluster_id, source_id: null, personality_id, member_sort_order: order };
}

describe("resolveCategoryClusters — the pure no-dup resolver (mirrors M1)", () => {
  it("filters clusters to the chosen category and orders members by member_sort_order", () => {
    // WHY: a chosen-category set returns ONLY this category's clusters (the "no
    // randoms" rule), and member order is the render order onboarding shows.
    const clusters = [
      makeCluster({ cluster_id: "c-ai", cluster_slug: "ai-labs", cluster_category: "ai" }),
      makeCluster({ cluster_id: "c-sport", cluster_slug: "sport-x", cluster_category: "sport" }),
    ];
    const members = [srcMember("c-ai", "s-2", 2), srcMember("c-ai", "s-1", 1), srcMember("c-sport", "s-9", 1)];
    const sources = [
      makeSource({ source_id: "s-1", source_name: "First", topic_tags: ["ai"] }),
      makeSource({ source_id: "s-2", source_name: "Second", topic_tags: ["ai"] }),
      makeSource({ source_id: "s-9", source_name: "SportOne", topic_tags: ["sport"] }),
    ];

    const resolved = resolveCategoryClusters("ai", clusters, members, sources, []);

    expect(resolved).toHaveLength(1);
    expect(resolved[0].cluster_slug).toBe("ai-labs");
    // member_sort_order 1 then 2 — render order.
    expect(resolved[0].members.map((m) => m.display_name)).toEqual(["First", "Second"]);
  });

  it("orders clusters by (cluster_sort_order, cluster_slug) — stable tie-break", () => {
    const clusters = [
      makeCluster({ cluster_id: "c-b", cluster_slug: "bbb", cluster_sort_order: 0 }),
      makeCluster({ cluster_id: "c-a", cluster_slug: "aaa", cluster_sort_order: 0 }),
      makeCluster({ cluster_id: "c-first", cluster_slug: "zzz", cluster_sort_order: -1 }),
    ];
    const members = [srcMember("c-b", "s-b", 1), srcMember("c-a", "s-a", 1), srcMember("c-first", "s-z", 1)];
    const sources = [
      makeSource({ source_id: "s-b" }),
      makeSource({ source_id: "s-a" }),
      makeSource({ source_id: "s-z" }),
    ];

    const resolved = resolveCategoryClusters("ai", clusters, members, sources, []);
    // sort_order -1 first, then sort_order 0 tie broken by slug aaa < bbb.
    expect(resolved.map((c) => c.cluster_slug)).toEqual(["zzz", "aaa", "bbb"]);
  });

  it("HIDES a present personality's bundled YouTube + X rows from the grid (no-dup — load-bearing)", () => {
    // WHY: this is the trust contract. A personality bundling a YouTube channel +
    // an X handle is shown ONCE as a personality card; its raw channel/account rows
    // MUST be suppressed. If they leak, the same human renders twice. This test
    // FAILS the moment the no-dup match stops suppressing the bundled rows.
    const clusters = [
      makeCluster({ cluster_id: "c-people", cluster_slug: "ai-people" }),
      makeCluster({ cluster_id: "c-raw", cluster_slug: "ai-raw" }),
    ];
    const members = [
      // The personality is present (a personality member of a cluster in this category).
      personMember("c-people", "p-lex", 1),
      // Their raw bundled rows are ALSO listed as members of another cluster — must be hidden.
      srcMember("c-raw", "s-lex-yt", 1),
      srcMember("c-raw", "s-lex-x", 2),
      // An unrelated source that should still render.
      srcMember("c-raw", "s-other", 3),
    ];
    const personalities = [
      makePersonality({
        personality_id: "p-lex",
        display_name: "Lex Fridman",
        youtube_channel_ids: ["UC_lex"],
        aliases: ["@lexfridman"],
      }),
    ];
    const sources = [
      makeSource({ source_id: "s-lex-yt", content_source_type: "youtube_channel", external_id: "UC_lex" }),
      makeSource({ source_id: "s-lex-x", content_source_type: "x_account", external_id: "@lexfridman" }),
      makeSource({ source_id: "s-other", source_name: "Other Channel", external_id: "UC_other" }),
    ];

    const resolved = resolveCategoryClusters("ai", clusters, members, sources, personalities);

    const allMembers = resolved.flatMap((c) => c.members);
    const renderedIds = allMembers.map((m) => m.followable_id);
    // The personality card renders.
    expect(renderedIds).toContain("p-lex");
    // Its bundled YouTube + X rows are SUPPRESSED.
    expect(renderedIds).not.toContain("s-lex-yt");
    expect(renderedIds).not.toContain("s-lex-x");
    // The unrelated source still renders.
    expect(renderedIds).toContain("s-other");
  });

  it("drops a cluster left empty after no-dup suppression (never surfaced)", () => {
    // WHY: a cluster whose only members were suppressed bundled rows must NOT show
    // as an empty card — the empty-cluster-omission rule.
    const clusters = [
      makeCluster({ cluster_id: "c-people", cluster_slug: "ai-people" }),
      makeCluster({ cluster_id: "c-raw", cluster_slug: "ai-raw-only" }),
    ];
    const members = [
      personMember("c-people", "p-lex", 1),
      srcMember("c-raw", "s-lex-yt", 1), // the ONLY member — suppressed → cluster empties.
    ];
    const personalities = [makePersonality({ personality_id: "p-lex", youtube_channel_ids: ["UC_lex"] })];
    const sources = [makeSource({ source_id: "s-lex-yt", external_id: "UC_lex" })];

    const resolved = resolveCategoryClusters("ai", clusters, members, sources, personalities);
    // Only the people cluster survives; the raw-only cluster is dropped.
    expect(resolved.map((c) => c.cluster_slug)).toEqual(["ai-people"]);
  });

  it("renders a followable in two clusters of one category ONCE (first-cluster-wins)", () => {
    const clusters = [
      makeCluster({ cluster_id: "c-1", cluster_slug: "aaa", cluster_sort_order: 0 }),
      makeCluster({ cluster_id: "c-2", cluster_slug: "bbb", cluster_sort_order: 1 }),
    ];
    const members = [srcMember("c-1", "s-shared", 1), srcMember("c-2", "s-shared", 1), srcMember("c-2", "s-only2", 2)];
    const sources = [makeSource({ source_id: "s-shared" }), makeSource({ source_id: "s-only2" })];

    const resolved = resolveCategoryClusters("ai", clusters, members, sources, []);
    // s-shared renders in the FIRST cluster only.
    expect(resolved[0].members.map((m) => m.followable_id)).toEqual(["s-shared"]);
    expect(resolved[1].members.map((m) => m.followable_id)).toEqual(["s-only2"]);
  });

  it("skips a missing or un-curated member row (don't error)", () => {
    const clusters = [makeCluster({ cluster_id: "c-1", cluster_slug: "aaa" })];
    const members = [
      srcMember("c-1", "s-missing", 1), // no row in the pool
      srcMember("c-1", "s-uncurated", 2), // is_curated false
      srcMember("c-1", "s-ok", 3),
    ];
    const sources = [
      makeSource({ source_id: "s-uncurated", is_curated: false }),
      makeSource({ source_id: "s-ok", source_name: "Kept" }),
    ];

    const resolved = resolveCategoryClusters("ai", clusters, members, sources, []);
    expect(resolved).toHaveLength(1);
    expect(resolved[0].members.map((m) => m.display_name)).toEqual(["Kept"]);
  });

  it("does NOT suppress a personality's handles when that personality is NOT present in the category", () => {
    // WHY: suppression is conditional on the personality being a member of a cluster
    // in THIS category. A bundled row whose personality isn't present must render.
    const clusters = [makeCluster({ cluster_id: "c-raw", cluster_slug: "ai-raw" })];
    const members = [srcMember("c-raw", "s-lex-yt", 1)];
    // The personality EXISTS in the pool but is NOT a cluster member here.
    const personalities = [makePersonality({ personality_id: "p-lex", youtube_channel_ids: ["UC_lex"] })];
    const sources = [makeSource({ source_id: "s-lex-yt", external_id: "UC_lex" })];

    const resolved = resolveCategoryClusters("ai", clusters, members, sources, personalities);
    expect(resolved[0].members.map((m) => m.followable_id)).toEqual(["s-lex-yt"]);
  });
});

/**
 * Fake Supabase client for the cluster read. Each `.from(table)` returns a builder
 * keyed by table so the four reads (clusters / members / sources / personalities)
 * resolve their own fixture rows. Supports `.eq`, `.in`, `.overlaps`, `.returns`.
 */
function makeClusterReadClient(tables: Record<string, { data: unknown; error: unknown }>) {
  const from = vi.fn((table: string) => {
    const result = tables[table] ?? { data: [], error: null };
    const builder: Record<string, unknown> = {
      returns: vi.fn().mockResolvedValue(result),
    };
    builder.eq = vi.fn(() => builder);
    builder.in = vi.fn(() => builder);
    builder.overlaps = vi.fn(() => builder);
    const select = vi.fn(() => builder);
    return { select };
  });
  return { client: { from } as never, from };
}

describe("getClustersForCategories — live read over M1 tables (mocked boundary)", () => {
  it("reads clusters + members + the candidate pools and resolves per category", async () => {
    const { client } = makeClusterReadClient({
      source_clusters: { data: [makeCluster({ cluster_id: "c-ai", cluster_slug: "ai-labs" })], error: null },
      source_cluster_members: { data: [srcMember("c-ai", "s-1", 1)], error: null },
      content_sources: { data: [makeSource({ source_id: "s-1", source_name: "AI One" })], error: null },
      personalities: { data: [], error: null },
    });

    const byCategory = await getClustersForCategories(["ai"], client);

    expect(byCategory.get("ai")).toHaveLength(1);
    expect(byCategory.get("ai")?.[0].members[0].display_name).toBe("AI One");
  });

  it("returns an empty map for an empty category list (no round-trip)", async () => {
    const { client, from } = makeClusterReadClient({});
    const byCategory = await getClustersForCategories([], client);
    expect(byCategory.size).toBe(0);
    expect(from).not.toHaveBeenCalled();
  });

  it("maps a category with no clusters to [] (un-seeded is valid, not a crash)", async () => {
    const { client } = makeClusterReadClient({
      source_clusters: { data: [], error: null },
      content_sources: { data: [], error: null },
      personalities: { data: [], error: null },
    });
    const byCategory = await getClustersForCategories(["ai"], client);
    expect(byCategory.get("ai")).toEqual([]);
  });

  it("throws when a catalog read errors (surface, never swallow — Rule 12)", async () => {
    const { client } = makeClusterReadClient({
      source_clusters: { data: null, error: { message: "relation does not exist" } },
    });
    await expect(getClustersForCategories(["ai"], client)).rejects.toThrow(/Failed to read source_clusters/i);
  });
});

/**
 * A fake client for the COMMIT path: `auth.getUser()` + per-table upserts. Captures
 * which ids were written to `user_content_sources` vs `user_personalities` so a test
 * can assert the EXACT resolved opt-out set was written — not "some rows".
 */
function makeCommitClient(user: { id: string } | null) {
  const sourceUpserts: unknown[] = [];
  const personalityUpserts: unknown[] = [];
  const getUser = vi.fn().mockResolvedValue({ data: { user }, error: null });
  const from = vi.fn((table: string) => ({
    upsert: vi.fn((payload: unknown) => {
      if (table === "user_content_sources") {
        sourceUpserts.push(payload);
      } else if (table === "user_personalities") {
        personalityUpserts.push(payload);
      }
      return Promise.resolve({ error: null });
    }),
  }));
  return { client: { auth: { getUser }, from } as never, sourceUpserts, personalityUpserts };
}

describe("commitClusterFollowSet — writes EXACTLY the resolved opt-out set (M6a SP4 DoD)", () => {
  it("writes exactly the resolved source ids to user_content_sources and personality ids to user_personalities", async () => {
    // WHY (the DoD, Rule 9): the committed follow set MUST EQUAL the resolved opt-out
    // set — a deselected member is simply absent from the set, so it is never written.
    // This asserts the WRITTEN ids equal the resolved ids, not merely "rows written".
    const followSet: ResolvedFollowSet = { sources: ["s-1", "s-2"], personalities: ["p-1"] };
    const { client, sourceUpserts, personalityUpserts } = makeCommitClient({ id: "u-1" });

    const counts = await commitClusterFollowSet(followSet, client);

    expect(sourceUpserts.map((row) => (row as { source_id: string }).source_id).sort()).toEqual(["s-1", "s-2"]);
    expect(personalityUpserts.map((row) => (row as { personality_id: string }).personality_id)).toEqual(["p-1"]);
    expect(counts).toEqual({ sources_followed: 2, personalities_followed: 1 });
  });

  it("an EMPTY follow set writes ZERO rows and does not error (zero-cluster path — User Story 21)", async () => {
    const { client, sourceUpserts, personalityUpserts } = makeCommitClient({ id: "u-1" });

    const counts = await commitClusterFollowSet({ sources: [], personalities: [] }, client);

    expect(sourceUpserts).toEqual([]);
    expect(personalityUpserts).toEqual([]);
    expect(counts).toEqual({ sources_followed: 0, personalities_followed: 0 });
  });

  it("a deselected member is NOT written (the set never contained it)", async () => {
    // The resolver/selection model already dropped the deselected member; commit only
    // ever sees the kept set. We assert the dropped id never reaches an upsert.
    const followSet: ResolvedFollowSet = { sources: ["s-kept"], personalities: [] };
    const { client, sourceUpserts } = makeCommitClient({ id: "u-1" });

    await commitClusterFollowSet(followSet, client);

    const writtenIds = sourceUpserts.map((row) => (row as { source_id: string }).source_id);
    expect(writtenIds).toEqual(["s-kept"]);
    expect(writtenIds).not.toContain("s-deselected");
  });
});
