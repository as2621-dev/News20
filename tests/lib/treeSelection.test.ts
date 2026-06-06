import { describe, expect, it } from "vitest";
import { createSelectionStore, PICKER_TREE } from "@/lib/followSets";
import {
  addCustomLeaf,
  branchTriState,
  buildOutlineTree,
  descendantLeafSelections,
  selectedLeafCount,
  type TreeNode,
  toggleBranch,
  toggleLeaf,
  totalLeafCount,
} from "@/lib/treeSelection";

/**
 * Pure-logic tests for the Blip Stage-1 outline-tree adapter (`@/lib/treeSelection`).
 *
 * Rule 9 — these encode WHY the behaviour matters, each failing on a real regression:
 *   - A leaf tap must produce the EXACT `FollowSelection` (path + id + kind + source)
 *     the archived `FollowSet`/`FollowChip` produced — the persisted follow id and the
 *     registry row both depend on this scheme. A drift in the path/id convention (e.g.
 *     a set label leaking into the path) FAILS the pinned NFL spot-check.
 *   - Branch tri-state is the screen's headline interaction ("ticking a branch takes
 *     everything in it"): a branch reads `some` when partially on, `all` when full, and
 *     a select-all then a clear-all round-trips through the SHARED store. A miscount or
 *     a non-idempotent toggle FAILS.
 *   - `moreSeeds` are folded inline as leaves (Stage-1 offline fallback) so they MUST
 *     count toward the branch total/tri-state, or the "all" state would be unreachable.
 */

/** Drill to a node by labels from a category root; throws on any missing segment. */
function drill(categoryId: string, ...labels: string[]): TreeNode {
  const roots = buildOutlineTree(PICKER_TREE);
  const root = roots.find((candidate) => candidate.treeId === categoryId);
  if (!root) {
    throw new Error(`category ${categoryId} not found`);
  }
  let node: TreeNode = root;
  for (const label of labels) {
    const next: TreeNode | undefined = node.children.find((child) => child.label === label);
    if (!next) {
      throw new Error(`child ${label} not found under ${node.label} [${node.children.map((c) => c.label).join(", ")}]`);
    }
    node = next;
  }
  return node;
}

describe("buildOutlineTree — maps the deep picker tree onto uniform branch/leaf rows", () => {
  it("makes categories level-0 roots and recursive items branches, plain items leaves", () => {
    const roots = buildOutlineTree(PICKER_TREE);
    expect(roots.length).toBe(PICKER_TREE.length);
    expect(roots.every((root) => root.level === 0 && !root.isLeaf)).toBe(true);

    // NFL carries its own sets → it is a BRANCH, not a leaf (the recursive mapping).
    const nfl = drill("sport", "American football", "Leagues", "NFL");
    expect(nfl.isLeaf).toBe(false);
    expect(nfl.children.map((child) => child.label)).toEqual(["Teams you follow", "People to follow"]);

    // A team under it has no sets → it is a LEAF.
    const chiefs = drill("sport", "American football", "Leagues", "NFL", "Teams you follow", "Kansas City Chiefs");
    expect(chiefs.isLeaf).toBe(true);
  });
});

describe("leaf selection — produces the exact FollowSelection the picker convention defines", () => {
  it("derives the path-derived id, the [Category, Sub, …item…, leaf] path, kind and source", () => {
    // WHY: this id is the persisted follow id and the registry key. Set labels
    // ("Leagues", "Teams you follow") are DROPPED from the path; parent item labels
    // ("NFL") are KEPT — exactly how the archived FollowSet/FollowChip built it.
    const chiefs = drill("sport", "American football", "Leagues", "NFL", "Teams you follow", "Kansas City Chiefs");
    const selection = chiefs.leafSelection;
    if (!selection) {
      throw new Error("expected a leaf selection");
    }
    expect(selection.followId).toBe("sport/american-football/leagues/nfl/teams-you-follow/kansas-city-chiefs");
    expect(selection.path).toEqual(["Sport", "American football", "NFL", "Kansas City Chiefs"]);
    expect(selection.type).toBe("entity");
    expect(selection.kind).toBe("team");
    expect(selection.source).toBe("seed");
  });

  it("toggleLeaf flips the store and toggleLeaf again clears it", () => {
    const store = createSelectionStore();
    const chiefs = drill("sport", "American football", "Leagues", "NFL", "Teams you follow", "Kansas City Chiefs");
    expect(toggleLeaf(chiefs, store)).toBe(true);
    expect(store.count()).toBe(1);
    expect(toggleLeaf(chiefs, store)).toBe(false);
    expect(store.count()).toBe(0);
  });
});

