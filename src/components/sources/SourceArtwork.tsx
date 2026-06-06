"use client";

/**
 * SourceArtwork — the universal source/personality avatar (Phase 5c SP2).
 *
 * Renders an `<img>` of the source thumbnail when one is supplied, and falls back
 * to a stable initials-on-gradient tile when the URL is absent OR the image 404s /
 * fails to load (`onError`). `kind` drives the shape: a PERSON axis
 * (`x_account`/`personality`) is a CIRCLE, a `youtube_channel`/`podcast` is a
 * rounded SQUARE — read straight off `SOURCE_TYPE_CONFIGS[kind].tile_shape` so the
 * mapping stays single-sourced with the type layer (no duplicated shape logic).
 *
 * A plain `<img>` (not `next/image`) is used on purpose: thumbnails come from
 * arbitrary external CDNs (i.ytimg.com, mzstatic.com, X/pbs.twimg.com) that aren't
 * whitelisted in `next.config.*`, and `referrerPolicy="no-referrer"` keeps those
 * CDNs from rejecting the hotlink.
 *
 * **Re-skin (CLAUDE.md / reuse-map §5):** ported in structure only from TL;DW's
 * `src/components/shared/source-artwork.tsx`. Its amber `var(--*)` tokens and serif
 * font var are dropped; colors/typography here are the News20 dark-editorial tokens
 * from `reference/design-language.md` (border `#D1D4BD`, text `#FFFFFF`, JetBrains
 * Mono for the initials). The gradient comes from {@link portraitGradient}, also
 * palette-constrained.
 *
 * SP3 (search modal) reuses this to render search-result avatars, so the Props are
 * kept general — it is NOT hardcoded to the rec-grid use case.
 */

import { type CSSProperties, useState } from "react";
import { initials, portraitGradient } from "@/lib/portraitBg";
import type { ContentSourceType } from "@/types/source";
import { SOURCE_TYPE_CONFIGS } from "@/types/source";

/** Design-language.md tokens used by the avatar, inlined (no globals.css edit — SP2 scope). */
const TOKENS = {
  border: "#D1D4BD", // border / sage line
  card: "#020617", // near-black canvas behind a loaded thumbnail
  initialsInk: "#FFFFFF", // text-primary, on the gradient tile
} as const;

/** JetBrains Mono stack (design-language.md label face) for the fallback initials. */
const MONO_FONT_STACK = '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace';

/** Default tile edge in px when the caller doesn't size it (grid-tile default). */
const DEFAULT_SIZE_PX = 56;

/** Rounded-square corner radius in px for channel/podcast tiles (design-language `radius: control`). */
const SQUARE_RADIUS_PX = 16;

export interface SourceArtworkProps {
  /** Display name — used for the `alt`, the fallback initials, and the gradient seed. */
  source_name: string;
  /** Thumbnail URL. Absent/null or a broken load → the initials-gradient fallback. */
  image_url?: string | null;
  /** The source axis — drives circle (person) vs rounded-square (channel/podcast). */
  kind: ContentSourceType;
  /** Tile edge in px (square bounding box). Defaults to {@link DEFAULT_SIZE_PX}. */
  size?: number;
}

/**
 * Render a source/personality avatar with a broken-image fallback.
 *
 * @param props - {@link SourceArtworkProps}.
 *
 * @example
 * <SourceArtwork source_name="Lex Fridman" image_url={src.thumbnail_url} kind="personality" size={64} />
 */
export function SourceArtwork({ source_name, image_url, kind, size = DEFAULT_SIZE_PX }: SourceArtworkProps) {
  // Reason: once an image errors we never re-attempt it — flipping this to true
  // swaps to the fallback AND removes the <img> from the tree, so onError can't
  // re-fire in a loop (the donor relied on the same single-shot flag).
  const [hasLoadError, setHasLoadError] = useState(false);

  const isCircle = SOURCE_TYPE_CONFIGS[kind].tile_shape === "circle";
  const radiusPx = isCircle ? size / 2 : SQUARE_RADIUS_PX;
  const shouldShowImage = Boolean(image_url) && !hasLoadError;

  const baseStyle: CSSProperties = {
    width: size,
    height: size,
    borderRadius: radiusPx,
    border: `1px solid ${TOKENS.border}`,
    overflow: "hidden",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  };

  if (shouldShowImage && image_url) {
    return (
      <div data-source-artwork="" style={{ ...baseStyle, background: TOKENS.card }}>
        {/* biome-ignore lint/performance/noImgElement: external CDN thumbnails aren't whitelisted in next.config — a plain <img> with no-referrer is intentional (reuse-map §5). */}
        <img
          src={image_url}
          alt={source_name}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setHasLoadError(true)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </div>
    );
  }

  return (
    <div
      data-source-artwork=""
      data-source-artwork-fallback=""
      role="img"
      aria-label={source_name}
      style={{
        ...baseStyle,
        background: portraitGradient(source_name),
        fontFamily: MONO_FONT_STACK,
        // Scale initials to ~⅓ of the tile so they read at 44px rows and 64px grid tiles.
        fontSize: Math.max(12, Math.round(size * 0.34)),
        fontWeight: 600,
        color: TOKENS.initialsInk,
      }}
    >
      {initials(source_name)}
    </div>
  );
}
