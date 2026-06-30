import { describe, expect, it } from "vitest";
import {
  buildInitialSelection,
  type ClusterSelection,
  isClusterSelected,
  isMemberFollowed,
  resolveFollowSet,
  toggleCluster,
  toggleMember,
} from "@/lib/clusterSelection";
import type { ResolvedCluster, ResolvedClusterMember } from "@/lib/sourceClusters";

/**
 * Phase FSR-M6a SP2 — the opt-out cluster-selection state model.
 *
 * WHY these tests exist (Rule 9 — encode the opt-out product rules, not the data
 * structure). Onboarding is OPT-OUT (User Story 12): recommended clusters arrive
 * pre-selected and the user deselects. The load-bearing edges that MUST fail if the
 * rule breaks:
 *   - pre-selected clusters yield their members as the initial follow set (opt-out);
 *   - DESELECTING a cluster after individually KEEPING one member leaves that member
 *     followed (the PRD edge case — must fail if deselect nukes the kept member);
 *   - picking ZERO clusters yields an empty follow set (allowed — User Story 21);
 *   - a member shared by two selected clusters appears ONCE (dedup);
 *   - sources vs personalities partition into the two output lists correctly.
 */

function srcMember(id: string, name = id): ResolvedClusterMember {
  return { kind: "source", followable_id: id, display_name: name, popularity_score: 50 };
}
function personMember(id: string, name = id): ResolvedClusterMember {
  return { kind: "personality", followable_id: id, display_name: name, popularity_score: 50 };
}
function cluster(slug: string, members: ResolvedClusterMember[], sortOrder = 0): ResolvedCluster {
  return {
    cluster_slug: slug,
    cluster_label: slug,
    cluster_category: "ai",
    cluster_sort_order: sortOrder,
    members,
  };
}

describe("buildInitialSelection — opt-out pre-selection", () => {
  it("pre-selects the recommended clusters and yields their members as the initial follow set", () => {
    // WHY: this is the opt-out promise. A recommended cluster starts FOLLOWED.
    const clusters = [cluster("rec", [srcMember("s-1"), personMember("p-1")]), cluster("notrec", [srcMember("s-2")])];
    const state = buildInitialSelection(clusters, ["rec"]);

    expect(isClusterSelected(state, "rec")).toBe(true);
    expect(isClusterSelected(state, "notrec")).toBe(false);
    const followed = resolveFollowSet(state);
    expect(followed.sources).toEqual(["s-1"]);
    expect(followed.personalities).toEqual(["p-1"]);
  });

  it("ignores a recommended slug not present in the resolved clusters (dropped/empty)", () => {
    const clusters = [cluster("rec", [srcMember("s-1")])];
    const state = buildInitialSelection(clusters, ["rec", "ghost"]);
    expect(isClusterSelected(state, "rec")).toBe(true);
    expect(isClusterSelected(state, "ghost")).toBe(false);
  });

  it("with ZERO recommended clusters yields an EMPTY follow set (User Story 21 — allowed)", () => {
    const clusters = [cluster("c-1", [srcMember("s-1")])];
    const state = buildInitialSelection(clusters, []);
    expect(resolveFollowSet(state)).toEqual({ sources: [], personalities: [] });
  });
});

describe("toggleCluster — bulk select/deselect", () => {
  it("selecting a cluster bulk-follows all its members; deselecting bulk-unfollows them", () => {
    const clusters = [cluster("c-1", [srcMember("s-1"), srcMember("s-2")])];
    let state: ClusterSelection = buildInitialSelection(clusters, []);

    state = toggleCluster(state, "c-1");
    expect(resolveFollowSet(state).sources.sort()).toEqual(["s-1", "s-2"]);

    state = toggleCluster(state, "c-1");
    expect(resolveFollowSet(state).sources).toEqual([]);
  });

  it("deselecting a pre-selected cluster removes exactly its non-individually-kept members", () => {
    const clusters = [cluster("c-1", [srcMember("s-1"), srcMember("s-2")]), cluster("c-2", [srcMember("s-3")])];
    let state = buildInitialSelection(clusters, ["c-1", "c-2"]);

    state = toggleCluster(state, "c-1");
    // c-1's members gone; c-2's member untouched.
    expect(resolveFollowSet(state).sources).toEqual(["s-3"]);
  });
});

