"use client";

/**
 * TopicTree — the Blip Flow Stage 1 "Topic tree" screen (the dark-editorial visual
 * replacement for {@link OnboardingPicker}). It renders the REAL recursive interest
 * tree (`PICKER_TREE`) as a branch/leaf outline with tri-state checkboxes ("ticking a
 * branch takes everything in it"), per-branch Add-custom inputs, and a live footer
 * count + Done CTA — pixel-sourced from the prototype's `blip-tree.js` + `blip-flow.css`.
 *
 * It drives the SAME shared {@link SelectionStore} and the SAME completion contract as
 * the picker it replaces: `onComplete(store.all())`, where Done is ALWAYS enabled and a
 * zero-selection completion is valid (skippable). All selection math lives in the pure,
 * tested `@/lib/treeSelection` helper; this file is rendering + open/closed UI state.
 *
 * Static-export safe: client-only (`"use client"`), no `window`/server APIs at module
 * scope. Styling comes from the verbatim `src/styles/blip-flow.css` (imported here)
 * scoped under `.tree-view`; the only inline style is the `--ac` accent CSS variable.
 *
 * Deferred (Stage 1, by design): the live registry Show-more/Add-your-own search is
 * NOT called — `moreSeeds` are folded inline as extra leaves (the offline fallback) and
 * customs are free-text only. See the execution report.
 */

