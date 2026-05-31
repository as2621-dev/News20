"use client";

/**
 * TrustStrip — the Detail "COVERAGE" authority strip (port-map §2 row 6, §4;
 * prototype trust card + `biasBar()` in `app.js`).
 *
 * Composes, on the editorial trust-card surface, the four coverage signals SP3
 * ships:
 *   1. the `COVERAGE` mono label + the **blindspot chip** (shown ONLY when
 *      `blindspot_lean` is non-null — no chip at all when balanced),
 *   2. the {@link BiasBar} (L/C/R proportions from the coverage counts),
 *   3. the **"COVERED BY N OUTLETS"** mono count (`coverage_outlet_count`),
 *   4. the {@link OpposingViewCard} (the opposing-view quote — renders nothing
 *      when `opposing_view_text` is null).
 *
 * **Prop contract (preserved from the SP2 stub).** `StoryDetail` passes the
 * already-fetched {@link TrustSummary} (`detail.trust_summary`), so the strip reads
 * coverage counts + blindspot + opposing view straight off it — no extra fetch.
 * The {@link TrustStripProps} shape is unchanged from SP2 (`StoryDetail` mounts
 * `<TrustStrip trustSummary={detail.trust_summary} />`).
 *
 * **Blindspot branch (the DoD seam).** The `>70%-one-side` rule is applied at
 * write time, so SP3 only branches on `blindspot_lean` being `null` vs set: a set
 * lean renders the blush blindspot chip naming the under-covered lean; a `null`
 * lean renders **no chip at all** (the plan's literal DoD — owner decision).
 *
 * @example
 * <TrustStrip trustSummary={detail.trust_summary} />
 */

import type { CSSProperties } from "react";
import { BiasBar } from "@/components/detail/BiasBar";
import { OpposingViewCard } from "@/components/detail/OpposingViewCard";
import type { TrustSummary } from "@/types/detail";

/**
 * The blindspot chip style — the prototype's blush treatment: `#E8B7BC` text on a
 * `rgba(232,183,188,.1)` field with a `rgba(232,183,188,.3)` border. The contrast
 * caveat (port-map §4) flags `#E8B7BC` as too thin for small mono *bare on
 * `#020617`*; here it sits on its own tinted blush field + border, the prototype's
 * accepted blindspot treatment, so it stays legible.
 */
const BLINDSPOT_CHIP_STYLE: CSSProperties = {
  color: "#E8B7BC",
  backgroundColor: "rgba(232,183,188,0.1)",
  border: "1px solid rgba(232,183,188,0.3)",
};

/**
 * Props for the Detail coverage/trust strip.
 *
 * SP3 keeps this interface verbatim from the SP2 stub: `StoryDetail` passes the
 * populated {@link TrustSummary} it already fetched, so the strip needs no own data
 * load.
 */
export interface TrustStripProps {
  /**
   * The per-story coverage/trust summary (coverage L/C/R counts, outlet count,
   * nullable `blindspot_lean`, nullable `opposing_view_text`). From
   * `fetchStoryDetail(...).trust_summary`.
   */
  trustSummary: TrustSummary;
}

/**
 * Render the coverage/trust strip: blindspot chip (only when set) → bias bar →
 * outlet count → opposing-view card.
 *
 * Reads {@link TrustStripProps.trustSummary}; branches the blindspot chip on
 * `blindspot_lean` (null → NO chip, set → blindspot chip naming the lean) and
 * defers the opposing-view null-branch to {@link OpposingViewCard}.
 */
export function TrustStrip({ trustSummary }: TrustStripProps) {
  const blindspotLean = trustSummary.blindspot_lean;

  return (
    <section aria-label="Coverage" className="mt-8">
      <div className="rounded-card border border-border/[0.16] bg-white/[0.025] p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <span className="whitespace-nowrap font-mono text-[10px] tracking-[0.14em] text-white/55">COVERAGE</span>
          {blindspotLean !== null ? (
            <span
              data-blindspot-chip="present"
              className="whitespace-nowrap rounded-pill px-2 py-1 font-mono text-[9px] tracking-wide"
              style={BLINDSPOT_CHIP_STYLE}
            >
              BLINDSPOT · {blindspotLean.toUpperCase()}
            </span>
          ) : null}
        </div>

        <BiasBar
          coverageLeftCount={trustSummary.coverage_left_count}
          coverageCenterCount={trustSummary.coverage_center_count}
          coverageRightCount={trustSummary.coverage_right_count}
        />

        <div className="mt-2.5 font-mono text-[10px] tracking-wide text-white/40">
          COVERED BY {trustSummary.coverage_outlet_count} OUTLETS
        </div>
      </div>

      <OpposingViewCard opposingViewText={trustSummary.opposing_view_text} />
    </section>
  );
}