describe("branch tri-state — none / some / all over descendant leaves (incl. moreSeeds)", () => {
  it("counts moreSeeds as leaves so the branch total includes the offline fallback rows", () => {
    // "Teams you follow" = 8 seed items + 8 moreSeeds = 16 leaves (Stage-1 fold-in).
    const teams = drill("sport", "American football", "Leagues", "NFL", "Teams you follow");
    expect(totalLeafCount(teams)).toBe(16);
  });

  it("reads none with zero on, some when partially on, all when every leaf is on", () => {
    const store = createSelectionStore();
    const teams = drill("sport", "American football", "Leagues", "NFL", "Teams you follow");
    const leaves = descendantLeafSelections(teams);

    expect(branchTriState(teams, store)).toBe("none");

    // Turn ONE leaf on → partial.
    store.toggle(leaves[0]);
    expect(branchTriState(teams, store)).toBe("some");
    expect(selectedLeafCount(teams, store)).toBe(1);

    // Turn the rest on → all.
    for (const leaf of leaves.slice(1)) {
      store.toggle(leaf);
    }
    expect(branchTriState(teams, store)).toBe("all");
    expect(selectedLeafCount(teams, store)).toBe(totalLeafCount(teams));
  });

  it("a childless branch (no descendant leaves) reads none", () => {
    // Construct a degenerate empty branch to pin the guard (no real picker branch is
    // empty, but the renderer must not crash/mis-state on one).
    const empty: TreeNode = { treeId: "x", label: "Empty", level: 1, isLeaf: false, children: [] };
    const store = createSelectionStore();
    expect(branchTriState(empty, store)).toBe("none");
    expect(totalLeafCount(empty)).toBe(0);
  });
});

describe("toggleBranch — select-all then clear-all round-trips through the shared store", () => {
  it("fills a partial branch to all, then a second toggle clears it to none", () => {
    const store = createSelectionStore();
    const teams = drill("sport", "American football", "Leagues", "NFL", "Teams you follow");
    const leaves = descendantLeafSelections(teams);

    // Pre-select 2 leaves → branch is partial; toggleBranch must FILL (not flip off).
    store.toggle(leaves[0]);
    store.toggle(leaves[1]);
    expect(branchTriState(teams, store)).toBe("some");

    toggleBranch(teams, store);
    expect(branchTriState(teams, store)).toBe("all");
    expect(store.count()).toBe(leaves.length);

    // Second toggle on a full branch CLEARS every descendant leaf.
    toggleBranch(teams, store);
    expect(branchTriState(teams, store)).toBe("none");
    expect(store.count()).toBe(0);
  });

  it("a higher branch (NFL) selects/clears across its nested sets at once", () => {
    const store = createSelectionStore();
    const nfl = drill("sport", "American football", "Leagues", "NFL");
    const total = totalLeafCount(nfl); // Teams (16) + People (6) = 22
    expect(total).toBe(22);

    toggleBranch(nfl, store);
    expect(branchTriState(nfl, store)).toBe("all");
    expect(store.count()).toBe(total);

    toggleBranch(nfl, store);
    expect(store.count()).toBe(0);
  });
});

describe("addCustomLeaf — free-text custom under a set-scoped branch", () => {
  it("creates a freetext follow with the nearest set id and the set's item-path", () => {
    const store = createSelectionStore();
    const teams = drill("sport", "American football", "Leagues", "NFL", "Teams you follow");

    const created = addCustomLeaf(teams, "  My Local Team  ", store);
    if (!created) {
      throw new Error("expected a custom selection");
    }
    // setId is the nearest enclosing set; path = the set's item-path + the typed label.
    expect(created.followId).toBe("sport/american-football/leagues/nfl/teams-you-follow/my-local-team");
    expect(created.path).toEqual(["Sport", "American football", "NFL", "My Local Team"]);
    expect(created.kind).toBe("freetext");
    expect(created.source).toBe("custom");
    expect(store.has(created.followId)).toBe(true);
  });

  it("returns null (no-op) on an empty value or a branch without a set context", () => {
    const store = createSelectionStore();
    const teams = drill("sport", "American football", "Leagues", "NFL", "Teams you follow");
    expect(addCustomLeaf(teams, "   ", store)).toBeNull();

    // A category root has no enclosing set → no add-input → no custom.
    const sportRoot = buildOutlineTree(PICKER_TREE).find((root) => root.treeId === "sport");
    if (!sportRoot) {
      throw new Error("sport root missing");
    }
    expect(addCustomLeaf(sportRoot, "Anything", store)).toBeNull();
    expect(store.count()).toBe(0);
  });
});
