"use client";

/**
 * TopicTree — the Blip Flow Stage 1 "Topic tree" onboarding interest picker.
 *
 * M5 (FSR) makes this picker **roots-only**: it renders ONLY the 8 canonical depth-0
 * topic categories (`PICKER_TREE` roots = AI · Geopolitics · Business · Environment ·
 * Politics · Tech · Sport · Arts) as a single flat layer of toggle rows — no caret,
 * no drill-down, no nested branches/leaves, no per-branch Add-custom. Tapping a root
 * toggles a single `topic` follow for that root category; that follow canonicalizes to
 * the depth-0 `interests` row (`interest_label`/`interest_slug` == the root) at persist
 * time, so a fresh onboarding NEVER creates a deep `user_interest_profile` row (the M5
 * collapse is a one-time historical fixup, not a recurring need).
 *
 * It drives the SAME shared {@link SelectionStore} and the SAME completion contract as
 * before: `onComplete(store.all())`, where Done is ALWAYS enabled and a zero-selection
 * completion is valid (skippable).
 *
 * Static-export safe: client-only (`"use client"`), no `window`/server APIs at module
 * scope. Styling comes from the verbatim `src/styles/blip-flow.css` (imported here)
 * scoped under `.tree-view`; the only inline style is the `--ac` accent CSS variable.
 */

import "@/styles/blip-flow.css";
import { type CSSProperties, useMemo, useRef, useSyncExternalStore } from "react";
import { BlipIconDefs } from "@/components/blip/BlipIconDefs";
import { createSelectionStore, PICKER_TREE, selectionFromNode } from "@/lib/followSets";
import { logger } from "@/lib/logger";
import type { FollowSelection, PickerCategory, SelectionStore } from "@/types/picker";

/** The single accent used on this screen (Tech cyan) — set as the `--ac` CSS var. */
const ACCENT_CYAN = "#22D3EE";

export interface TopicTreeProps {
  /**
   * Called when the user taps Done. Receives the canonical, deduped follows
   * (`store.all()`) — an EMPTY array is a valid completion (skippable, like the picker).
   */
  onComplete: (selections: FollowSelection[]) => void;
}

/**
 * One root category as a selectable {@link FollowSelection}. The selection is a `topic`
 * follow whose `followId`/`label` are the root's id (== root slug, e.g. `"ai"`) and
 * label (e.g. `"AI"`), and whose `path` is just `[label]` (a depth-0 follow has no
 * ancestry). Built via `selectionFromNode` (the same builder leaf follows use) so it
 * carries the canonical `topic:<slug>` key and persists through the existing
 * `persistPickerFollows` topic path — matching the depth-0 `interests` row by
 * label/slug, never a deep node.
 */
function rootSelection(category: PickerCategory): FollowSelection {
  return selectionFromNode({
    node: { id: category.id, label: category.label, type: "topic" },
    path: [category.label],
    source: "seed",
  });
}

/** Subscribe to the store snapshot so counts + selected styling re-render on any change. */
function useStoreSnapshot(store: SelectionStore): readonly FollowSelection[] {
  return useSyncExternalStore(
    (listener) => store.subscribe(listener),
    () => store.getSnapshot(),
    () => store.getSnapshot(),
  );
}

/** The tri-state checkbox button (`.cbox none|all`) — the check resolves `#i-check`. */
function CheckBox({ state, onClick }: { state: "none" | "all"; onClick: () => void }) {
  return (
    <button type="button" className={`cbox ${state}`} onClick={onClick} aria-label="Toggle selection">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <use href="#i-check" />
      </svg>
    </button>
  );
}

/** One roots-only row: a single depth-0 category toggle (no caret, no children). */
function RootRow({ category, store }: { category: PickerCategory; store: SelectionStore }) {
  // Subscribe so this row's box/selected styling tracks the store.
  useStoreSnapshot(store);
  const selection = useMemo(() => rootSelection(category), [category]);
  const on = store.has(selection.followId);
  const toggle = () => store.toggle(selection);

  return (
    <div className="tnode lvl0 root">
      <div className={`trow${on ? " on" : ""}`}>
        {/* No caret on a roots-only row — a non-interactive spacer keeps the grid. */}
        <span className="caret leafpad" aria-hidden="true" />
        <CheckBox state={on ? "all" : "none"} onClick={toggle} />
        <button type="button" className="tlabel" onClick={toggle} style={LABEL_BUTTON_RESET}>
          {category.label}
        </button>
      </div>
    </div>
  );
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

/**
 * Render the roots-only Stage 1 topic picker.
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

  // The 8 canonical depth-0 roots — the picker renders these and ONLY these.
  const roots = useMemo(() => PICKER_TREE, []);
  const selections = useStoreSnapshot(store);

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
    total > 0 ? `${previewLabels.join(" · ")}${total > 3 ? ` +${total - 3}` : ""}` : "Tick the topics you want";

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
            Your interests · Pick your topics
          </div>
          <h2>Your topics</h2>
          <div className="sub">Tick the top-level topics you want in your feed. You can go deeper later.</div>
        </div>

        <div className="ttool">
          <span className="tt">{roots.length} topics</span>
        </div>

        <div className="tree">
          {roots.map((root) => (
            <RootRow key={root.id} category={root} store={store} />
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
