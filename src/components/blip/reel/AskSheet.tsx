"use client";

/**
 * AskSheet — the bottom sheet that rises over the paused, dimmed reel for asking
 * about the active story (prototype `blip-reel.js` ask sheet). It owns the SHARED
 * header (`sheet-grab` + `ask-head`: accent dot + story headline + close) and
 * delegates the body to one of two disjoint modules so they can be built
 * independently:
 *   - TYPE  → {@link AskSheetType}  (composer + suggested questions + on-screen
 *             keyboard, wired to grounded Q&A — Sub-phase 4b).
 *   - VOICE → {@link AskSheetVoice} (permission → listening → responding orb,
 *             wired to Gemini Live — Sub-phase 4c).
 *
 * The `--accent` cascade is supplied by the `.sheet` wrapper in {@link BlipReel},
 * so the header dot reads the active story's accent. The body components mount
 * only while the sheet is the active overlay (BlipReel renders AskSheet then), so
 * their hook lifecycles (Q&A fetch, Gemini Live connect/disconnect) bind to the
 * sheet's open/close.
 */
import { AskSheetType } from "@/components/blip/reel/AskSheetType";
import { AskSheetVoice } from "@/components/blip/reel/AskSheetVoice";
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
  /** Hand off to the full-article layer ("read the full story"). */
  onOpenArticle: () => void;
}

/**
 * Render the ask sheet for the active story: the shared header plus the
 * mode-specific body. Returns the sheet's INNER content; the sliding `.sheet`
 * container + scrim are owned by {@link BlipReel}.
 */
export function AskSheet({ story, mode, onClose, onOpenArticle }: AskSheetProps) {
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
      {mode === "voice" ? (
        <AskSheetVoice story={story} onClose={onClose} onOpenArticle={onOpenArticle} />
      ) : (
        <AskSheetType story={story} onClose={onClose} onOpenArticle={onOpenArticle} />
      )}
    </>
  );
}
