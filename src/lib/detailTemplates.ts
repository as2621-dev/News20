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
 * The Detail-page TEMPLATE buckets a story can fall into (phase-SP1 removed
 * `breaking`). These are the panel-LAYOUT keys, NOT the SP3 picker taxonomy: each
 * distinct panel layout keeps one canonical key (`world` carries the partisan-
 * coverage layout; `markets` the market-impact layout; `culture` the long-tail
 * profile layout). The SP3 taxonomy roots (`ai, geopolitics, business, environment,
 * politics, tech, sport, arts`) resolve onto these via {@link DETAIL_CATEGORY_ALIASES}
 * so a `geopolitics` or `business` story renders a meaningful template rather than the
 * default. `podcasts` rides the `youtube` source layout (it has no SP3 axis of its
 * own — see `feedBuckets.ts` `SOURCE_TYPE_TO_DESIGN_BUCKET`).
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

/**
 * SP3 taxonomy root → Detail-page TEMPLATE key. The SP3 picker roots
 * (`src/lib/feedBuckets.ts` `DesignBucketId`) do not each own a bespoke panel
 * layout; they fold onto the closest existing template so a `geopolitics` /
 * `business` / `ai` story renders a meaningful Detail page instead of the
 * Culture default. Byte-for-byte twin of `agents/pipeline/detail_templates.py`
 * `DETAIL_CATEGORY_ALIASES` (Rule 7).
 *
 * Fold (new root → borrowed template, documented per the locked palette/segment
 * map in `src/lib/feed/fixtureFeed.ts` `SEGMENT_DETAIL_CATEGORY`):
 *  - `geopolitics` → `world`   (the split `world_politics` keeps the partisan-coverage layout)
 *  - `politics`    → `world`   (no bespoke layout; nearest is the partisan/world layout)
 *  - `environment` → `world`   (no bespoke layout; nearest is the world/stakes layout)
 *  - `business`    → `markets` (the old `markets` fold collapses into business)
 *  - `arts`        → `culture` (the old `culture` catch-all renames to arts)
 *  - `ai`          → `tech`    (no bespoke layout; rides the tech why-it-matters/concept layout)
 *  - `tech`/`sport`/`youtube`/`x` are already canonical template keys (identity, omitted).
 */
export const DETAIL_CATEGORY_ALIASES: Readonly<Record<string, DetailCategory>> = {
  geopolitics: "world",
  politics: "world",
  environment: "world",
  business: "markets",
  arts: "culture",
  ai: "tech",
};

/** Best-fit fallback when a story's detail category is unknown/missing. */
export const DEFAULT_DETAIL_CATEGORY: DetailCategory = "culture";

/**
 * Resolve a story's template defensively. Accepts EITHER a canonical template key
 * (`world`/`markets`/...) OR an SP3 taxonomy root (`geopolitics`/`business`/...),
 * folding the latter onto its template via {@link DETAIL_CATEGORY_ALIASES}. Falls
 * back to the Culture template when `detailCategory` is null/unknown (a pre-migration
 * story with no `story_detail_category`), so the Detail page always has three slots.
 *
 * @param detailCategory - The story's `story_detail_category`, possibly null.
 * @returns The ordered panel specs to render.
 */
export function templateForCategory(detailCategory: string | null | undefined): readonly PanelSpec[] {
  if (detailCategory) {
    const aliased = DETAIL_CATEGORY_ALIASES[detailCategory];
    if (aliased) {
      return DETAIL_TEMPLATES[aliased];
    }
    if (detailCategory in DETAIL_TEMPLATES) {
      return DETAIL_TEMPLATES[detailCategory as DetailCategory];
    }
  }
  return DETAIL_TEMPLATES[DEFAULT_DETAIL_CATEGORY];
}
