"use client";

/**
 * ReelSectionHeader — the reel's per-slot section chrome (FSR slice #8): the
 * `.seg-chip` chip carrying the slot's section header, plus — on a climbed
 * fallback slot — the honesty line above it.
 *
 * Pure presentational: it paints exactly the {@link SectionChipModel} that
 * `computeSectionChips` (`src/lib/reel/sectionChips.ts`) derived from the
 * `daily_feeds` row metadata — the user-vocabulary header with slot count
 * ("IPL — 4"), the reserved "Beyond your bubble" label, or the plain category
 * label on legacy/source rows (which makes this a drop-in replacement for the
 * old inline `.seg-chip`, unchanged rendering on metadata-less feeds).
 *
 * **The user's words stay the user's words:** a long label is clipped ONLY
 * visually (CSS ellipsis on the label span) — the DOM text is always the
 * verbatim label, never paraphrased or shortened in JS.
 */

import type { CSSProperties } from "react";
import type { SectionChipModel } from "@/lib/reel/sectionChips";

/**
 * Visual-only clipping for a long user-vocabulary label. `max-width` keeps the
 * chip inside the reel's headline column (the `.head` zone is full-width minus
 * padding); the text itself stays verbatim in the DOM.
 */
const CHIP_LABEL_CLIP_STYLE: CSSProperties = {
  maxWidth: "70vw",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

/** The honesty line above the chip — small, muted, never uppercase (it's a sentence). */
const FALLBACK_LINE_STYLE: CSSProperties = {
  display: "block",
  margin: "0 0 6px",
  fontSize: "12px",
  lineHeight: 1.35,
  color: "rgba(255,255,255,.72)",
  maxWidth: "80vw",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

export interface ReelSectionHeaderProps {
  /** The derived chip model for this slot (`computeSectionChips`, index-aligned). */
  chip: SectionChipModel;
  /** The story's segment accent hex (colours the chip text, matching the old chip). */
  accentHex: string;
}

/**
 * Render one slot's section chip (+ the honesty line when the slot was climbed).
 *
 * @example
 * <ReelSectionHeader chip={{ chip_label: "IPL — 4", fallback_label: null }} accentHex="#F59E0B" />
 */
export function ReelSectionHeader({ chip, accentHex }: ReelSectionHeaderProps) {
  return (
    <>
      {chip.fallback_label !== null ? (
        <span data-testid="section-fallback" style={FALLBACK_LINE_STYLE}>
          {chip.fallback_label}
        </span>
      ) : null}
      <div className="seg-chip" style={{ color: accentHex }}>
        <span className="seg-dot" />
        <span data-testid="section-chip-label" style={CHIP_LABEL_CLIP_STYLE}>
          {chip.chip_label}
        </span>
      </div>
    </>
  );
}
