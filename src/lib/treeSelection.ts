/**
 * treeSelection — the pure outline-tree adaptation of the recursive picker model
 * (Blip Flow Stage 1, the "Topic tree" screen).
 *
 * The Blip topic tree is a branch/leaf outline with tri-state checkboxes ("ticking a
 * branch takes everything in it") plus a per-branch Add-custom input. The REAL app's
 * interest data is the recursive `PICKER_TREE` (`PickerCategory → PickerSubcategory →
 * PickerFollowSet → PickerNode`, with items that can recurse via their own `sets`).
 *
 * This module is the **adapter + selection math** between those two shapes. It is
 * deliberately framework-free (no React) so the non-trivial logic — flattening the
 * deep tree into uniform branch/leaf {@link TreeNode}s, computing a branch's tri-state
 * over its descendant leaves, and building the exact `FollowSelection`s a leaf/branch
 * toggle must produce — is unit-testable in isolation. `TopicTree.tsx` renders these
 * nodes and drives the shared {@link SelectionStore}; it holds NO selection math.
 *
 * ── `path` / `setId` convention (MUST match `FollowSet`/`FollowChip`) ──────────────
 * Selections are keyed by a path-derived id; the registry rows + persisted follows
 * depend on it. The established convention (see `OnboardingPicker`/`FollowSet`):
 *   - `path` = `[Category.label, Subcategory.label, …ancestor ITEM labels…, leaf.label]`.
 *     FollowSet labels are NEVER in the path — only category, subcategory, and the
 *     item labels along the chain. This matches `FollowSet`'s `[...path, node.label]`
 *     where the set's own `path` is `[Category, Sub]` (top-level) or `[…, parentItem]`
 *     (nested) — i.e. the set label is dropped, the parent item label is kept.
 *   - A free-text custom uses the NEAREST enclosing set's `id` as its `setId`, and a
 *     path of `[…that set's item-path…, typedLabel]` — exactly `FollowSet.submitCustom`.
 */

import { selectionFromFreeText, selectionFromNode } from "@/lib/followSets";
import type { FollowSelection, PickerCategory, PickerFollowSet, PickerNode, SelectionStore } from "@/types/picker";

/**
 * A uniform node in the flattened outline tree. Every category, subcategory,
 * follow-set, and item becomes one of these so the renderer can treat them
 * identically (caret, tri-state box, label, badge, children, add-input).
 */
export interface TreeNode {
  /**
   * Stable id for open/closed UI state + React keys. For item/category/subcategory/
   * set nodes this is the underlying model id (so it survives re-renders). For a
   * leaf it equals the node's `followId` (its selection key).
   */
  treeId: string;
  /** Display label rendered on the row. */
  label: string;
  /** Outline depth (0 = category root row), drives the `lvl{n}` CSS class. */
  level: number;
  /** A branch has children; a leaf is selectable (and has none). */
  isLeaf: boolean;
  /** Child rows (branches only). */
  children: TreeNode[];
  /**
   * The selection this node toggles when it is a LEAF. Undefined for branches
   * (branches toggle their descendant leaves via {@link branchTriState}/select-all).
   */
  leafSelection?: FollowSelection;
  /**
   * The nearest enclosing follow-set id, present on branches/leaves that sit under a
   * set — the `setId` an Add-custom input uses. Absent above the set level (category/
   * subcategory rows get no add-input, matching the current Add-your-own placement).
   */
  enclosingSetId?: string;
  /**
   * The path an Add-custom input submits under (the enclosing context's item-path).
   * Present iff {@link enclosingSetId} is present.
   */
  addPath?: string[];
}

/** The tri-state of a branch over its descendant leaves (prototype `cbox` classes). */
export type BranchState = "none" | "some" | "all";

/**
 * Collect every descendant LEAF selection under a node (or the node itself if it is a
 * leaf). The order is stable (depth-first, source order) so select-all is deterministic.
 *
 * @param node - The tree node to walk.
 * @returns The flat list of leaf selections beneath it.
 *
 * @example
 * descendantLeafSelections(nbaBranch).length; // number of NBA leaf topics/entities
 */