import "@/styles/blip-flow.css";
import { type CSSProperties, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { BlipIconDefs } from "@/components/blip/BlipIconDefs";
import { createSelectionStore, PICKER_TREE } from "@/lib/followSets";
import { logger } from "@/lib/logger";
import {
  addCustomLeaf,
  branchTriState,
  buildOutlineTree,
  selectedLeafCount,
  type TreeNode,
  toggleBranch,
  toggleLeaf,
  totalLeafCount,
} from "@/lib/treeSelection";
import type { FollowSelection, SelectionStore } from "@/types/picker";

/** The single accent used on this screen (Tech cyan) — set as the `--ac` CSS var. */
const ACCENT_CYAN = "#22D3EE";

export interface TopicTreeProps {
  /**
   * Called when the user taps Done. Receives the canonical, deduped follows
   * (`store.all()`) — an EMPTY array is a valid completion (skippable, like the picker).
   */
  onComplete: (selections: FollowSelection[]) => void;
}

/** Subscribe to the store snapshot so counts + tri-states re-render on any change. */
function useStoreSnapshot(store: SelectionStore): readonly FollowSelection[] {
  return useSyncExternalStore(
    (listener) => store.subscribe(listener),
    () => store.getSnapshot(),
    () => store.getSnapshot(),
  );
}

/** The tri-state checkbox button (`.cbox none|some|all`) — the check resolves `#i-check`. */
function CheckBox({ state, onClick }: { state: "none" | "some" | "all"; onClick: () => void }) {
  return (
    <button type="button" className={`cbox ${state}`} onClick={onClick} aria-label="Toggle selection">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <use href="#i-check" />
      </svg>
    </button>
  );
}

/** Props shared by every outline row so a node can render itself + recurse. */
interface TreeRowProps {
  node: TreeNode;
  store: SelectionStore;
  openIds: Set<string>;
  onToggleOpen: (treeId: string) => void;
  onAddCustom: (node: TreeNode, value: string) => void;
}

/** One outline row + (when open) its children + per-branch add-input. Recursive. */
function TreeRow({ node, store, openIds, onToggleOpen, onAddCustom }: TreeRowProps) {
  // Subscribe so this row's tri-state/badge/selected styling tracks the store.
  useStoreSnapshot(store);
  const [customValue, setCustomValue] = useState("");

  const isBranch = !node.isLeaf;
  const isOpen = openIds.has(node.treeId);
  const state = isBranch ? branchTriState(node, store) : toLeafState(node, store);
  const rowOn = state !== "none";

  const rootClass = node.level === 0 ? " root" : "";
  const caretClass = isBranch ? (isOpen ? "caret open" : "caret") : "caret leafpad";

  const handleBox = () => {
    if (isBranch) {
      toggleBranch(node, store);
    } else {
      toggleLeaf(node, store);
    }
  };
  const handleLabel = isBranch ? () => onToggleOpen(node.treeId) : handleBox;

  return (
    <div className={`tnode lvl${node.level}${rootClass}`}>
      <div className={`trow${rowOn ? " on" : ""}`}>
        <button
          type="button"
          className={caretClass}
          onClick={isBranch ? () => onToggleOpen(node.treeId) : undefined}
          aria-label={isBranch ? (isOpen ? "Collapse" : "Expand") : undefined}
          tabIndex={isBranch ? 0 : -1}
        >
          ›
        </button>
        <CheckBox state={state} onClick={handleBox} />
        {/* The label is a button so it is keyboard-reachable (the prototype binds click). */}
        <button type="button" className="tlabel" onClick={handleLabel} style={LABEL_BUTTON_RESET}>
          {node.label}
        </button>
        {isBranch ? <BranchBadge node={node} store={store} /> : null}
      </div>

      {isBranch && isOpen ? (
        <div className="tchildren">
          {node.children.map((child) => (
            <TreeRow
              key={child.treeId}
              node={child}
              store={store}
              openIds={openIds}
              onToggleOpen={onToggleOpen}
              onAddCustom={onAddCustom}
            />
          ))}
          {/* Add-custom input only where a set context exists (matches Add-your-own). */}
          {node.enclosingSetId !== undefined ? (
            <div className="addchip">
              <input
                value={customValue}
                placeholder={`+ Add to ${node.label}`}
                maxLength={80}
                onChange={(event) => setCustomValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && customValue.trim() !== "") {
                    onAddCustom(node, customValue.trim());
                    setCustomValue("");
                  }
                }}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** The `n/total` (lit) or bare `total` badge for a branch row. */
function BranchBadge({ node, store }: { node: TreeNode; store: SelectionStore }) {
  const total = totalLeafCount(node);
  const on = selectedLeafCount(node, store);
  return <span className={`tbadge${on ? " lit" : ""}`}>{on ? `${on}/${total}` : `${total}`}</span>;
}

/** A leaf's box is binary: `all` when selected, `none` otherwise (no `some`). */
function toLeafState(node: TreeNode, store: SelectionStore): "none" | "all" {
  return node.leafSelection && store.has(node.leafSelection.followId) ? "all" : "none";
}

/** Reset native button chrome so `.tlabel` matches the prototype's `<span>` look. */
const LABEL_BUTTON_RESET: CSSProperties = {
  background: "none",
  border: "none",
  padding: 0,
  textAlign: "left",
  font: "inherit",
};

/**
 * The minimal full-bleed surface standing in for the prototype's `.screen` (NOT the
 * reviewer bezel). Relative + full-height on the dark canvas so `.tree-view`'s
 * `position:absolute; inset:0` fills the app surface on device.
 */
const SCENE_SURFACE_STYLE: CSSProperties = {
  position: "relative",
  minHeight: "100dvh",
  width: "100%",
  background: "#020617",
  color: "#fff",
  overflow: "hidden",
};

/** Collect every branch `treeId` (for Expand-all). */
function collectBranchIds(nodes: TreeNode[], out: Set<string>): void {
  for (const node of nodes) {
    if (!node.isLeaf) {
      out.add(node.treeId);
      collectBranchIds(node.children, out);
    }
  }
}

/**
 * Render the Stage 1 topic tree.
 *
 * @param props - {@link TopicTreeProps}.
 *
 * @example
 * <TopicTree onComplete={(selections) => persist(selections)} />
 */
export function TopicTree({ onComplete }: TopicTreeProps) {
  // ONE store per session (lazy useRef init) — never re-created on render.
  const storeRef = useRef<SelectionStore | null>(null);
  if (storeRef.current === null) {
    storeRef.current = createSelectionStore();
  }
  const store = storeRef.current;

  // The outline tree is derived once from the static PICKER_TREE.
  const roots = useMemo(() => buildOutlineTree(PICKER_TREE), []);
  const allBranchIds = useMemo(() => {
    const set = new Set<string>();
    collectBranchIds(roots, set);
    return set;
  }, [roots]);
  // Total selectable leaves across the whole tree (the `tt` denominator).
  const totalTopics = useMemo(() => roots.reduce((sum, root) => sum + totalLeafCount(root), 0), [roots]);

  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set());
  const selections = useStoreSnapshot(store);

  // Expand-all is true only when EVERY root category is open (matches the prototype's
  // `allOpen` over top-level kids; "Collapse all" then clears the whole open set).
  const allRootsOpen = roots.every((root) => openIds.has(root.treeId));

  const toggleOpen = (treeId: string) => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(treeId)) {
        next.delete(treeId);
      } else {
        next.add(treeId);
      }
      return next;
    });
  };

  const handleExpandAll = () => {
    setOpenIds(allRootsOpen ? new Set() : new Set(allBranchIds));
  };

  const handleAddCustom = (node: TreeNode, value: string) => {
    const created = addCustomLeaf(node, value, store);
    if (created) {
      logger.info("topic_tree_custom_added", { set_id: node.enclosingSetId, follow_id: created.followId });
    }
  };

  const handleDone = () => {
    const finalSelections = store.all();
    logger.info("topic_tree_done", { selected_count: finalSelections.length });
    onComplete(finalSelections);
  };

  const total = selections.length;
  // Footer preview: the most-recent labels (newest first), matching the prototype.
  const previewLabels = selections
    .slice(-3)
    .reverse()
    .map((selection) => selection.label);
  const previewText =
    total > 0 ? `${previewLabels.join(" · ")}${total > 3 ? ` +${total - 3}` : ""}` : "Tick topics across your tree";

  return (
    // Full-bleed surface that gives `.tree-view` (position:absolute; inset:0) its
    // sizing context WITHOUT the prototype's reviewer bezel — on iOS the device IS the
    // phone (port note 4). A relative, min-h-dvh #020617 column stands in for `.screen`.
    <div style={SCENE_SURFACE_STYLE}>
      <div className="tree-view" style={{ "--ac": ACCENT_CYAN } as CSSProperties}>
        <BlipIconDefs />

        <div className="tband">
          <div className="kick">
            <span className="kd" />
            Your interests · Go deeper
          </div>
          <h2>Your topic tree</h2>
          <div className="sub">Expand a branch, tick what&apos;s inside. Ticking a branch takes everything in it.</div>
        </div>

        <div className="ttool">
          <span className="tt">
            {roots.length} branches · {totalTopics} topics
          </span>
          <button type="button" className="exp" onClick={handleExpandAll}>
            {allRootsOpen ? "Collapse all" : "Expand all"}
          </button>
        </div>

        <div className="tree">
          {roots.map((root) => (
            <TreeRow
              key={root.treeId}
              node={root}
              store={store}
              openIds={openIds}
              onToggleOpen={toggleOpen}
              onAddCustom={handleAddCustom}
            />
          ))}
        </div>

        <div className="tfoot">
          <div>
            <div className="n">{total}</div>
            <div className="nl">topics</div>
          </div>
          <div className="mid">{previewText}</div>
          <button type="button" className="tcta" onClick={handleDone}>
            Done →
          </button>
        </div>
      </div>
    </div>
  );
}
