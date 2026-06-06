"use client";

/**
 * FollowChip — one tap-to-toggle bubble in the recursive interest picker (Phase 5
 * SP3). A real `<button>` (keyboard/screen-reader accessible) with `aria-pressed`,
 * a ≥44px touch target, the label, and (for companies) the `ticker` rendered in the
 * rust accent. Selected state is the deep-green fill; custom (Add-your-own) chips
 * get a dashed border. Styling is self-contained via the spec §8 hex tokens (inline
 * styles) so it does NOT depend on globals.css/tailwind edits (out of SP3 scope).
 *
 * Nesting: when the chip's source node carries child `sets`, the PARENT `FollowSet`
 * lazily mounts those sets on first select and collapses them on deselect (state in
 * the store, not here) — this component just signals the toggle and reflects the
 * selected state. See `FollowSet.tsx`.
 */

import type { CSSProperties } from "react";

/** Spec §8 design tokens (warm editorial palette), inlined to avoid global edits. */
const TOKENS = {
  bg: "#f4f1ea",
  ink: "#1b1a17",
  line: "#dcd6c8",
  card: "#fffdf7",
  sel: "#3a5a40",
  selInk: "#fffdf7",
  rust: "#9a4a1f",
  selTicker: "#d9e4d2",
} as const;

/** Mono stack for the ticker (Spline Sans Mono in the prototype) + system fallback. */
const MONO_FONT_STACK = '"Spline Sans Mono", ui-monospace, SFMono-Regular, Menlo, monospace';
/** Body stack for the chip label (Spline Sans in the prototype) + system fallback. */
const BODY_FONT_STACK = '"Spline Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

export interface FollowChipProps {
  /** The path-derived id of this chip's node (the `followId`). */
  followId: string;
  /** The chip label. */
  label: string;
  /** Whether this chip is currently selected (drives fill + `aria-pressed`). */
  selected: boolean;
  /** Ticker symbol (companies only) — rendered in the rust accent after the label. */
  ticker?: string;
  /** True for an Add-your-own chip → dashed border affordance (spec §8). */
  isCustom?: boolean;
  /** Toggle handler — the parent set flips store state + (un)mounts nested sets. */
  onToggle: () => void;
}

/**
 * Render one selectable bubble.
 *
 * @param props - {@link FollowChipProps}.
 *
 * @example
 * <FollowChip followId="…/nvidia" label="Nvidia" ticker="NVDA" selected onToggle={fn} />
 */
export function FollowChip({ followId, label, selected, ticker, isCustom, onToggle }: FollowChipProps) {
  const style: CSSProperties = {
    // ≥44px touch target (spec §8 mobile-first) — minHeight, not padding alone.
    minHeight: 44,
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
    padding: "6px 13px",
    fontFamily: BODY_FONT_STACK,
    fontSize: 13.5,
    lineHeight: 1.2,
    borderRadius: 999,
    borderWidth: 1.5,
    borderStyle: isCustom ? "dashed" : "solid",
    cursor: "pointer",
    transition: "background-color .12s, border-color .12s, color .12s",
    backgroundColor: selected ? TOKENS.sel : TOKENS.card,
    borderColor: selected ? TOKENS.sel : TOKENS.line,
    color: selected ? TOKENS.selInk : TOKENS.ink,
  };

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={selected}
      data-follow-chip=""
      data-follow-id={followId}
      data-selected={selected ? "true" : "false"}
      data-custom={isCustom ? "true" : "false"}
      data-ticker={ticker ?? ""}
      style={style}
    >
      <span>{label}</span>
      {ticker ? (
        <span
          data-ticker-label=""
          style={{
            fontFamily: MONO_FONT_STACK,
            fontSize: 10.5,
            // Rust accent unselected; the lighter green-tint when selected (spec §8).
            color: selected ? TOKENS.selTicker : TOKENS.rust,
          }}
        >
          {ticker}
        </span>
      ) : null}
    </button>
  );
}
