"use client";

/**
 * ArticleLayer — the full-article layer that rises over the reel when the user
 * taps a story's headline (prototype `blip-reel.js` `renderArticle()`). It carries
 * the back-to-reel top bar + segment chip and (Sub-phase 4d) the key-stat, the
 * timeline / market / coverage analytics tabs, the bullets, and the read-more →
 * long-form + opposing-view body — wired to {@link fetchStoryDetail}.
 *
 * **4a scope.** The open/close PLUMBING is live (this renders inside the
 * `.layer-article` singleton that {@link BlipReel} slides up). The body is the
 * headline + read-length stub; 4d replaces it with the wired detail panels. The
 * `--accent` cascade is supplied by the `.layer-article` wrapper in
 * {@link BlipReel}, so the segment dot reads the active story's accent.
 */
import { ic } from "@/components/blip/reel/icons";
import type { Story } from "@/types/feed";

export interface ArticleLayerProps {
  /** The active story whose full article this layer shows. */
  story: Story;
  /** Close the article and return to the reel. */
  onClose: () => void;
  /**
   * Open the type-ask sheet from the article's own ask bar (Sub-phase 4d). Swaps
   * the single overlay (the article slides down, the sheet slides up). Optional so
   * the 4a/scaffold article renders without it.
   */
  onOpenType?: () => void;
  /** Open the voice-ask sheet from the article's own ask bar (Sub-phase 4d). */
  onOpenVoice?: () => void;
}

/**
 * Render the article layer for the active story. Returns the layer's INNER
 * content; the sliding `.layer-article` container is owned by {@link BlipReel}.
 */
export function ArticleLayer({ story, onClose }: ArticleLayerProps) {
  return (
    <>
      <div className="art-top">
        <button type="button" className="v-back" aria-label="Back to reel" onClick={onClose}>
          {ic("back")}
          REEL
        </button>
        <span className="seg-chip" style={{ color: story.segment_accent_hex }}>
          <span className="seg-dot" />
          {story.segment_label.toUpperCase()}
        </span>
      </div>
      <div className="art-scroll">
        <h1 className="art-h1">{story.headline}</h1>
        <div className="art-meta">&lt; 100s READ</div>
      </div>
    </>
  );
}
