"use client";

/**
 * QaComposer — the pinned bottom Q&A input (port-map §2 row 6, §7; prototype
 * `#qa-input` / `#qa-send` in `app.js` / `styles.css`).
 *
 * The text field + send button row for asking a story a free-text question. Pinned
 * to the bottom of the Detail over a fade-to-canvas gradient, above the
 * grounding-disclaimer caption. Submitting (Enter or the send button) calls
 * {@link QaComposerProps.onAsk} with the trimmed text — the same path a tapped
 * suggested chip takes — then clears the field.
 *
 * **Hit-target discipline (port-map §3.2 / §6).** The send button is ≥44×44px and
 * the input row clears the bottom safe-area inset.
 *
 * **In-flight guard.** While an answer is in flight (`disabled`), the input + send
 * are disabled so a user can't stack overlapping questions over the thinking state.
 *
 * @example
 * <QaComposer onAsk={ask} disabled={isThinking} />
 */

import { type FormEvent, useState } from "react";

export interface QaComposerProps {
  /**
   * Called with the trimmed question text on submit (Enter or send button). The
   * Detail wires this to `askQuestion`; empty/whitespace input is ignored.
   */
  onAsk: (questionText: string) => void;
  /**
   * When `true`, the input + send button are disabled (an answer is in flight) so
   * a user can't submit a second question over the thinking state.
   */
  disabled?: boolean;
}

/**
 * Render the pinned bottom composer.
 *
 * Holds the draft locally; on submit it trims, ignores empty input, calls
 * {@link QaComposerProps.onAsk}, and clears the field. The send button + input row
 * meet the ≥44px touch-target rule.
 */
export function QaComposer({ onAsk, disabled = false }: QaComposerProps) {
  const [draftQuestion, setDraftQuestion] = useState<string>("");

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmed = draftQuestion.trim();
    // Reason: ignore empty/whitespace-only submits (matches the prototype's
    // `if (!v) return` guard) so a blank Enter never starts a turn.
    if (trimmed.length === 0 || disabled) {
      return;
    }
    onAsk(trimmed);
    setDraftQuestion("");
  };

  return (
    <form data-qa-composer onSubmit={handleSubmit}>
      <div className="flex items-center gap-2 rounded-control border border-white/[0.12] bg-white/[0.05] py-2 pr-2 pl-4">
        <input
          data-qa-input
          value={draftQuestion}
          onChange={(event) => setDraftQuestion(event.target.value)}
          disabled={disabled}
          placeholder="Ask this story…"
          autoComplete="off"
          aria-label="Ask this story a question"
          className="min-h-[44px] flex-1 bg-transparent font-sans text-[14px] text-white outline-none placeholder:text-white/35 disabled:opacity-50"
        />
        <button
          type="submit"
          data-qa-send
          disabled={disabled}
          aria-label="Send question"
          className="grid h-11 w-11 place-items-center rounded-control bg-primary/[0.18] text-[#93b4ff] transition-opacity disabled:opacity-40"
        >
          {/* Reason: inline send glyph ports the prototype's `icon("send")` arrow
              without pulling an icon dependency the SPA doesn't otherwise use. */}
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true">
            <path
              d="M4 12L20 4L13 20L11 13L4 12Z"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
      <div className="mt-2 text-center font-mono text-[8.5px] tracking-wide text-white/30">
        ANSWERS GROUNDED IN THIS STORY&rsquo;S SOURCE
      </div>
    </form>
  );
}
