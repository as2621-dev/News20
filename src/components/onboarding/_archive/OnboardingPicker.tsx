"use client";

/**
 * OnboardingPicker — the page container of the recursive interest picker (Phase 5
 * SP4). It owns the SINGLE selection store (created once per session), renders the 8
 * categories from `PICKER_TREE` (collapsible category → subcategory → `FollowSet`s),
 * hosts the persistent {@link SelectionTray}, and exposes completion via `onComplete`.
 *
 * Skippable (spec §10/§11): the Continue/Skip affordance is ALWAYS enabled — it reads
 * "Continue" when ≥1 follow is selected and "Skip" at zero. A zero-follow completion
 * is valid (the caller persists nothing and routes to the breaking-news-only reel).
 *
 * Styling is self-contained via inline spec §8 hex tokens (no globals.css/tailwind
 * edits — out of SP4 scope, SP3 Concern #3). The recursion (lazy-mount + preserve-on-
 * collapse) lives in SP3's `FollowSet`/`FollowChip`; this file wires the store through
 * and provides the category/subcategory collapsible shells (spec §9 breakdown).
 */

import { type CSSProperties, useRef, useState, useSyncExternalStore } from "react";
import { FollowSet } from "@/components/onboarding/_archive/FollowSet";
import { SelectionTray } from "@/components/onboarding/_archive/SelectionTray";
import { createSelectionStore, PICKER_TREE } from "@/lib/followSets";
import type { FollowSelection, PickerCategory, SelectionStore } from "@/types/picker";

/** Spec §8 design tokens (warm editorial palette), inlined to avoid global edits. */
const TOKENS = {
  bg: "#f4f1ea",
  ink: "#1b1a17",
  muted: "#6f6a5e",
  line: "#dcd6c8",
  card: "#fffdf7",
  sel: "#3a5a40",
} as const;
const DISPLAY_FONT_STACK = '"Fraunces", Georgia, "Times New Roman", serif';
const BODY_FONT_STACK = '"Spline Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
const MONO_FONT_STACK = '"Spline Sans Mono", ui-monospace, SFMono-Regular, Menlo, monospace';

export interface OnboardingPickerProps {
  /**
   * Called when the user taps Continue/Skip. Receives the canonical, deduped follows
   * (`store.all()`) — an EMPTY array on a skip (a valid, no-error completion).
   */
  onComplete: (selections: FollowSelection[]) => void;
}

/** Subscribe to the store snapshot so the Continue/Skip label + count re-render live. */
function useStoreSnapshot(store: SelectionStore): readonly FollowSelection[] {
  return useSyncExternalStore(
    (listener) => store.subscribe(listener),
    () => store.getSnapshot(),
    () => store.getSnapshot(),
  );
}

