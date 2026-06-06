"use client";

/**
 * KeyFigureCard — the Detail key-figure card (port-map §2 row 6, §4).
 *
 * Renders the one headline figure for a story (`"~20%"`) and what it measures
 * (`"of global oil transits Hormuz"`), accent-coded with the per-story
 * `var(--accent)` cascade (the same CSS variable the reel sets per story —
 * port-map §3.4). On the dark canvas the figure reads in the story's segment
 * accent so the card belongs to the story it describes.
 *
 * **Null handling (Rule 12).** Both key-figure fields are nullable in the schema
 * (a story may have no key figure). When {@link KeyFigure.key_figure_value} is
 * null the card renders **nothing** (returns `null`) rather than an empty box —
 * the Detail body simply omits the card.
 *
 * @example
 * <KeyFigureCard keyFigure={{ key_figure_value: "~20%", key_figure_label: "of global oil transits Hormuz" }} />
 * // → a card showing "~20%" in var(--accent) above the label
 *
 * @example
 * <KeyFigureCard keyFigure={{ key_figure_value: null, key_figure_label: null }} />
 * // → renders nothing
 */

import type { CSSProperties } from "react";
import type { KeyFigure } from "@/types/detail";

/** A `--accent`-reading style carrier (the figure colour comes from the cascade). */
const KEY_FIGURE_VALUE_STYLE: CSSProperties = { color: "var(--accent)" };

export interface KeyFigureCardProps {
  /**
   * The key-figure values for the story (`fetchStoryDetail(...).key_figure`).
   * `key_figure_value` is nullable; a null value renders no card.
   */
  keyFigure: KeyFigure;
}

/**
 * Render the key-figure card, or nothing when the story has no key figure.
 *
 * Reads {@link KeyFigureCardProps.keyFigure}; the figure is coloured with the
 * inherited `var(--accent)` so it matches the active story's segment accent.
 */
export function KeyFigureCard({ keyFigure }: KeyFigureCardProps) {
  // Reason: a story may carry no key figure (both fields null) — omit the card
  // entirely rather than render an empty shell.
  if (keyFigure.key_figure_value === null) {
    return null;
  }

  return (
    <div className="mt-8 rounded-card border-l-2 border-white/10 pl-4" style={{ borderColor: "var(--accent)" }}>
      <div className="font-serif text-[40px] font-bold leading-[1.05]" style={KEY_FIGURE_VALUE_STYLE}>
        {keyFigure.key_figure_value}
      </div>
      {keyFigure.key_figure_label !== null ? (
        <div className="mt-1 font-sans text-[14px] leading-snug text-white/60">{keyFigure.key_figure_label}</div>
      ) : null}
    </div>
  );
}
