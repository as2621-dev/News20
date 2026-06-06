"use client";

/**
 * AskSheetVoice — the VOICE body of the ask sheet (prototype `blip-reel.js`
 * `voicePermission()` → `voiceListening()` → `voiceResponding()`). Owned by
 * Sub-phase 4c: it renders the mic-permission card, then the live orb
 * (`LISTENING` ⇄ `RESPONDING`) + spoken-turn transcript, wired to
 * {@link useGeminiLive} grounded on the active story via {@link storyQaTool}.
 *
 * Renders the `.sheet-body` (and the `vs-foot` end button) only — the shared
 * `sheet-grab` + `ask-head` header is owned by {@link AskSheet}. The Gemini Live
 * connect/disconnect binds to this component's mount/unmount (the sheet only
 * renders it while voice is the active overlay).
 *
 * **4c STUB** — this is the placeholder body; Sub-phase 4c replaces it with the
 * wired orb + transcript.
 */
import type { Story } from "@/types/feed";

export interface AskSheetVoiceProps {
  /** The active story to ground the voice session in. */
  story: Story;
  /** Close the sheet (the "END VOICE · BACK TO REEL" action). */
  onClose: () => void;
  /** Hand off to the full-article layer ("read the full story"). */
  onOpenArticle: () => void;
}

/** Render the voice-ask body. */
export function AskSheetVoice(_props: AskSheetVoiceProps) {
  return (
    <div className="sheet-body" style={{ alignItems: "center", justifyContent: "center", textAlign: "center" }}>
      <p style={{ color: "rgba(255,255,255,.72)", fontSize: "14.5px", lineHeight: 1.5, maxWidth: "300px" }}>
        Ask out loud, hands-free — answers stay grounded in this story’s source.
      </p>
    </div>
  );
}
