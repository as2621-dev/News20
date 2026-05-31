import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { BiasBar, computeBiasSegmentProportions } from "@/components/detail/BiasBar";
import { OpposingViewCard } from "@/components/detail/OpposingViewCard";
import { TrustStrip } from "@/components/detail/TrustStrip";
import type { TrustSummary } from "@/types/detail";

/**
 * Component + unit tests for the Phase 2 SP3 trust strip (BiasBar + TrustStrip +
 * OpposingViewCard).
 *
 * Rule 9 — these encode WHY the behaviour matters, not just WHAT renders:
 *   - The bias bar's whole purpose is to show the coverage proportion HONESTLY:
 *     its segment widths must equal the NORMALIZED L/C/R counts. A test asserts
 *     the rendered widths match `count / total`, so a fabricated/wrong/un-
 *     normalized proportion FAILS (e.g. raw counts, swapped fills, or a re-derived
 *     ratio that doesn't sum to 100%).
 *   - The blindspot chip is a TRUST claim: it must appear ONLY when a side is
 *     materially under-covered (`blindspot_lean` set) and name that lean; a
 *     balanced story (`blindspot_lean = null`) must show NO blindspot chip. A test
 *     asserts both branches, so a wrong blindspot state FAILS.
 *   - The opposing-view card shows a dissenting read when present and NOTHING when
 *     null (no empty shell).
 *
 * Rendering uses React 19's `react-dom/client` + `react`'s `act` directly (no
 * @testing-library — not a project dependency; scope lock forbids adding one),
 * matching the SP2 `storyDetail.test.tsx` idiom. No Supabase / fetch is touched —
 * TrustStrip is pure-prop.
 */

/** A blindspot story: right-leaning coverage is materially under-covered. */
const BLINDSPOT_TRUST_SUMMARY: TrustSummary = {
  coverage_left_count: 9,
  coverage_center_count: 7,
  coverage_right_count: 3,
  coverage_outlet_count: 19,
  blindspot_lean: "right",
  opposing_view_text: "Critics argue the response is overblown.",
};

