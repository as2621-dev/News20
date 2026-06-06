"use client";

/**
 * SourceSwipeCard — a single source card in the swipe deck (Phase 5c SP-UI).
 *
 * Ported from the Claude Design "blip" handoff — Source Swipe (`blip-sources.js`
 * `cardEl` + `blip-flow.css` `.card`). Purely PRESENTATIONAL: it renders one
 * {@link SourceSwipeCardModel} (logo header + accent gradient, platform glyph,
 * % match badge, Playfair name, mono meta, coverage tags, why-box, and the
 * FOLLOW/SKIP stamps). All gesture/commit state lives in the parent
 * {@link "@/components/sources/SourceSwipe"} — this component owns NO swipe logic,
 * only the broken-thumbnail fallback (single-shot, like {@link SourceArtwork}).
 *
 * The logo treatment differs from `SourceCard`/`SourceArtwork` (a tall gradient
 * header with big mono initials, not a small avatar tile), so it does NOT reuse
 * `SourceArtwork` — but it DOES reuse {@link portraitGradient}/{@link initials}
 * for the missing/404 fallback so a broken thumbnail degrades to the same stable
 * initials-gradient the rest of the source UI uses.
 *
 * The prototype's reviewer-only logo upload/drag-drop affordance (`thumbInput`,
 * `drophint`, localStorage thumbs) is intentionally OMITTED — production uses real
 * catalog thumbnails with the initials-gradient as the fallback.
 */

import { type CSSProperties, useState } from "react";
import { initials, portraitGradient } from "@/lib/portraitBg";
import type { SourceSwipeCardModel, SourceSwipePlatform } from "@/lib/sourceSwipeData";

export interface SourceSwipeCardProps {
  /** The card view-model to render. */
  card: SourceSwipeCardModel;
  /** The platform pass this card belongs to (glyph + follower unit + people-vs-source copy). */
  platform: SourceSwipePlatform;
  /** Layer in the stack: 0 = lead (interactive), 1/2 = behind cards (scaled back). */
  layer: 0 | 1 | 2;
}

/** Stack-position transform for a behind-card (lead has none). Matches the design's `.behind1/.behind2`. */
const BEHIND_TRANSFORM: Record<number, string> = {
  1: "scale(0.945) translateY(15px)",
  2: "scale(0.89) translateY(30px)",
};

/**
 * Render a single deck card. The lead card (`layer === 0`) is `data-source-swipe-card="lead"`
 * so the parent can bind pointer gestures to exactly it.
 *
 * @param props - {@link SourceSwipeCardProps}.
 */
export function SourceSwipeCard({ card, platform, layer }: SourceSwipeCardProps) {
  // Single-shot: once a thumbnail 404s we drop the <img> and show the gradient
  // fallback (mirrors SourceArtwork — onError can't loop once the img is gone).
  const [hasLoadError, setHasLoadError] = useState(false);
  const showImage = Boolean(card.thumbnail_url) && !hasLoadError;

  const cardClass = layer === 0 ? "card lead" : `card behind${layer}`;
  const cardStyle: CSSProperties = layer === 0 ? {} : { transform: BEHIND_TRANSFORM[layer] };

  // The logo header gradient when a thumbnail loads: accent → near-black (design's
  // `linear-gradient(150deg, accent, #0b1120 78%)`). On the FALLBACK path (no/404
  // thumbnail) we swap to the stable per-name portraitGradient so the missing-art
  // tile reads as part of the same source-UI system (reuse-map §5) and never
  // flickers its color between renders; the mono initials stay solid white (design).
  const accentHeaderGradient = `linear-gradient(150deg, ${card.accent_color}, #0b1120 78%)`;
  const logoBackground = showImage ? accentHeaderGradient : portraitGradient(card.source_name);
  const whyLabel = platform.key === "people" ? "What we'll track" : "Why we picked this";

  return (
    <div data-source-swipe-card={layer === 0 ? "lead" : `behind${layer}`} className={cardClass} style={cardStyle}>
      <div className="logo" style={{ background: logoBackground }}>
        {showImage && card.thumbnail_url ? (
          // biome-ignore lint/performance/noImgElement: external CDN thumbnails aren't whitelisted in next.config — a plain <img> with no-referrer is intentional (mirrors SourceArtwork, reuse-map §5).
          <img
            className="logo-img"
            src={card.thumbnail_url}
            alt={card.source_name}
            loading="lazy"
            decoding="async"
            referrerPolicy="no-referrer"
            onError={() => setHasLoadError(true)}
          />
        ) : (
          // Fallback: solid-white mono initials over the stable portraitGradient header.
          <div className="mono">{initials(card.source_name)}</div>
        )}
        <div className="pl">
          <svg aria-hidden="true">
            <use href={`#${platform.glyph}`} />
          </svg>
        </div>
        <div className="matchbadge">
          <span className="num" style={{ color: card.accent_color }}>
            {card.match_pct}
          </span>
          <span className="lab">% match</span>
        </div>
      </div>

      <div className="body">
        <div className="nm">{card.source_name}</div>
        <div className="meta">
          {card.follower_label ? <span className="followers">{card.follower_label}</span> : null}
          {card.follower_label ? <span className="dot" /> : null}
          {platform.unit}
        </div>
        {card.coverage_tags.length > 0 ? (
          <div className="covers">
            {card.coverage_tags.map((tag) => (
              <span key={tag} className="ctag">
                {tag}
              </span>
            ))}
          </div>
        ) : null}
        <div className="why">
          <div className="wl">
            <span className="d" style={{ background: card.accent_color }} />
            {whyLabel}
          </div>
          <p>{card.why_text}</p>
        </div>
      </div>

      <div className="stamp follow">FOLLOW</div>
      <div className="stamp skip">SKIP</div>
    </div>
  );
}