export function descendantLeafSelections(node: TreeNode): FollowSelection[] {
  if (node.isLeaf) {
    return node.leafSelection ? [node.leafSelection] : [];
  }
  const out: FollowSelection[] = [];
  for (const child of node.children) {
    out.push(...descendantLeafSelections(child));
  }
  return out;
}

/**
 * Compute a branch's tri-state from how many of its descendant leaves are selected in
 * the store: `none` (zero on), `all` (every leaf on), else `some`. A childless branch
 * (no descendant leaves) reads `none`. Mirrors the prototype `S.state`.
 *
 * @param node - The branch node.
 * @param store - The shared selection store (read via `has`).
 * @returns `"none" | "some" | "all"`.
 */
export function branchTriState(node: TreeNode, store: SelectionStore): BranchState {
  const leaves = descendantLeafSelections(node);
  if (leaves.length === 0) {
    return "none";
  }
  const onCount = leaves.filter((selection) => store.has(selection.followId)).length;
  if (onCount === 0) {
    return "none";
  }
  return onCount === leaves.length ? "all" : "some";
}

/** How many descendant leaves of a branch are currently selected (the `n/total` badge). */
export function selectedLeafCount(node: TreeNode, store: SelectionStore): number {
  return descendantLeafSelections(node).filter((selection) => store.has(selection.followId)).length;
}

/** Total descendant leaves under a branch (the badge denominator). */
export function totalLeafCount(node: TreeNode): number {
  return descendantLeafSelections(node).length;
}

/**
 * Toggle a single leaf node in the store. Thin pass-through so the renderer never
 * touches the store API directly (keeps the toggle semantics in one tested place).
 *
 * @param node - A leaf tree node (must carry `leafSelection`).
 * @param store - The shared store.
 * @returns The new selected state, or `false` for a branch/empty leaf (no-op).
 */
export function toggleLeaf(node: TreeNode, store: SelectionStore): boolean {
  if (!node.leafSelection) {
    return false;
  }
  return store.toggle(node.leafSelection);
}

/**
 * Tri-state branch toggle: if the branch is NOT fully selected, select every
 * descendant leaf; if it is fully selected, clear them all. Reaches the target state
 * by toggling only the leaves not already at it (so a partial branch fills up rather
 * than flipping). Mirrors the prototype `S.toggleBranch`.
 *
 * @param node - The branch node.
 * @param store - The shared store.
 *
 * @example
 * // NBA branch with 2 of 5 teams on → selects the remaining 3 (now "all").
 * toggleBranch(nbaBranch, store);
 */
export function toggleBranch(node: TreeNode, store: SelectionStore): void {
  const leaves = descendantLeafSelections(node);
  if (leaves.length === 0) {
    return;
  }
  const turnOn = leaves.some((selection) => !store.has(selection.followId));
  for (const selection of leaves) {
    const isOn = store.has(selection.followId);
    if (isOn !== turnOn) {
      store.toggle(selection);
    }
  }
}

/**
 * Add a free-text custom leaf under a branch that has an enclosing set, returning the
 * created (and now-selected) selection so the caller can surface it. No-op (returns
 * `null`) on a branch without a set context or an empty value. Mirrors the prototype's
 * per-branch `+ Add to <label>` input via `FollowSet.submitCustom`'s free-text path.
 *
 * @param node - The branch node the input sits under (must carry `enclosingSetId` + `addPath`).
 * @param rawValue - The user-typed value (trimmed + length-clamped here).
 * @param store - The shared store.
 * @returns The created selection, or `null` when nothing was added.
 */
export function addCustomLeaf(node: TreeNode, rawValue: string, store: SelectionStore): FollowSelection | null {
  const trimmed = rawValue.trim().slice(0, 80);
  if (trimmed === "" || node.enclosingSetId === undefined || node.addPath === undefined) {
    return null;
  }
  const selection = selectionFromFreeText({
    label: trimmed,
    setId: node.enclosingSetId,
    path: [...node.addPath, trimmed],
  });
  store.toggle(selection);
  return selection;
}

/**
 * Build a leaf {@link TreeNode} from a picker item that has no nested sets. Its
 * selection is built EXACTLY as `FollowChip`/`FollowSet` build it (`selectionFromNode`,
 * `source:"seed"`, path = ancestor item-path + the node's own label).
 */
