"use client";

/**
 * AskSheetType — the TYPE body of the ask sheet (prototype `blip-reel.js`
 * `composerEmpty()` → `thinking()` → `answerBody()` / `refusalBody()`). Owned by
 * Sub-phase 4b: it renders the suggested-question list + composer + on-screen
 * keyboard, runs the question through grounded Q&A ({@link askQuestion}), and
 * shows the answer bubble (with a "read the full story" hand-off) or the
 * `⌀ CAN'T ANSWER FROM SOURCE` refusal card when `answer_is_grounded === false`.
 *
 * Renders the `.sheet-body` (and any footer) only — the shared `sheet-grab` +
 * `ask-head` header is owned by {@link AskSheet}.
 *
 * **4b STUB** — this is the placeholder body; Sub-phase 4b replaces it with the
 * wired composer.
 */
import type { Story } from "@/types/feed";

export interface AskSheetTypeProps {
  /** The active story to ground answers in (`story.digest_id` is the Q&A `story_id`). */
  story: Story;
  /** Close the sheet. */
  onClose: () => void;
  /** Hand off to the full-article layer ("read the full story"). */
  onOpenArticle: () => void;
}

/** Render the type-ask body. */
export function AskSheetType(_props: AskSheetTypeProps) {
  return (
    <div className="sheet-body" style={{ alignItems: "center", justifyContent: "center", textAlign: "center" }}>
      <p style={{ color: "rgba(255,255,255,.72)", fontSize: "14.5px", lineHeight: 1.5, maxWidth: "300px" }}>
        Ask anything about this story — answers stay grounded in its source.
      </p>
    </div>
  );
}
