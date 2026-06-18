/**
 * Per-category Detail-page panel templates (the frontend source of truth).
 *
 * Byte-for-byte twin of the backend `agents/pipeline/detail_templates.py`
 * (Rule 7: the two must never drift — `detailTemplates.test.ts` asserts parity).
 * The story Detail page (`ArticleLayer`) reads `DETAIL_TEMPLATES[detailCategory]`
 * to decide which ordered triple of panels to render; only the resolved
 * `story_detail_category` key travels over the wire, never the template itself.
 *
 * A template is up to three {@link PanelSpec}. A panel is one of:
 *  - `timeline` — the "HOW IT DEVELOPED" events (`story_timeline`)
 *  - `coverage` — the trust/coverage strip (`story_trust`), framed by `coverage_mode`
 *  - `analytic` — one analytic panel (`story_analytics` row), matched by `analytic_slot_index`
 *
 * Source categories (youtube/podcasts/x) carry NO timeline and NO coverage —
 * their stories are single-creator content, not multi-outlet news.
 *
 * Static-export safe: pure data + pure helpers, no `window`/server APIs at module scope.
 */

import type { AnalyticKind, CoverageMode } from "@/types/detail";

/**
 * The eight Detail-page buckets a story can fall into (phase-SP1 removed
 * `breaking`). Aligned to the 8 design buckets in `src/lib/feedBuckets.ts`
 * (`DesignBucketId`), distinct from the 5-valued `SegmentKey`.
 */
export type DetailCategory =
  | "world"
  | "markets"
  | "tech"
  | "sport"
  | "culture"
  | "youtube"
  | "podcasts"
  | "x";

/** The kind of panel a slot renders. */
export type PanelKind = "timeline" | "coverage" | "analytic";

/**
 * One panel in a category's Detail template. Exactly one kind-specific field set:
 *  - `panel_kind === "timeline"` → no extra fields.
 *  - `panel_kind === "coverage"` → `coverage_mode` set.
 *  - `panel_kind === "analytic"` → `analytic_kind` + `analytic_tab_label` set.
 */
export interface PanelSpec {
  /** Which renderer this slot uses. */
  readonly panel_kind: PanelKind;
  /** The analytic kind to render (analytic panels only). */
  readonly analytic_kind?: AnalyticKind;
  /** The fixed tab label (analytic panels only). */
  readonly analytic_tab_label?: string;
  /** How the Coverage strip is framed (coverage panels only). */
  readonly coverage_mode?: CoverageMode;
}

/** Build a timeline panel spec. */
function timeline(): PanelSpec {
  return { panel_kind: "timeline" };
}

/** Build a coverage panel spec with the given framing. */
function coverage(mode: CoverageMode): PanelSpec {
  return { panel_kind: "coverage", coverage_mode: mode };
}

/** Build an analytic panel spec with its kind + fixed tab label. */
function analytic(kind: AnalyticKind, label: string): PanelSpec {
  return { panel_kind: "analytic", analytic_kind: kind, analytic_tab_label: label };
}

/**
 * The ordered triple of panels each detail category renders. THE source of truth.
 * Must equal `agents/pipeline/detail_templates.py` `DETAIL_TEMPLATES`.
 */
export const DETAIL_TEMPLATES: Readonly<Record<DetailCategory, readonly PanelSpec[]>> = {
  world: [timeline(), analytic("stakes", "STAKES"), coverage("partisan")],
  markets: [timeline(), analytic("market_impact", "MARKET IMPACT"), analytic("by_the_numbers", "BY THE NUMBERS")],
  tech: [timeline(), analytic("why_it_matters", "WHY IT MATTERS"), analytic("the_concept", "THE CONCEPT")],
  sport: [timeline(), analytic("stat_line", "STAT LINE"), analytic("recent_form", "RECENT FORM")],
  culture: [timeline(), analytic("subject_profile", "PROFILE"), analytic("why_it_matters", "WHY IT MATTERS")],
  youtube: [
    analytic("source_context", "THE VIDEO"),
    analytic("key_points", "KEY POINTS"),
    analytic("implications", "IMPLICATIONS"),
  ],
  podcasts: [
    analytic("source_context", "THE EPISODE"),
    analytic("key_points", "KEY POINTS"),
    analytic("implications", "IMPLICATIONS"),
  ],
  x: [
    analytic("source_context", "THE GIST"),
    analytic("key_points", "KEY POINTS"),
    analytic("implications", "IMPLICATIONS"),
  ],
};

/** Best-fit fallback when a story's detail category is unknown/missing. */
export const DEFAULT_DETAIL_CATEGORY: DetailCategory = "culture";

/**
 * Resolve a story's template defensively — falls back to the Culture template
 * when `detailCategory` is null/unknown (a pre-migration story with no
 * `story_detail_category`), so the Detail page always has three slots to draw.
 *
 * @param detailCategory - The story's `story_detail_category`, possibly null.
 * @returns The ordered panel specs to render.
 */
export function templateForCategory(detailCategory: string | null | undefined): readonly PanelSpec[] {
  if (detailCategory && detailCategory in DETAIL_TEMPLATES) {
    return DETAIL_TEMPLATES[detailCategory as DetailCategory];
  }
  return DETAIL_TEMPLATES[DEFAULT_DETAIL_CATEGORY];
}
