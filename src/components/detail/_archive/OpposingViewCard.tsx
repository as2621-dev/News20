"use client";

/**
 * OpposingViewCard — the Detail "↔ THE OPPOSING VIEW" quote card (port-map §2
 * row 6; prototype `.light-card` / `.lc-quote` in `styles.css`).
 *
 * The one sparing **light** surface in the otherwise near-black Detail: a sage
 * (`surface` `#D1D4BD`) card carrying the opposing-view quote in Playfair on dark
 * ink, under a mono `↔ THE OPPOSING VIEW` label. It exists to show the reader the
 * dissenting read in its own visual register (port-map §2 row 6 trust strip).
 *
 * **Null handling (Rule 12).** `opposing_view_text` is nullable in the schema — a
 * story may have no opposing-view quote. When it is null the card renders
 * **nothing** (returns `null`), exactly as the SP2 `KeyFigureCard` omits its card
 * on a null value — no empty sage box.
 *
 * @example
 * <OpposingViewCard opposingViewText="Critics argue the response is overblown." />
 * // → a sage card with the quote in Playfair under "↔ THE OPPOSING VIEW"
 *
 * @example
 * <OpposingViewCard opposingViewText={null} />
 * // → renders nothing
 */

import type { CSSProperties } from "react";

/**
 * The mono section label colour — the prototype's `#5b5e4f` muted olive, which
 * reads on the light sage `surface` card (a dark-on-light label, deliberately not
 * a `bias-*`/`--accent` token).
 */
const OPPOSING_VIEW_LABEL_STYLE: CSSProperties = { color: "#5b5e4f" };

export interface OpposingViewCardProps {
  /**
   * The opposing-view quote (`trust_summary.opposing_view_text`), or `null` when
   * the story has none. A null value renders no card.
   */
  opposingViewText: string | null;
}

/**
 * Render the opposing-view quote card, or nothing when there is no opposing view.
 *
 * Reads {@link OpposingViewCardProps.opposingViewText}; on null returns `null`
 * (no empty card), mirroring `KeyFigureCard`'s null-omission idiom.
 */
export function OpposingViewCard({ opposingViewText }: OpposingViewCardProps) {
  // Reason: nullable opposing view — omit the card entirely rather than render an
  // empty sage shell (matches KeyFigureCard's null branch).
  if (opposingViewText === null) {
    return null;
  }

  return (
    <div className="mt-4 rounded-card bg-surface p-5 text-[#1b1d18]">
      <div className="font-mono text-[10px] tracking-[0.14em]" style={OPPOSING_VIEW_LABEL_STYLE}>
        ↔ THE OPPOSING VIEW
      </div>
      <p className="mt-2 font-serif text-[17px] leading-[1.55]">{opposingViewText}</p>
    </div>
  );
}
