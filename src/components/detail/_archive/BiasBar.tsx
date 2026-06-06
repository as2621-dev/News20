"use client";

/**
 * BiasBar — the L/C/R coverage-proportion bar (port-map §2 row 6, §4; prototype
 * `biasBar()` in `app.js`).
 *
 * Renders three side-by-side segments whose widths are the **normalized
 * proportions** of the left/center/right outlet counts, coloured with the static
 * `bias-left | bias-center | bias-right` tokens (port-map §4). Below the bar a
 * mono legend prints the raw counts (`L · 9`, `C · 7`, `R · 3`).
 *
 * **Proportion math (the DoD seam).** The segment widths are computed by the pure
 * {@link computeBiasSegmentProportions} so the math is testable in isolation: each
 * width is `count / total` as a percentage. When all three counts are zero the
 * total is zero — dividing would yield `NaN`; the all-zero guard returns three
 * equal thirds instead, so the bar still renders (an even, neutral grey-ish split)
 * rather than collapsing or throwing.
 *
 * **Contrast caveat (port-map §4 / ui-design-decisions §6).** `bias-right`
 * (`#E8B7BC`) is fine for the bar FILL but NOT for small thin mono on `#020617`.
 * So the legend's `R` label uses `text-white/55` (not the blush), while `L`/`C`
 * keep their tokens (`bias-left`/`bias-center` read fine as small mono). The bar
 * fill carries the colour encoding either way.
 *
 * @example
 * <BiasBar coverageLeftCount={9} coverageCenterCount={7} coverageRightCount={3} />
 * // → segments 47.4% / 36.8% / 15.8%; legend "L · 9  C · 7  R · 3"
 *
 * @example
 * <BiasBar coverageLeftCount={0} coverageCenterCount={0} coverageRightCount={0} />
 * // → three equal thirds (all-zero guard); legend "L · 0  C · 0  R · 0"
 */

/**
 * The three normalized segment widths (as CSS percentage strings) for the bias
 * bar, in L/C/R order. Each is `count / total * 100`; on an all-zero total each
 * is an equal third.
 */
export interface BiasSegmentProportions {
  /** Left segment width as a CSS percentage string (e.g. `"47.368%"`). */
  leftWidthPercent: string;
  /** Center segment width as a CSS percentage string. */
  centerWidthPercent: string;
  /** Right segment width as a CSS percentage string. */
  rightWidthPercent: string;
}

/**
 * Compute the normalized L/C/R segment widths for the bias bar.
 *
 * Pure + testable: the segment widths are `count / total` as percentages, so the
 * bar's visual proportion is exactly the coverage proportion. Guards the all-zero
 * case (no outlets in any bucket) by returning three equal thirds instead of
 * dividing by zero (which would yield `NaN%` and an invisible bar).
 *
 * @param coverageLeftCount - Outlets leaning left (`coverage_left_count`).
 * @param coverageCenterCount - Outlets leaning center (`coverage_center_count`).
 * @param coverageRightCount - Outlets leaning right (`coverage_right_count`).
 * @returns The three width percentage strings in L/C/R order.
 *
 * @example
 * computeBiasSegmentProportions(9, 7, 3)
 * // → { leftWidthPercent: "47.36842105263158%", centerWidthPercent: "36.84210526315789%", rightWidthPercent: "15.789473684210526%" }
 *
 * @example
 * computeBiasSegmentProportions(0, 0, 0)
 * // → { leftWidthPercent: "33.333333333333336%", ... } (equal thirds, no NaN)
 */
export function computeBiasSegmentProportions(
  coverageLeftCount: number,
  coverageCenterCount: number,
  coverageRightCount: number,
): BiasSegmentProportions {
  const totalCoverageCount = coverageLeftCount + coverageCenterCount + coverageRightCount;

  // Reason: with no outlets in any lean bucket, total is 0 — dividing would yield
  // NaN and an invisible/broken bar. Fall back to an even neutral split so the bar
  // still renders.
  if (totalCoverageCount === 0) {
    const equalThirdPercent = `${100 / 3}%`;
    return {
      leftWidthPercent: equalThirdPercent,
      centerWidthPercent: equalThirdPercent,
      rightWidthPercent: equalThirdPercent,
    };
  }

  return {
    leftWidthPercent: `${(coverageLeftCount / totalCoverageCount) * 100}%`,
    centerWidthPercent: `${(coverageCenterCount / totalCoverageCount) * 100}%`,
    rightWidthPercent: `${(coverageRightCount / totalCoverageCount) * 100}%`,
  };
}

export interface BiasBarProps {
  /** Outlets covering this story leaning left (`trust_summary.coverage_left_count`). */
  coverageLeftCount: number;
  /** Outlets leaning center (`trust_summary.coverage_center_count`). */
  coverageCenterCount: number;
  /** Outlets leaning right (`trust_summary.coverage_right_count`). */
  coverageRightCount: number;
}

/**
 * Render the L/C/R coverage-proportion bar + its mono count legend.
 *
 * Segment widths come from {@link computeBiasSegmentProportions} (normalized
 * counts, all-zero-guarded). The fills use the `bias-*` tokens; the bar track is a
 * faint white wash (prototype `.bias-bar` background).
 */
export function BiasBar({ coverageLeftCount, coverageCenterCount, coverageRightCount }: BiasBarProps) {
  const segmentProportions = computeBiasSegmentProportions(coverageLeftCount, coverageCenterCount, coverageRightCount);

  return (
    <div>
      <div className="flex h-2 overflow-hidden rounded-pill bg-white/[0.06]">
        <span
          data-bias-segment="left"
          className="block h-full bg-bias-left"
          style={{ width: segmentProportions.leftWidthPercent }}
        />
        <span
          data-bias-segment="center"
          className="block h-full bg-bias-center"
          style={{ width: segmentProportions.centerWidthPercent }}
        />
        <span
          data-bias-segment="right"
          className="block h-full bg-bias-right"
          style={{ width: segmentProportions.rightWidthPercent }}
        />
      </div>
      <div className="mt-2 flex justify-between font-mono text-[10px] tracking-wide">
        <span className="text-bias-left">L · {coverageLeftCount}</span>
        <span className="text-bias-center">C · {coverageCenterCount}</span>
        {/* Contrast caveat: bias-right (#E8B7BC) is too thin as small mono on
            #020617, so the R label uses text-white/55, not the blush token. */}
        <span className="text-white/55">R · {coverageRightCount}</span>
      </div>
    </div>
  );
}
