"use client";

/**
 * AskSheet — the bottom sheet that rises over the paused, dimmed reel for asking
 * about the active story (prototype `blip-reel.js` ask sheet). It carries the
 * shared header (`ask-head`: accent dot + story headline + close) and a body that
 * switches on {@link AskSheetMode}: TYPE (composer + suggested questions + on-
 * screen keyboard, wired to grounded Q&A in Sub-phase 4b) and VOICE (permission →
 * listening → responding, wired to Gemini Live in Sub-phase 4c).
 *
 * **4a scope.** The open/close/dim/pause PLUMBING is live (this renders inside the
 * `.sheet` singleton that {@link BlipReel} slides up). The bodies are the real
 * grounded-source copy but non-interactive; 4b/4c replace them with the wired
 * composer and voice orb. The `--accent` cascade is supplied by the `.sheet`
 * wrapper in {@link BlipReel}, so the header dot reads the active story's accent.
 */
import { ic } from "@/components/blip/reel/icons";
import type { Story } from "@/types/feed";

/** Which ask affordance opened the sheet. */
export type AskSheetMode = "type" | "voice";

export interface AskSheetProps {
  /** The active story the sheet is asking about (drives the header + grounding). */
  story: Story;
  /** Whether the sheet opened from the question field (`type`) or signal button (`voice`). */
  mode: AskSheetMode;
  /** Close the sheet (restores the reel + resumes narration if it was playing). */
  onClose: () => void;
  /** Hand off to the full-article layer ("read the full story"). Used by 4b/4c answers. */
  onOpenArticle: () => void;
}

/**
 * Render the ask sheet for the active story. Returns the sheet's INNER content;
 * the sliding `.sheet` container + scrim are owned by {@link BlipReel}.
 */
export function AskSheet({ story, mode, onClose }: AskSheetProps) {
  return (
    <>
      <div className="sheet-grab" />
      <div className="ask-head">
        <div className="ah-title">
          <span className="seg-dot" />
          <span className="ah-text">{story.headline}</span>
        </div>
        <button type="button" className="sheet-x" aria-label="Close" onClick={onClose}>
          {ic("close")}
        </button>
      </div>
      <div className="sheet-body" style={{ alignItems: "center", justifyContent: "center", textAlign: "center" }}>
        <p style={{ color: "rgba(255,255,255,.72)", fontSize: "14.5px", lineHeight: 1.5, maxWidth: "300px" }}>
          {mode === "voice"
            ? "Ask out loud, hands-free — answers stay grounded in this story’s source."
            : "Ask anything about this story — answers stay grounded in its source."}
        </p>
      </div>
    </>
  );
}
