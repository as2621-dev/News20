"use client";

/**
 * ArticleLayer — the full-article layer that rises over the reel when the user
 * taps a story's headline (prototype `blip-reel.js` `renderArticle()`). Renders
 * the layer's INNER content; the sliding `.layer-article` container is owned by
 * {@link BlipReel}.
 *
 * Wired to {@link fetchStoryDetail} (Sub-phase 4d). On mount it fetches the full
 * {@link StoryDetail} payload keyed on `story.digest_id`, then renders:
 *   - `.art-top` — REEL back button + segment chip
 *   - `.art-scroll` — key-stat card, analytics tabs (timeline/market/coverage),
 *     bullets, read-more → long-form body + opposing view
 *   - `.art-bar` — bottom ask bar wired to `onOpenVoice` / `onOpenType`
 *
 * **Null handling (Rule 12).** Every optional field is guarded — no fabricated
 * values. When a field is absent the corresponding UI block is omitted or a
 * neutral placeholder is shown.
 *
 * @example
 * <ArticleLayer story={activeStory} onClose={handleClose}
 *   onOpenType={openTypeSheet} onOpenVoice={openVoiceSheet} />
 */

import { useEffect, useRef, useState } from "react";
import { ic } from "@/components/blip/reel/icons";
// Fixture-backed detail, mirroring the reel's fixture feed: a bare `next dev` has
// no Supabase rows for the fixture digests, so the Supabase-direct fetch hangs on
// "LOADING…". `fixtureStoryDetail` is the drop-in sibling of `fetchStoryDetail`
// (same signature); Phase 1c/3 swaps this back to "@/lib/detail/fetchStoryDetail".
import { fetchStoryDetail } from "@/lib/detail/fixtureStoryDetail";
import { logger } from "@/lib/logger";
import type {
  DetailChunk,
  DetailKeyPoint,
  SecondAnalytic,
  StoryDetail,
  TimelineEvent,
  TrustSummary,
} from "@/types/detail";
import type { Story } from "@/types/feed";

export interface ArticleLayerProps {
  /** The active story whose full article this layer shows. */
  story: Story;
  /** Close the article and return to the reel. */
  onClose: () => void;
  /**
   * Open the type-ask sheet from the article's own ask bar. Swaps the single
   * overlay (article slides down, sheet slides up). Optional so the scaffold
   * article renders without it.
   */
  onOpenType?: () => void;
  /** Open the voice-ask sheet from the article's own ask bar. */
  onOpenVoice?: () => void;
}

/** The three analytics tab identifiers. */
type AnalyticsTabId = "timeline" | "market" | "coverage";

// ---------------------------------------------------------------------------
// Sub-renderers (pure functions; no new files per the task constraint)
// ---------------------------------------------------------------------------

/**
 * Render the HOW-IT-DEVELOPED timeline panel.
 *
 * @param timeline - Ordered timeline events from the story detail.
 * @returns The `.art-panel` element with `.tl` items.
 *
 * @example
 * <TimelinePanel timeline={detail.timeline} />
 */
