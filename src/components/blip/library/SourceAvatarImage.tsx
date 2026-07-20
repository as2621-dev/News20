"use client";

/**
 * SourceAvatarImage — the inner content of a library `.av` tile (the followed-source
 * list in `SourcesScreen` and the search results in `SourcesAddControls`).
 *
 * Founder decision 2026-07-05 (#21 no-avatar rescope): names/handles are the
 * canonical, honest render. A source must ALWAYS resolve to a legible name-derived
 * initial and must never depend on — or get stuck showing a broken/empty tile from —
 * a remote avatar load. A catalog `thumbnail_url` is kept only as a PROGRESSIVE
 * NICETY: it renders when present AND loads, but a null/blank URL OR an `onError`
 * (404 / hotlink reject from an external CDN such as i.ytimg.com) degrades to the
 * initial — the same single-shot fallback as {@link "@/components/sources/SourceArtwork"},
 * so these two library surfaces match the rest of the source UI.
 *
 * No avatar is ever FETCHED here: this only renders whatever `thumbnail_url` the
 * catalog already carries, and treats its absence/failure as the normal case.
 */

import { useState } from "react";

export interface SourceAvatarImageProps {
  /** The catalog `thumbnail_url`; null/blank or a failed load → the initial. */
  thumbnail_url: string | null;
  /** Display name — the fallback initial is its first character (uppercased). */
  source_name: string;
}

/**
 * Render the `.av` tile's img-or-initial with a resilient initials fallback.
 *
 * @param props - {@link SourceAvatarImageProps}.
 *
 * @example
 * <div className="av sq">
 *   <SourceAvatarImage thumbnail_url={source.thumbnail_url} source_name={source.source_name} />
 *   <span className="pbadge"><Glyph id={axis.glyph} /></span>
 * </div>
 */
export function SourceAvatarImage({ thumbnail_url, source_name }: SourceAvatarImageProps) {
  // Reason: once the remote thumbnail errors we drop the <img> for good and show the
  // initial — the same single-shot pattern as SourceArtwork, so onError can't loop
  // and the tile never stays stuck on a broken/empty image.
  const [hasLoadError, setHasLoadError] = useState(false);
  const initial = source_name.charAt(0).toUpperCase() || "?";

  if (!thumbnail_url || hasLoadError) {
    return <span className="mono">{initial}</span>;
  }

  return (
    // biome-ignore lint/performance/noImgElement: small remote avatar in a static export; next/image is inappropriate here.
    <img src={thumbnail_url} alt="" referrerPolicy="no-referrer" onError={() => setHasLoadError(true)} />
  );
}
