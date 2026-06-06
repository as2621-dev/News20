"use client";

/**
 * SuggestedQuestionChips — the horizontally-scrolling row of tappable Q&A starter
 * chips above the pinned composer (port-map §2 row 6, §7; prototype `#qa-chips` +
 * `.qa-chip` in `app.js` / `styles.css`).
 *
 * Renders `detail.suggested_questions` (already ordered by `question_index` from
 * `fetchStoryDetail`) as mono pill chips. Tapping a chip is equivalent to typing
 * that question — it calls {@link SuggestedQuestionChipsProps.onAsk} with the
 * chip's text, which the Detail wires to `askQuestion` (the same path as the
 * composer submit), so a tap shows the thinking state then the grounded/refusal
 * answer (the plan's DoD: "typing OR tapping a suggested question").
 *
 * **Empty handling.** A story with no suggested questions renders nothing (returns
 * `null`) — no empty chip row.
 *
 * @example
 * <SuggestedQuestionChips suggestedQuestions={detail.suggested_questions} onAsk={ask} disabled={isThinking} />
 */

import type { SuggestedQuestion } from "@/types/detail";

export interface SuggestedQuestionChipsProps {
  /**
   * The story's suggested-question chips, ordered by `question_index`
   * (`fetchStoryDetail(...).suggested_questions`). Empty → renders nothing.
   */
  suggestedQuestions: SuggestedQuestion[];
  /**
   * Called with a chip's `question_text` when tapped. The Detail wires this to the
   * same ask path as the composer submit.
   */
  onAsk: (questionText: string) => void;
  /**
   * When `true`, chips are disabled (an answer is in flight) so a user can't
   * stack overlapping questions while the thinking state is showing.
   */
  disabled?: boolean;
}

/**
 * Render the suggested-question chip row, or nothing when there are no chips.
 *
 * The chips port the prototype's `.qa-chip` mono pill verbatim (port-map §7);
 * colours are kept on the dark-canvas neutral scale (no hardcoded segment/bias
 * token), and the row scrolls horizontally without a visible scrollbar.
 */
export function SuggestedQuestionChips({ suggestedQuestions, onAsk, disabled = false }: SuggestedQuestionChipsProps) {
  // Reason: no chips → render nothing rather than an empty scroll row.
  if (suggestedQuestions.length === 0) {
    return null;
  }

  return (
    <div
      data-qa-chips
      className="mb-2.5 flex gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {suggestedQuestions.map((question) => (
        <button
          key={question.question_index}
          type="button"
          data-qa-chip
          disabled={disabled}
          onClick={() => onAsk(question.question_text)}
          className="min-h-[44px] whitespace-nowrap rounded-pill border border-white/[0.16] bg-white/[0.03] px-[13px] py-2 font-mono text-[11px] tracking-[0.02em] text-slate-300 transition-colors active:bg-white/10 disabled:opacity-40"
        >
          {question.question_text}
        </button>
      ))}
    </div>
  );
}