describe("toggleMember — individual override wins over cluster bulk state", () => {
  it("DESELECTING a cluster after individually KEEPING one member leaves that member followed (PRD edge case)", () => {
    // WHY: this is the load-bearing edge (User Story 12). The user kept s-1
    // individually; deselecting its cluster must NOT nuke s-1. This FAILS the moment
    // toggleCluster ignores / clears member overrides.
    const clusters = [cluster("c-1", [srcMember("s-1"), srcMember("s-2")])];
    let state = buildInitialSelection(clusters, ["c-1"]); // both on via bulk

    // Individually re-affirm s-1 as an explicit keep (toggle off → on records `on`).
    state = toggleMember(state, { kind: "source", followable_id: "s-1" }); // → off
    state = toggleMember(state, { kind: "source", followable_id: "s-1" }); // → on (explicit keep)

    // Deselect the whole cluster. The bulk-off would drop both, but the explicit keep
    // on s-1 WINS; s-2 (no override) drops.
    state = toggleCluster(state, "c-1");

    expect(isClusterSelected(state, "c-1")).toBe(false);
    expect(resolveFollowSet(state).sources).toEqual(["s-1"]);
  });

  it("an explicit member REMOVAL survives its cluster being re-selected (override off wins over re-select)", () => {
    // The mirror edge: removing s-2 then re-toggling the cluster keeps s-2 off — the
    // individual intent is sticky until the user flips it again.
    const clusters = [cluster("c-1", [srcMember("s-1"), srcMember("s-2")])];
    let state = buildInitialSelection(clusters, ["c-1"]);

    state = toggleMember(state, { kind: "source", followable_id: "s-2" }); // remove s-2
    state = toggleCluster(state, "c-1"); // deselect
    state = toggleCluster(state, "c-1"); // re-select

    // c-1 selected again, but s-2's explicit-off override still wins.
    expect(resolveFollowSet(state).sources).toEqual(["s-1"]);
  });

  it("removing a member from a selected cluster leaves it unfollowed (override off wins)", () => {
    const clusters = [cluster("c-1", [srcMember("s-1"), srcMember("s-2")])];
    let state = buildInitialSelection(clusters, ["c-1"]);

    state = toggleMember(state, { kind: "source", followable_id: "s-2" }); // remove s-2
    expect(isMemberFollowed(state, { kind: "source", followable_id: "s-2" })).toBe(false);
    expect(resolveFollowSet(state).sources).toEqual(["s-1"]);
  });

  it("an individually-kept member persists even with NO cluster selected (deselect-keeps-member, clean)", () => {
    // WHY: the precise PRD edge — no cluster is on, but a member the user toggled on
    // individually stays in the follow set. This is the override beating the (off) bulk.
    const clusters = [cluster("c-1", [srcMember("s-1"), srcMember("s-2")])];
    let state = buildInitialSelection(clusters, []); // nothing selected

    state = toggleMember(state, { kind: "source", followable_id: "s-1" }); // explicitly follow s-1
    expect(resolveFollowSet(state).sources).toEqual(["s-1"]);
    expect(isClusterSelected(state, "c-1")).toBe(false);
  });
});

describe("resolveFollowSet — dedup + partition", () => {
  it("a member shared by two selected clusters appears ONCE", () => {
    const shared = personMember("p-shared");
    const clusters = [cluster("c-1", [shared, srcMember("s-1")]), cluster("c-2", [shared, srcMember("s-2")])];
    const state = buildInitialSelection(clusters, ["c-1", "c-2"]);

    const followed = resolveFollowSet(state);
    expect(followed.personalities).toEqual(["p-shared"]); // once
    expect(followed.sources.sort()).toEqual(["s-1", "s-2"]);
  });

  it("partitions sources and personalities into the two output lists correctly", () => {
    const clusters = [cluster("c-1", [srcMember("s-1"), personMember("p-1"), srcMember("s-2"), personMember("p-2")])];
    const state = buildInitialSelection(clusters, ["c-1"]);

    const followed = resolveFollowSet(state);
    expect(followed.sources.sort()).toEqual(["s-1", "s-2"]);
    expect(followed.personalities.sort()).toEqual(["p-1", "p-2"]);
  });
});