function buildLeafNode(node: PickerNode, itemPath: string[], level: number): TreeNode {
  const leafSelection = selectionFromNode({ node, path: [...itemPath, node.label], source: "seed" });
  return {
    treeId: leafSelection.followId,
    label: node.label,
    level,
    isLeaf: true,
    children: [],
    leafSelection,
  };
}

/**
 * Build the children of a follow-set: its seed items (and their `moreSeeds`, appended
 * inline as additional leaves so they participate in the set's tri-state — the offline
 * fallback; the live registry is NOT called in Stage 1). An item WITH its own `sets`
 * becomes a branch whose children are those nested sets (recursing through
 * {@link buildSetNode}); an item without sets is a leaf.
 *
 * @param set - The follow-set whose items become rows.
 * @param itemPath - The ancestor item-path this set's items extend (no set label).
 * @param level - The depth of the items (the set is at `level - 1`).
 */
function buildSetItemNodes(set: PickerFollowSet, itemPath: string[], level: number): TreeNode[] {
  // moreSeeds are folded in beside the seed items as plain leaves (offline fallback).
  const items: PickerNode[] = [...set.items, ...(set.moreSeeds ?? [])];
  return items.map((item) => {
    if (item.sets && item.sets.length > 0) {
      // A recursive item: a branch whose children are its nested sets. The nested
      // sets' item-path extends with THIS item's label (set labels stay out of path).
      const childItemPath = [...itemPath, item.label];
      const children = item.sets.map((childSet) => buildSetNode(childSet, childItemPath, level + 1));
      return {
        treeId: item.id,
        label: item.label,
        level,
        isLeaf: false,
        children,
      };
    }
    return buildLeafNode(item, itemPath, level);
  });
}

/**
 * Build a branch {@link TreeNode} for a follow-set. Its children are the set's items;
 * its add-input context is THIS set (so a custom typed under it resolves to this set's
 * id with the correct item-path). The set's own label is the branch label (eyebrow).
 *
 * @param set - The follow-set.
 * @param itemPath - The ancestor item-path the set hangs under (no set label).
 * @param level - The branch depth.
 */
function buildSetNode(set: PickerFollowSet, itemPath: string[], level: number): TreeNode {
  return {
    treeId: set.id,
    label: set.label,
    level,
    isLeaf: false,
    children: buildSetItemNodes(set, itemPath, level + 1),
    enclosingSetId: set.id,
    addPath: itemPath,
  };
}

/**
 * Build a subcategory branch {@link TreeNode}: its children are its follow-set branches.
 * The item-path passed down is `[Category.label, Subcategory.label]` — the base every
 * descendant leaf extends.
 */
function buildSubcategoryNode(sub: PickerCategory["subs"][number], categoryLabel: string, level: number): TreeNode {
  const itemPath = [categoryLabel, sub.label];
  return {
    treeId: sub.id,
    label: sub.label,
    level,
    isLeaf: false,
    children: sub.sets.map((set) => buildSetNode(set, itemPath, level + 1)),
  };
}

/**
 * Build a category root branch {@link TreeNode} (level 0): its children are its
 * subcategory branches. This is the entry point per top-level tree row.
 *
 * @param category - A picker category.
 * @returns The category as an outline branch with the full subtree built.
 */
export function buildCategoryNode(category: PickerCategory): TreeNode {
  return {
    treeId: category.id,
    label: category.label,
    level: 0,
    isLeaf: false,
    children: category.subs.map((sub) => buildSubcategoryNode(sub, category.label, 1)),
  };
}

/**
 * Build the full outline tree (one root per category) from the typed picker tree.
 * Pure + deterministic; computed once by `TopicTree` per mount.
 *
 * @param categories - The picker categories (defaults to the live `PICKER_TREE`).
 * @returns The category root nodes.
 *
 * @example
 * const roots = buildOutlineTree(PICKER_TREE);
 * roots[0].level; // 0
 */
export function buildOutlineTree(categories: PickerCategory[]): TreeNode[] {
  return categories.map(buildCategoryNode);
}