/** A balanced story: no under-covered side, and no opposing-view quote. */
const BALANCED_TRUST_SUMMARY: TrustSummary = {
  coverage_left_count: 6,
  coverage_center_count: 6,
  coverage_right_count: 6,
  coverage_outlet_count: 18,
  blindspot_lean: null,
  opposing_view_text: null,
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

/** Render any element into the test container and flush effects. */
function render(node: React.ReactElement): void {
  act(() => {
    root.render(node);
  });
}

/** Read the three `data-bias-segment` widths (inline style) in L/C/R order. */
function readBiasSegmentWidths(): { left: string; center: string; right: string } {
  const segments = container.querySelectorAll<HTMLElement>("[data-bias-segment]");
  const byName = (name: string): string => {
    const segment = Array.from(segments).find((node) => node.dataset.biasSegment === name);
    if (segment === undefined) {
      throw new Error(`bias segment "${name}" not rendered`);
    }
    return segment.style.width;
  };
  return { left: byName("left"), center: byName("center"), right: byName("right") };
}

describe("computeBiasSegmentProportions — the proportion math (Rule 9)", () => {
  it("normalizes counts to percentages that match count / total", () => {
    // WHY: the bar must show the HONEST proportion. 9/7/3 of 19 total → exact
    // percentages; any re-derivation that doesn't equal count/total*100 fails.
    const proportions = computeBiasSegmentProportions(9, 7, 3);
    const total = 9 + 7 + 3;
    expect(proportions.leftWidthPercent).toBe(`${(9 / total) * 100}%`);
    expect(proportions.centerWidthPercent).toBe(`${(7 / total) * 100}%`);
    expect(proportions.rightWidthPercent).toBe(`${(3 / total) * 100}%`);
  });

  it("normalized widths sum to 100% (proportions, not raw counts)", () => {
    // WHY: catches a regression that rendered RAW counts as widths (would not sum
    // to 100). Parse the percentages back out and sum them.
    const proportions = computeBiasSegmentProportions(9, 7, 3);
    const sum =
      Number.parseFloat(proportions.leftWidthPercent) +
      Number.parseFloat(proportions.centerWidthPercent) +
      Number.parseFloat(proportions.rightWidthPercent);
    expect(sum).toBeCloseTo(100, 6);
  });

  it("guards the all-zero case with equal thirds (no NaN, no divide-by-zero)", () => {
    // WHY: an edge story with zero outlets in every bucket must not produce NaN%
    // (an invisible/broken bar). It falls back to three equal thirds.
    const proportions = computeBiasSegmentProportions(0, 0, 0);
    const equalThird = `${100 / 3}%`;
    expect(proportions.leftWidthPercent).toBe(equalThird);
    expect(proportions.centerWidthPercent).toBe(equalThird);
    expect(proportions.rightWidthPercent).toBe(equalThird);
    expect(proportions.leftWidthPercent).not.toContain("NaN");
  });
});

describe("BiasBar — rendered segment widths equal the normalized counts (Rule 9)", () => {
  it("renders each segment at width = count / total", () => {
    render(<BiasBar coverageLeftCount={9} coverageCenterCount={7} coverageRightCount={3} />);
    const widths = readBiasSegmentWidths();
    const total = 9 + 7 + 3;
    // WHY: the DOM widths must equal the normalized proportions — a fabricated or
    // un-normalized width (e.g. raw "9px"/"9%") fails this exact-string compare.
    expect(widths.left).toBe(`${(9 / total) * 100}%`);
    expect(widths.center).toBe(`${(7 / total) * 100}%`);
    expect(widths.right).toBe(`${(3 / total) * 100}%`);
  });

  it("colours the fills with the bias-left/center/right tokens", () => {
    render(<BiasBar coverageLeftCount={9} coverageCenterCount={7} coverageRightCount={3} />);
    const segments = container.querySelectorAll<HTMLElement>("[data-bias-segment]");
    const classByName = (name: string): string =>
      Array.from(segments).find((node) => node.dataset.biasSegment === name)?.className ?? "";
    expect(classByName("left")).toContain("bg-bias-left");
    expect(classByName("center")).toContain("bg-bias-center");
    expect(classByName("right")).toContain("bg-bias-right");
  });
});

describe("TrustStrip — blindspot present/absent branch + outlet count (Rule 9)", () => {
  it("shows the blindspot chip naming the under-covered lean when blindspot_lean is set", () => {
    render(<TrustStrip trustSummary={BLINDSPOT_TRUST_SUMMARY} />);
    // WHY: a blindspot story must surface the under-covered lean as a chip.
    const chip = container.querySelector<HTMLElement>('[data-blindspot-chip="present"]');
    expect(chip).not.toBeNull();
    expect(chip?.textContent).toContain("BLINDSPOT");
    expect(chip?.textContent).toContain("RIGHT");
  });

  it("shows NO chip at all when blindspot_lean is null", () => {
    render(<TrustStrip trustSummary={BALANCED_TRUST_SUMMARY} />);
    // WHY (owner decision — plan's literal DoD): a balanced story must NOT claim a
    // blindspot AND must NOT show any chip (no "BALANCED" fallback). Any chip
    // rendered here is wrong. A blindspot state that leaks the chip fails.
    expect(container.querySelector("[data-blindspot-chip]")).toBeNull();
    expect(container.textContent).not.toContain("BLINDSPOT");
    expect(container.textContent).not.toContain("BALANCED");
  });

  it("renders the bias bar with normalized widths inside the strip", () => {
    render(<TrustStrip trustSummary={BLINDSPOT_TRUST_SUMMARY} />);
    const widths = readBiasSegmentWidths();
    const total = 9 + 7 + 3;
    expect(widths.left).toBe(`${(9 / total) * 100}%`);
    expect(widths.center).toBe(`${(7 / total) * 100}%`);
    expect(widths.right).toBe(`${(3 / total) * 100}%`);
  });

  it("shows the COVERED BY N OUTLETS count from coverage_outlet_count", () => {
    render(<TrustStrip trustSummary={BLINDSPOT_TRUST_SUMMARY} />);
    expect(container.textContent).toContain("COVERED BY 19 OUTLETS");
  });
});

describe("OpposingViewCard — renders the quote when present, nothing when null", () => {
  it("renders the opposing-view quote when opposing_view_text is present", () => {
    render(<OpposingViewCard opposingViewText="Critics argue the response is overblown." />);
    expect(container.textContent).toContain("THE OPPOSING VIEW");
    expect(container.textContent).toContain("Critics argue the response is overblown.");
  });

  it("renders nothing when opposing_view_text is null (no empty shell)", () => {
    // WHY: nullable opposing view — a null must render NO card (the component
    // returns null), not an empty sage box.
    render(<OpposingViewCard opposingViewText={null} />);
    expect(container.textContent).not.toContain("THE OPPOSING VIEW");
    expect(container.querySelector("p")).toBeNull();
  });

  it("TrustStrip omits the opposing-view card for a story with a null opposing view", () => {
    render(<TrustStrip trustSummary={BALANCED_TRUST_SUMMARY} />);
    // WHY: TrustStrip must defer the null-branch to OpposingViewCard — a balanced
    // story with no opposing view shows no opposing-view label anywhere.
    expect(container.textContent).not.toContain("THE OPPOSING VIEW");
  });
});
