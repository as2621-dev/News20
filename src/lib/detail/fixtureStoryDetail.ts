/**
 * Fixture Story Detail provider — the drop-in sibling of `fetchStoryDetail.ts`
 * (Supabase-direct) for the local fixture feed.
 *
 * **The seam (mirrors the feed).** Just as `fixtureFeed.ts` ⇄ `supabaseFeed.ts`
 * both export `getFeed(): Promise<Story[]>`, this file ⇄ `fetchStoryDetail.ts`
 * both export `fetchStoryDetail(story_id): Promise<StoryDetail>`. The reel's
 * {@link import("@/components/blip/reel/ArticleLayer").ArticleLayer} imports the
 * fixture provider so a bare `next dev` renders the article end-to-end — the reel
 * feed is fixture-backed, and the fixture digests have no Supabase detail rows, so
 * the Supabase-direct fetch would otherwise hang on "LOADING…". Phase 1c/3 swaps
 * the import back to `fetchStoryDetail.ts` (Supabase) with zero ArticleLayer
 * changes — same as the feed swap.
 *
 * **Provenance (no fabrication).** Content is transcribed verbatim from the
 * prototype `prototype/News20 Prototype/data.js` `STORIES[]` detail fields
 * (`detail_chunks` / `keyFigure` / `trust.{coverage,outlet_count,blindspot,
 * timeline,opposing_view}` / `suggested_questions` / `citations`) — the same
 * source `fixtureFeed.ts` used for headlines. Positional mapping: prototype
 * `s{N}` ↔ feed `digest-{N}`.
 *
 * **Phase-2c fields absent.** The prototype predates `detail_key_points` and
 * `second_analytic`, so they are left absent / `null` rather than invented.
 * ArticleLayer degrades cleanly: bullets fall back to the first chunks, and the
 * second-analytic ("MARKET IMPACT") tab shows "Not available for this story."
 */

import { logger } from "@/lib/logger";
import type { BiasLean, StoryDetail } from "@/types/detail";

/**
 * Compact transcription of one prototype story's detail fields. Mapped to the
 * canonical {@link StoryDetail} by {@link buildFixtureStoryDetail}.
 */
interface FixtureDetailSource {
  /** Feed `digest_id` (`"digest-1"`..`"digest-5"`); the ArticleLayer fetch key. */
  digest_id: string;
  /** Long-form body paragraphs, in reading order (`data.js` `detail_chunks`). */
  chunks: string[];
  /** Key-stat card value, or `null` (`data.js` `keyFigure.value`). */
  key_figure_value: string | null;
  /** Key-stat card label, or `null` (`data.js` `keyFigure.label`). */
  key_figure_label: string | null;
  /** Outlets covering this story leaning left (`trust.coverage.left`). */
  coverage_left: number;
  /** Outlets leaning center (`trust.coverage.center`). */
  coverage_center: number;
  /** Outlets leaning right (`trust.coverage.right`). */
  coverage_right: number;
  /** Total outlets ("COVERED BY N OUTLETS"; `trust.outlet_count`). */
  coverage_outlet_count: number;
  /** Materially under-covered lean, or `null` when balanced (`trust.blindspot`). */
  blindspot_lean: BiasLean | null;
  /** Opposing-view card quote, or `null` (`trust.opposing_view`). */
  opposing_view_text: string | null;
  /** "HOW IT DEVELOPED" events, in order (`trust.timeline`). */
  timeline: { when_label: string; what_text: string }[];
  /** Tappable suggested-question chips, in order (`data.js` `suggested_questions`). */
  suggested_questions: string[];
  /** Citation outlet names (`data.js` `citations`). */
  citation_outlets: string[];
}