/** A single collapsible category (spec §9 `Category`) with a live per-category count. */
function CategorySection({ category, store }: { category: PickerCategory; store: SelectionStore }) {
  const selections = useStoreSnapshot(store);
  const [open, setOpen] = useState(false);
  // Per-category count: selections whose first path segment is THIS category's label.
  const categoryCount = selections.filter((selection) => selection.path[0] === category.label).length;

  return (
    <section data-picker-category={category.id} style={{ borderBottom: `1px solid ${TOKENS.line}` }}>
      <button
        type="button"
        data-category-toggle={category.id}
        aria-expanded={open}
        onClick={() => setOpen((isOpen) => !isOpen)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
          minHeight: 52,
          padding: "12px 16px",
          background: "none",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <span style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <span style={{ fontFamily: DISPLAY_FONT_STACK, fontWeight: 600, fontSize: 19, color: TOKENS.ink }}>
            {category.label}
          </span>
          {categoryCount > 0 ? (
            <span
              data-category-count={category.id}
              style={{ fontFamily: MONO_FONT_STACK, fontSize: 11, color: "#9a4a1f" }}
            >
              {categoryCount}
            </span>
          ) : null}
        </span>
        <span
          aria-hidden
          style={{
            fontFamily: MONO_FONT_STACK,
            fontSize: 12,
            color: TOKENS.muted,
            transform: open ? "rotate(90deg)" : "none",
            transition: "transform .15s",
          }}
        >
          ›
        </span>
      </button>

      {open ? (
        <div data-category-body={category.id} style={{ padding: "0 16px 14px" }}>
          {category.subs.map((sub) => (
            <SubcategorySection key={sub.id} categoryLabel={category.label} sub={sub} store={store} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

/** A collapsible subcategory (spec §9 `Subcategory`) rendering its follow-sets. */
function SubcategorySection({
  categoryLabel,
  sub,
  store,
}: {
  categoryLabel: string;
  sub: PickerCategory["subs"][number];
  store: SelectionStore;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div data-picker-subcategory={sub.id} style={{ marginTop: 8 }}>
      <button
        type="button"
        data-subcategory-toggle={sub.id}
        aria-expanded={open}
        onClick={() => setOpen((isOpen) => !isOpen)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          width: "100%",
          minHeight: 44,
          padding: "6px 0",
          background: "none",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          fontFamily: MONO_FONT_STACK,
          fontSize: 11.5,
          letterSpacing: ".05em",
          textTransform: "uppercase",
          color: TOKENS.muted,
        }}
      >
        <span aria-hidden style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }}>
          ›
        </span>
        {sub.label}
      </button>
      {open ? (
        <div data-subcategory-body={sub.id}>
          {sub.sets.map((followSet) => (
            <FollowSet key={followSet.id} followSet={followSet} path={[categoryLabel, sub.label]} store={store} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Render the recursive interest picker page.
 *
 * @param props - {@link OnboardingPickerProps}.
 */
export function OnboardingPicker({ onComplete }: OnboardingPickerProps) {
  // Reason: ONE store per picker session, created once (a lazy useRef initializer) —
  // NOT per render, or each re-render would reset every selection.
  const storeRef = useRef<SelectionStore | null>(null);
  if (storeRef.current === null) {
    storeRef.current = createSelectionStore();
  }
  const store = storeRef.current;

  const selections = useStoreSnapshot(store);
  const hasSelections = selections.length > 0;

  const pageStyle: CSSProperties = {
    minHeight: "100dvh",
    // Leave room for the fixed tray (count row + preview) so content isn't occluded.
    paddingBottom: 180,
    background: TOKENS.bg,
    color: TOKENS.ink,
  };

  return (
    <div data-onboarding-picker="" style={pageStyle}>
      <header style={{ padding: "20px 16px 8px" }}>
        <h1 style={{ fontFamily: DISPLAY_FONT_STACK, fontWeight: 900, fontSize: 26, lineHeight: 1.1, margin: 0 }}>
          Build your briefing
        </h1>
        <p style={{ fontFamily: BODY_FONT_STACK, fontSize: 13.5, color: TOKENS.muted, margin: "6px 0 0" }}>
          Follow the topics, companies, teams, and people you care about. You can skip — we&apos;ll start with the
          day&apos;s biggest stories.
        </p>
      </header>

      <div>
        {PICKER_TREE.map((category) => (
          <CategorySection key={category.id} category={category} store={store} />
        ))}
      </div>

      {/* Continue / Skip — ALWAYS enabled (skippable, spec §10/§11). Sits above the
          fixed tray; the page bottom padding keeps it clear of the bar. */}
      <div style={{ padding: "16px 16px 0" }}>
        <button
          type="button"
          data-picker-continue=""
          onClick={() => onComplete(store.all())}
          style={{
            display: "block",
            width: "100%",
            minHeight: 48,
            background: hasSelections ? TOKENS.sel : TOKENS.ink,
            color: TOKENS.card,
            border: "none",
            borderRadius: 999,
            fontFamily: BODY_FONT_STACK,
            fontSize: 15,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          {hasSelections ? `Continue with ${selections.length}` : "Skip for now"}
        </button>
      </div>

      <SelectionTray store={store} />
    </div>
  );
}
