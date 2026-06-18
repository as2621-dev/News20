import { describe, expect, it } from "vitest";
import { DEFAULT_DETAIL_CATEGORY, DETAIL_TEMPLATES, type PanelSpec, templateForCategory } from "@/lib/detailTemplates";

/**
 * Parity + shape tests for the frontend detail-template twin.
 *
 * WHY (Rule 7/9): `DETAIL_TEMPLATES` is a hand-maintained twin of the Python
 * `agents/pipeline/detail_templates.py` `DETAIL_TEMPLATES`. If the two drift, the
 * backend persists analytic panels in one slot order while the UI renders another —
 * a silent, product-visible mismatch. The `EXPECTED` table below is the SAME locked
 * table asserted in `tests/agents/pipeline/test_detail_templates.py`; keeping both
 * test files green is the drift guard. These also assert the structural invariants
 * the UI relies on (sources have no timeline/coverage; coverage only on world —
 * phase-SP1 removed the breaking detail template).
 */

/** The owner-locked table (2026-06-16; breaking removed phase-SP1) — mirrors the Python test's `_EXPECTED`. */
const EXPECTED: Record<string, Array<[string, string | null]>> = {
  world: [
    ["timeline", null],
    ["stakes", "STAKES"],
    ["coverage", "partisan"],
  ],
  markets: [
    ["timeline", null],
    ["market_impact", "MARKET IMPACT"],
    ["by_the_numbers", "BY THE NUMBERS"],
  ],
  tech: [
    ["timeline", null],
    ["why_it_matters", "WHY IT MATTERS"],
    ["the_concept", "THE CONCEPT"],
  ],
  sport: [
    ["timeline", null],
    ["stat_line", "STAT LINE"],
    ["recent_form", "RECENT FORM"],
  ],
  culture: [
    ["timeline", null],
    ["subject_profile", "PROFILE"],
    ["why_it_matters", "WHY IT MATTERS"],
  ],
  youtube: [
    ["source_context", "THE VIDEO"],
    ["key_points", "KEY POINTS"],
    ["implications", "IMPLICATIONS"],
  ],
  podcasts: [
    ["source_context", "THE EPISODE"],
    ["key_points", "KEY POINTS"],
    ["implications", "IMPLICATIONS"],
  ],
  x: [
    ["source_context", "THE GIST"],
    ["key_points", "KEY POINTS"],
    ["implications", "IMPLICATIONS"],
  ],
};

/** Flatten one spec to the `[kind|panel_kind, label|mode|null]` shape EXPECTED uses. */
function specTuple(spec: PanelSpec): [string, string | null] {
  if (spec.panel_kind === "timeline") {
    return ["timeline", null];
  }
  if (spec.panel_kind === "coverage") {
    return ["coverage", spec.coverage_mode ?? null];
  }
  return [spec.analytic_kind ?? "", spec.analytic_tab_label ?? null];
}

describe("DETAIL_TEMPLATES (frontend twin)", () => {
  it("covers exactly the 8 detail categories (no breaking — phase-SP1)", () => {
    expect(Object.keys(DETAIL_TEMPLATES).sort()).toEqual(Object.keys(EXPECTED).sort());
    expect(Object.keys(DETAIL_TEMPLATES)).not.toContain("breaking");
    expect(Object.keys(DETAIL_TEMPLATES)).toHaveLength(8);
  });

  for (const category of Object.keys(EXPECTED)) {
    it(`${category} matches the locked Python table exactly`, () => {
      const actual = DETAIL_TEMPLATES[category as keyof typeof DETAIL_TEMPLATES].map(specTuple);
      expect(actual).toEqual(EXPECTED[category]);
    });
  }

  it("gives source categories no timeline and no coverage", () => {
    for (const category of ["youtube", "podcasts", "x"] as const) {
      const kinds = new Set(DETAIL_TEMPLATES[category].map((s) => s.panel_kind));
      expect(kinds).toEqual(new Set(["analytic"]));
    }
  });

  it("places coverage only on world (phase-SP1 removed the breaking template)", () => {
    const withCoverage = Object.entries(DETAIL_TEMPLATES)
      .filter(([, specs]) => specs.some((s) => s.panel_kind === "coverage"))
      .map(([category]) => category)
      .sort();
    expect(withCoverage).toEqual(["world"]);
  });
});

describe("templateForCategory (defensive resolver)", () => {
  it("returns the matching template for a known category", () => {
    expect(templateForCategory("markets")).toBe(DETAIL_TEMPLATES.markets);
  });

  it("falls back to the Culture template for null/unknown", () => {
    expect(templateForCategory(null)).toBe(DETAIL_TEMPLATES[DEFAULT_DETAIL_CATEGORY]);
    expect(templateForCategory("not-a-category")).toBe(DETAIL_TEMPLATES[DEFAULT_DETAIL_CATEGORY]);
    expect(DEFAULT_DETAIL_CATEGORY).toBe("culture");
  });
});