function TimelinePanel({ timeline }: { timeline: TimelineEvent[] }) {
  if (timeline.length === 0) {
    return (
      <div className="art-panel">
        <p style={{ color: "rgba(255,255,255,0.4)", fontSize: "12px", fontFamily: "monospace" }}>
          No timeline available for this story.
        </p>
      </div>
    );
  }

  return (
    <div className="art-panel">
      <div className="tl">
        {timeline.map((event, idx) => {
          const isLast = idx === timeline.length - 1;
          return (
            <div key={event.timeline_event_index} className={isLast ? "tl-item now" : "tl-item"}>
              <span className="tl-dot" />
              <div className="tl-date">{event.timeline_when_label}</div>
              <div className="tl-text">{event.timeline_what_text}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Render the MARKET IMPACT panel from `second_analytic`.
 * If `second_analytic` is null/absent, shows a neutral "Not available" note.
 *
 * @param secondAnalytic - The segment-skinned analytic, or null.
 * @returns The `.art-panel` element.
 *
 * @example
 * <MarketPanel secondAnalytic={detail.second_analytic} />
 */
function MarketPanel({ secondAnalytic }: { secondAnalytic: SecondAnalytic | null | undefined }) {
  if (!secondAnalytic) {
    return (
      <div className="art-panel">
        <p style={{ color: "rgba(255,255,255,0.4)", fontSize: "12px", fontFamily: "monospace" }}>
          Not available for this story.
        </p>
      </div>
    );
  }

  return (
    <div className="art-panel">
      <div className="mk-head">
        <span className="ac-label">{secondAnalytic.analytic_headline}</span>
        <span className="mk-tag">{secondAnalytic.analytic_tab_label}</span>
      </div>
      <p style={{ color: "rgba(255,255,255,0.6)", fontSize: "13px", marginTop: "8px", lineHeight: 1.5 }}>
        {secondAnalytic.analytic_summary_text}
      </p>
      {secondAnalytic.analytic_rows.length > 0 ? (
        <div style={{ marginTop: "10px" }}>
          {secondAnalytic.analytic_rows.map((row, idx) => (
            <div
              // Reason: no stable unique id on analytic rows; idx is safe here (static list).
              // biome-ignore lint/suspicious/noArrayIndexKey: static analytic rows with no natural key
              key={idx}
              style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px" }}
            >
              <span style={{ color: "rgba(255,255,255,0.55)", fontSize: "11px", fontFamily: "monospace" }}>
                {row.analytic_row_label}
              </span>
              <span style={{ color: "rgba(255,255,255,0.85)", fontSize: "11px", fontFamily: "monospace" }}>
                {row.analytic_row_direction === "up" ? "▲ " : row.analytic_row_direction === "down" ? "▼ " : ""}
                {row.analytic_row_value ?? "—"}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Render the COVERAGE panel from `trust_summary`.
 *
 * @param trustSummary - The per-story trust/coverage summary.
 * @returns The `.art-panel` element with bias breakdown.
 *
 * @example
 * <CoveragePanel trustSummary={detail.trust_summary} />
 */
function CoveragePanel({ trustSummary }: { trustSummary: TrustSummary }) {
  const total =
    trustSummary.coverage_left_count + trustSummary.coverage_center_count + trustSummary.coverage_right_count;

  // Reason: prevent division-by-zero when all counts are 0 (degenerate seed data).
  const leftPct = total > 0 ? Math.round((trustSummary.coverage_left_count / total) * 100) : 0;
  const centerPct = total > 0 ? Math.round((trustSummary.coverage_center_count / total) * 100) : 0;
  const rightPct = total > 0 ? 100 - leftPct - centerPct : 0;

  const blindspotLean = trustSummary.blindspot_lean;

  return (
    <div className="art-panel">
      <div className="cv-head">
        <span className="ac-label">COVERED BY {trustSummary.coverage_outlet_count} OUTLETS</span>
        {blindspotLean !== null ? <span className="cv-tag">BLINDSPOT · {blindspotLean.toUpperCase()}</span> : null}
      </div>
      {/* Bias bar */}
      <div
        style={{
          display: "flex",
          height: "8px",
          borderRadius: "9999px",
          overflow: "hidden",
          background: "rgba(255,255,255,0.06)",
        }}
      >
        <span style={{ width: `${leftPct}%`, background: "#3B82F6" }} />
        <span style={{ width: `${centerPct}%`, background: "#A1A1AA" }} />
        <span style={{ width: `${rightPct}%`, background: "#E8B7BC" }} />
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontFamily: "'JetBrains Mono',monospace",
          fontSize: "10px",
          marginTop: "9px",
        }}
      >
        <span style={{ color: "#3B82F6" }}>LEFT · {trustSummary.coverage_left_count}</span>
        <span style={{ color: "#A1A1AA" }}>CENTER · {trustSummary.coverage_center_count}</span>
        <span style={{ color: "#E8B7BC" }}>RIGHT · {trustSummary.coverage_right_count}</span>
      </div>
    </div>
  );
}

/**
 * Render the bullet list above "Read the full article".
 * Uses `detail_key_points` when present; falls back to the first 5 `detail_chunks`.
 *
 * @param keyPoints - The 5 at-a-glance bullets, or undefined.
 * @param fallbackChunks - Used only when keyPoints is absent.
 * @returns The `.art-bul` element with `.b` bullet rows.
 *
 * @example
 * <Bullets keyPoints={detail.detail_key_points} fallbackChunks={detail.detail_chunks} />
 */
function Bullets({
  keyPoints,
  fallbackChunks,
}: {
  keyPoints: DetailKeyPoint[] | undefined;
  fallbackChunks: DetailChunk[];
}) {
  // Reason: prefer detail_key_points (curated bullets); fall back to first 5 chunks.
  const bulletTexts: string[] =
    keyPoints && keyPoints.length > 0
      ? keyPoints.map((kp) => kp.key_point_text)
      : fallbackChunks.slice(0, 5).map((c) => c.chunk_text);

  if (bulletTexts.length === 0) {
    return null;
  }

  return (
    <div className="art-bul">
      {bulletTexts.map((text, idx) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: bullets have no stable id; static list
        <div key={idx} className="b">
          <span className="mk" />
          <p>{text}</p>
        </div>
      ))}
    </div>
  );
}

/**
 * Render the long-form body (chunks) + opposing-view card.
 *
 * @param chunks - All body paragraphs in chunk_index order.
 * @param opposingViewText - Opposing view quote, or null (omitted when absent).
 * @returns The full long-form block.
 *
 * @example
 * <LongForm chunks={detail.detail_chunks} opposingViewText={detail.trust_summary.opposing_view_text} />
 */
function LongForm({ chunks, opposingViewText }: { chunks: DetailChunk[]; opposingViewText: string | null }) {
  return (
    <>
      <div className="art-rule">FULL ARTICLE</div>
      <div className="art-body">
        {chunks.map((chunk) => (
          <p key={chunk.chunk_index}>{chunk.chunk_text}</p>
        ))}
      </div>
      {opposingViewText !== null ? (
        <div className="oppose">
          <div className="ol">
            {ic("back")}
            THE OPPOSING VIEW
          </div>
          <p>{opposingViewText}</p>
        </div>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

/**
 * Render the article layer for the active story. Returns the layer's INNER
 * content; the sliding `.layer-article` container is owned by {@link BlipReel}.
 *
 * @param story - The active story (uses `digest_id` as the fetch key).
 * @param onClose - Close the article and return to the reel.
 * @param onOpenType - Open the type-ask overlay (optional).
 * @param onOpenVoice - Open the voice-ask overlay (optional).
 *
 * @example
 * <ArticleLayer story={activeStory} onClose={handleClose}
 *   onOpenType={openTypeSheet} onOpenVoice={openVoiceSheet} />
 */
export function ArticleLayer({ story, onClose, onOpenType, onOpenVoice }: ArticleLayerProps) {
  const [detail, setDetail] = useState<StoryDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<AnalyticsTabId>("timeline");
  const [isExpanded, setIsExpanded] = useState(false);

  // Reason: monotonic token prevents a stale in-flight fetch from overwriting
  // the state when the user closes + reopens a different story mid-flight.
  const requestTokenRef = useRef<number>(0);

  useEffect(() => {
    const requestToken = requestTokenRef.current + 1;
    requestTokenRef.current = requestToken;
    setDetail(null);
    setLoadError(null);
    setActiveTab("timeline");
    setIsExpanded(false);

    const storyId = story.digest_id;

    logger.info("article_layer_fetch_started", { story_id: storyId });

    fetchStoryDetail(storyId)
      .then((payload) => {
        if (requestTokenRef.current !== requestToken) {
          // Reason: a newer request was issued; discard this stale response.
          return;
        }
        logger.info("article_layer_fetch_succeeded", {
          story_id: storyId,
          chunk_count: payload.detail_chunks.length,
          timeline_event_count: payload.timeline.length,
        });
        setDetail(payload);
      })
      .catch((error: unknown) => {
        if (requestTokenRef.current !== requestToken) {
          return;
        }
        const errorMessage = error instanceof Error ? error.message : "Unknown error loading story detail.";
        logger.error("article_layer_fetch_failed", {
          story_id: storyId,
          error_message: errorMessage,
          fix_suggestion: "Check Supabase RLS, confirm migrations applied, and verify the story seed ran.",
        });
        setLoadError(errorMessage);
      });
  }, [story.digest_id]);

  // ---------------------------------------------------------------------------
  // Tab definitions — the middle tab label comes from second_analytic when
  // present, otherwise falls back to the static "MARKET IMPACT" label.
  // ---------------------------------------------------------------------------
  const marketTabLabel = detail?.second_analytic?.analytic_tab_label ?? "MARKET IMPACT";

  const TAB_DEFS: { id: AnalyticsTabId; label: string }[] = [
    { id: "timeline", label: "STORY TIMELINE" },
    { id: "market", label: marketTabLabel },
    { id: "coverage", label: "COVERAGE" },
  ];

  return (
    <>
      {/* ------------------------------------------------------------------ */}
      {/* TOP BAR — back button + segment chip                                */}
      {/* ------------------------------------------------------------------ */}
      <div className="art-top">
        <button
          type="button"
          className="v-back"
          aria-label="Back to reel"
          onClick={onClose}
          style={{ background: "transparent", border: "none", padding: 0 }}
        >
          {ic("back")}
          REEL
        </button>
        <span className="seg-chip" style={{ color: story.segment_accent_hex }}>
          <span className="seg-dot" />
          {story.segment_label.toUpperCase()}
        </span>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* SCROLLABLE BODY                                                     */}
      {/* ------------------------------------------------------------------ */}
      <div className="art-scroll">
        {/* Headline always renders from the in-memory story (no blank flash). */}
        <h1 className="art-h1">{story.headline}</h1>
        <div className="art-meta">&lt; 100s READ</div>

        {/* ---- loading / error states ---- */}
        {loadError !== null ? (
          <div style={{ marginTop: "24px" }}>
            <p style={{ color: "rgba(255,255,255,0.45)", fontSize: "13px", lineHeight: 1.5 }}>
              Could not load this story&rsquo;s detail. Tap REEL to go back and try again.
            </p>
          </div>
        ) : detail === null ? (
          <p
            style={{
              marginTop: "24px",
              fontFamily: "monospace",
              fontSize: "12px",
              textTransform: "uppercase",
              letterSpacing: "0.14em",
              color: "rgba(255,255,255,0.3)",
            }}
          >
            Loading…
          </p>
        ) : (
          <>
            {/* ---- KEY-STAT CARD ---------------------------------------- */}
            {/* Omit entirely when key_figure_value is null (Rule 12). */}
            {detail.key_figure.key_figure_value !== null ? (
              <div className="art-stat">
                <div className="big">{detail.key_figure.key_figure_value}</div>
                {detail.key_figure.key_figure_label !== null ? (
                  <div className="cap">{detail.key_figure.key_figure_label}</div>
                ) : null}
              </div>
            ) : null}

            {/* ---- ANALYTICS TABS --------------------------------------- */}
            <div className="art-tabs">
              {TAB_DEFS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  className={`art-tab${activeTab === tab.id ? " on" : ""}`}
                  onClick={() => setActiveTab(tab.id)}
                  style={{ background: "transparent", border: "none", padding: 0 }}
                  aria-pressed={activeTab === tab.id}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* ---- ACTIVE PANEL ----------------------------------------- */}
            {activeTab === "timeline" ? (
              <TimelinePanel timeline={detail.timeline} />
            ) : activeTab === "market" ? (
              <MarketPanel secondAnalytic={detail.second_analytic} />
            ) : (
              <CoveragePanel trustSummary={detail.trust_summary} />
            )}

            {/* ---- BULLETS ---------------------------------------------- */}
            <Bullets keyPoints={detail.detail_key_points} fallbackChunks={detail.detail_chunks} />

            {/* ---- READ-MORE BUTTON ------------------------------------- */}
            {!isExpanded ? (
              <button
                type="button"
                className="read-more"
                onClick={() => setIsExpanded(true)}
                style={{ background: "transparent", border: "none", padding: 0, width: "100%" }}
                aria-expanded={false}
              >
                <div>
                  <div className="rm-t">Read the full article</div>
                  <div className="rm-s">LONG-FORM · OPPOSING VIEW</div>
                </div>
                {ic("arrow")}
              </button>
            ) : null}

            {/* ---- LONG-FORM BODY (revealed when expanded) -------------- */}
            {isExpanded ? (
              <LongForm chunks={detail.detail_chunks} opposingViewText={detail.trust_summary.opposing_view_text} />
            ) : null}
          </>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* BOTTOM ASK BAR                                                     */}
      {/* ------------------------------------------------------------------ */}
      <div className="art-bar">
        <div className="r3-bottom">
          <div className="r3-row">
            <button
              type="button"
              className="sig-btn"
              aria-label="Ask by voice"
              onClick={() => onOpenVoice?.()}
              style={{ background: "transparent", border: "none", padding: 0 }}
            >
              <span className="ring" />
              <span className="ring r2" />
              {ic("voice")}
            </button>
            <button
              type="button"
              className="qfield field"
              aria-label="Type a question"
              onClick={() => onOpenType?.()}
              style={{ background: "transparent", border: "none", padding: 0, textAlign: "left" }}
            >
              <span className="q">Ask anything about this story…</span>
              <span className="kbd">{ic("keyboard")}</span>
            </button>
          </div>
          <div className="r3-hint">
            <span className="dot" />
            PRESS TO TALK · OR TYPE YOUR OWN
          </div>
        </div>
      </div>
    </>
  );
}