/** The 5 prototype stories' detail content, positional `s{N}` ↔ `digest-{N}`. */
const FIXTURE_DETAIL_SOURCES: readonly FixtureDetailSource[] = [
  {
    digest_id: "digest-1",
    chunks: [
      "The United States struck a second target inside Iran overnight, a site Washington says threatened American forces and commercial shipping in the Gulf. It is the latest escalation in a confrontation that has flared for weeks.",
      "President Trump told reporters he is confident a deal to end the fighting is close — but made clear he is not satisfied with the current terms, and is willing to restart strikes if Tehran does not meet U.S. demands.",
      "Meanwhile Iran is pushing back. It issued new rules for any vessel passing through the Strait of Hormuz, the narrow chokepoint where roughly a fifth of the world’s oil moves — an attempt to formalize control in defiance of U.S. warnings.",
    ],
    key_figure_value: "~20%",
    key_figure_label: "of global oil transits Hormuz",
    coverage_left: 9,
    coverage_center: 7,
    coverage_right: 3,
    coverage_outlet_count: 19,
    blindspot_lean: "right",
    opposing_view_text:
      "Some regional analysts argue the strikes harden Tehran’s position and make a negotiated deal less likely, not more.",
    timeline: [
      { when_label: "08:10", what_text: "U.S. officials confirm an overnight strike inside Iran." },
      { when_label: "10:25", what_text: "Trump: a deal is “close,” but he won’t rush it." },
      { when_label: "13:40", what_text: "Iran issues new transit rules for the Strait of Hormuz." },
    ],
    suggested_questions: ["What led to this?", "Why does Hormuz matter?", "Who’s affected?"],
    citation_outlets: ["CNN", "Reuters"],
  },
  {
    digest_id: "digest-2",
    chunks: [
      "Travis Kelce, the Kansas City Chiefs tight end, has purchased a minority stake in the Cleveland Guardians — the Major League Baseball team he grew up watching in his hometown of Cleveland Heights.",
      "It’s a homecoming story. As a kid, Kelce rode the light rail downtown with his dad to catch games; before football, he was one of the best baseball players in the Cleveland area.",
      "He joins a growing club of active stars taking ownership positions — LeBron James with the Red Sox, Giannis Antetokounmpo with the Brewers, and his own teammate Patrick Mahomes with the Royals. The size of Kelce’s stake hasn’t been disclosed.",
    ],
    key_figure_value: "undisclosed",
    key_figure_label: "size of Kelce’s stake",
    coverage_left: 4,
    coverage_center: 11,
    coverage_right: 6,
    coverage_outlet_count: 21,
    blindspot_lean: null,
    opposing_view_text:
      "Critics question whether celebrity minority stakes mean real influence, or are mostly a branding and access play.",
    timeline: [
      { when_label: "Mon", what_text: "Guardians confirm a new minority investor group." },
      { when_label: "Tue", what_text: "Reports identify Kelce among the investors." },
      { when_label: "Wed", what_text: "Kelce’s camp confirms; stake size withheld." },
    ],
    suggested_questions: ["How big is the stake?", "Which other athletes own teams?", "Why the Guardians?"],
    citation_outlets: ["ESPN", "AP"],
  },
  {
    digest_id: "digest-3",
    chunks: [
      "Physicists at the University of Houston have broken a superconductivity record that stood for more than thirty years. Superconductors carry electricity with zero resistance — no energy lost at all.",
      "The long-standing catch is temperature: you needed extreme cold to make it work. The old record, set in 1993, was 133 Kelvin. The Houston team pushed that to 151 Kelvin — the highest ever achieved at normal, everyday pressure.",
      "Their method, called “pressure quenching,” squeezes the material and then locks in the new properties after the pressure is removed. It’s still about −122°C — but every degree closer to room temperature matters for lossless grids, faster electronics, fusion and medical scanners.",
    ],
    key_figure_value: "151 K",
    key_figure_label: "new record (was 133 K, 1993)",
    coverage_left: 5,
    coverage_center: 14,
    coverage_right: 2,
    coverage_outlet_count: 16,
    blindspot_lean: "right",
    opposing_view_text:
      "Independent labs caution that the result needs replication before it reshapes the field — extraordinary claims need repeat measurement.",
    timeline: [
      { when_label: "1993", what_text: "Previous record set at 133 Kelvin." },
      { when_label: "May", what_text: "University of Houston reports 151 K at ambient pressure." },
      { when_label: "Now", what_text: "Result submitted for peer review and replication." },
    ],
    suggested_questions: ["What is superconductivity?", "Why does pressure matter?", "When is this useful?"],
    citation_outlets: ["ScienceDaily", "Nature"],
  },
  {
    digest_id: "digest-4",
    chunks: [
      "Nvidia reported quarterly results that show no sign of the AI boom cooling. Revenue came in at $81.6 billion, ahead of the roughly $79 billion Wall Street expected.",
      "The engine is the data-center business, where revenue nearly doubled from a year ago — the AI gold rush captured in a single number. Profit beat too, at $1.87 a share against forecasts of $1.78, and the company guided to $91 billion next quarter.",
      "Nvidia also rewarded shareholders with an $80 billion buyback and a dividend hiked from a penny to 25 cents. And yet the stock slipped afterward — when you’re priced for perfection, even a blowout isn’t always good enough.",
    ],
    key_figure_value: "$81.6B",
    key_figure_label: "quarterly revenue (est. ~$79B)",
    coverage_left: 6,
    coverage_center: 13,
    coverage_right: 9,
    coverage_outlet_count: 24,
    blindspot_lean: null,
    opposing_view_text:
      "Bears argue the data-center surge is a one-off AI capex spike that won’t sustain the company’s valuation.",
    timeline: [
      { when_label: "16:05", what_text: "Nvidia releases Q1 results after the bell." },
      { when_label: "16:20", what_text: "Guidance of $91B next quarter beats estimates." },
      { when_label: "16:45", what_text: "Shares slip in after-hours trading." },
    ],
    suggested_questions: ["Why did the stock slip?", "How big is data center?", "What’s the guidance?"],
    citation_outlets: ["CNBC", "Kiplinger"],
  },
  {
    digest_id: "digest-5",
    chunks: [
      "Pope Leo XIV has issued one of his strongest warnings yet about artificial intelligence, urging world leaders to slow the race to deploy it and to agree on international safeguards.",
      "His concern is that unchecked AI could deepen misinformation, destabilize societies, and push autonomous weapons past meaningful human control — the last point being the line many quietly fear most.",
      "It’s a striking moment: a moral authority stepping into a debate usually led by engineers and CEOs. It lands the same week a major report said AI will soon be the single biggest force shaping global cybersecurity. Two very different voices, one message — slow down.",
    ],
    key_figure_value: "3 risks",
    key_figure_label: "misinformation · instability · autonomous weapons",
    coverage_left: 8,
    coverage_center: 6,
    coverage_right: 4,
    coverage_outlet_count: 14,
    blindspot_lean: "right",
    opposing_view_text:
      "Some technologists argue a deployment slowdown cedes ground to less cautious actors and delays beneficial uses of AI.",
    timeline: [
      { when_label: "Mon", what_text: "Vatican publishes the Pope’s remarks on AI." },
      { when_label: "Tue", what_text: "Tech and policy figures respond." },
      { when_label: "Wed", what_text: "Cybersecurity report names AI the top emerging threat." },
    ],
    suggested_questions: ["What did the Pope say?", "What risks are named?", "Who else is warning?"],
    citation_outlets: ["TechStartups", "Vatican News"],
  },
] as const;

/**
 * Map one {@link FixtureDetailSource} to a fully-populated {@link StoryDetail},
 * deriving the index fields (`chunk_index`, `timeline_event_index`,
 * `question_index`) from array position.
 *
 * @param source - The transcribed prototype detail for one story.
 * @returns The canonical Detail payload ArticleLayer renders.
 */
function buildFixtureStoryDetail(source: FixtureDetailSource): StoryDetail {
  return {
    story_id: source.digest_id,
    // The prototype fixtures predate per-category detail templates; null lets the
    // UI fall back to the Culture template (and the analytic panels stay empty).
    detail_category: null,
    detail_chunks: source.chunks.map((chunk_text, chunk_index) => ({ chunk_index, chunk_text })),
    trust_summary: {
      coverage_left_count: source.coverage_left,
      coverage_center_count: source.coverage_center,
      coverage_right_count: source.coverage_right,
      coverage_outlet_count: source.coverage_outlet_count,
      blindspot_lean: source.blindspot_lean,
      opposing_view_text: source.opposing_view_text,
      coverage_mode: "partisan",
    },
    key_figure: {
      key_figure_value: source.key_figure_value,
      key_figure_label: source.key_figure_label,
    },
    sources: source.citation_outlets.map((source_outlet_name) => ({
      source_outlet_name,
      source_bias_lean: null,
      source_article_url: null,
      source_published_utc: null,
      source_is_citation: true,
    })),
    timeline: source.timeline.map((event, timeline_event_index) => ({
      timeline_event_index,
      timeline_when_label: event.when_label,
      timeline_what_text: event.what_text,
    })),
    suggested_questions: source.suggested_questions.map((question_text, question_index) => ({
      question_index,
      question_text,
    })),
    // Phase-2c fields the prototype predates — absent, not fabricated (see file docstring).
    analytic_panels: [],
  };
}

/**
 * Fixture story-detail fetch — the drop-in replacement for the Supabase-direct
 * {@link import("@/lib/detail/fetchStoryDetail").fetchStoryDetail}.
 *
 * Resolves the prototype-transcribed Detail for one fixture story, building a
 * fresh payload per call. Throws (rather than hanging) when the id has no fixture
 * — mirroring the Supabase fetch's throw-on-missing contract so ArticleLayer's
 * error branch renders instead of an indefinite "LOADING…".
 *
 * @param story_id - The reel's `activeStory.digest_id` (`"digest-1"`..`"digest-5"`).
 * @returns The fully populated Detail payload for the story.
 * @throws If `story_id` has no matching fixture (feed/detail digest-id drift).
 *
 * @example
 * const detail = await fetchStoryDetail("digest-1");
 * detail.detail_chunks[0].chunk_text;          // first body paragraph
 * detail.trust_summary.coverage_outlet_count;  // 19
 * detail.key_figure.key_figure_value;          // "~20%"
 */
export async function fetchStoryDetail(story_id: string): Promise<StoryDetail> {
  logger.info("fetch_story_detail_started", { story_id, detail_source: "fixtures" });

  const source = FIXTURE_DETAIL_SOURCES.find((candidate) => candidate.digest_id === story_id);
  if (source === undefined) {
    logger.error("fetch_story_detail_failed", {
      story_id,
      detail_source: "fixtures",
      error_message: `No fixture story detail for "${story_id}".`,
      fix_suggestion: "Confirm the reel feed and detail fixtures share digest ids (digest-1..digest-5).",
    });
    throw new Error(`No fixture story detail for "${story_id}".`);
  }

  const detail = buildFixtureStoryDetail(source);
  logger.info("fetch_story_detail_completed", {
    story_id,
    detail_source: "fixtures",
    chunk_count: detail.detail_chunks.length,
    timeline_event_count: detail.timeline.length,
  });
  return detail;
}
