"use client";

/**
 * QaThread — the grounded-Q&A conversation thread (port-map §2 row 6, §7;
 * prototype `askQuestion()` / `#qa-thread` in `app.js`).
 *
 * Renders, top-to-bottom, the ask/answer turns of the Detail Q&A:
 *   - a right-aligned **`.qa-bubble-q`** for each user question,
 *   - while an answer is in flight, the **`.dot-typing`** thinking state (three
 *     blinking dots) — the thinking-before-every-answer beat (port-map §7),
 *   - then ONE of two byte-for-byte-ported answer states keyed on
 *     `answer_is_grounded`:
 *       • grounded → a left-aligned **`.qa-bubble-a`** carrying the answer text and
 *         one **`.cite-chip`** per `answer_citations` entry (chip label = the
 *         citation outlet name),
 *       • not grounded → the **`.qa-refusal`** blush card with the mono header
 *         **`⌀ CAN'T ANSWER FROM SOURCE`** and the refusal copy, and NEVER a
 *         citation chip.
 *
 * **The trust guarantee (Rule 9).** `answer_is_grounded` is the SINGLE switch
 * between the two states. A refusal turn renders the refusal card and never an
 * answer bubble; a grounded turn renders exactly one chip per citation. This
 * visual distinction is how users learn to trust the system (port-map §7) — it is
 * not redesigned here, only ported.
 *
 * This component is presentational: it renders the {@link QaTurn}[] the Detail
 * owns. The ask → thinking → answer transition is driven by the Detail flipping a
 * turn from `phase: "thinking"` to `phase: "answered"`.
 *
 * @example
 * <QaThread turns={turns} />
 */

import type { QuestionAnswer } from "@/types/qa";

/**
 * One Q&A turn in the thread: the user's question plus, once resolved, the answer.
 *
 * While `phase === "thinking"` the answer is absent and the `.dot-typing` state
 * shows; once `phase === "answered"` the `answer` is present and the grounded
 * bubble or refusal card renders. The Detail owns the list and flips the phase.
 */
export interface QaTurn {
  /** Stable key for the turn (monotonic id from the Detail). */
  turn_id: number;
  /** The user's question text (rendered in the `.qa-bubble-q`). */
  question_text: string;
  /** `"thinking"` → show `.dot-typing`; `"answered"` → render the answer. */
  phase: "thinking" | "answered";
  /** The resolved answer; `null` until `phase === "answered"`. */
  answer: QuestionAnswer | null;
}

/** The mono refusal-header colour — the prototype's blush `#E8B7BC` (port-map §7). */
const REFUSAL_HEADER_STYLE = { color: "#E8B7BC" } as const;

export interface QaThreadProps {
  /** The ordered Q&A turns the Detail owns. Empty → renders an empty thread. */
  turns: QaTurn[];
}

/**
 * Render the Q&A thread: a question bubble + thinking/answer for each turn.
 *
 * Branches each answered turn on {@link QuestionAnswer.answer_is_grounded} (Rule 9)
 * — grounded → `.qa-bubble-a` with one `.cite-chip` per citation; not grounded →
 * the `.qa-refusal` card with NO chips.
 */
export function QaThread({ turns }: QaThreadProps) {
  return (
    <div data-qa-thread className="mt-6 flex flex-col">
      {turns.map((turn) => (
        <div key={turn.turn_id}>
          {/* user question — right-aligned blue bubble */}
          <div className="mb-3 flex justify-end">
            <div className="qa-bubble-q max-w-[78%] rounded-[16px_16px_4px_16px] border border-primary/35 bg-primary/[0.14] px-4 py-2.5">
              <p className="font-sans text-[14px] leading-snug text-white">{turn.question_text}</p>
            </div>
          </div>

          {turn.phase === "thinking" || turn.answer === null ? (
            <ThinkingBubble />
          ) : turn.answer.answer_is_grounded ? (
            <GroundedAnswerBubble answer={turn.answer} />
          ) : (
            <RefusalCard answer={turn.answer} />
          )}
        </div>
      ))}
    </div>
  );
}

/** The `.dot-typing` thinking state — three blinking dots in an answer bubble. */
function ThinkingBubble() {
  return (
    <div className="mb-3 flex justify-start">
      <div
        data-qa-thinking
        className="qa-bubble-a rounded-[16px_16px_16px_4px] border border-white/10 bg-white/[0.04] px-4 py-3"
      >
        <span className="dot-typing" />
        <span className="dot-typing" />
        <span className="dot-typing" />
      </div>
    </div>
  );
}

/**
 * The grounded answer: `.qa-bubble-a` with the answer text and one `.cite-chip`
 * per citation. Chip label = the citation's outlet name.
 */
function GroundedAnswerBubble({ answer }: { answer: QuestionAnswer }) {
  return (
    <div className="mb-4 flex justify-start">
      <div
        data-qa-bubble-a
        className="qa-bubble-a max-w-[88%] rounded-[16px_16px_16px_4px] border border-white/10 bg-white/[0.04] px-4 py-3"
      >
        <p className="font-sans text-[14px] leading-relaxed text-white/90">{answer.answer_text}</p>
        {answer.answer_citations.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {answer.answer_citations.map((citation) => (
              <span
                key={citation.passage_id}
                data-cite-chip
                className="cite-chip inline-flex items-center gap-[5px] rounded-pill border border-white/[0.12] bg-white/[0.06] px-2 py-[3px] font-mono text-[9.5px] tracking-[0.04em] text-slate-300"
              >
                {citation.source_outlet_name}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The `.qa-refusal` blush card with the mono `⌀ CAN'T ANSWER FROM SOURCE` header
 * and the refusal copy. NEVER renders a citation chip (Rule 9 — the trust guarantee).
 */
function RefusalCard({ answer }: { answer: QuestionAnswer }) {
  return (
    <div className="mb-4 flex justify-start">
      <div
        data-qa-refusal
        className="qa-refusal max-w-[90%] rounded-[16px] border border-accent/30 bg-accent/[0.08] px-4 py-3"
      >
        <div className="mb-1.5 font-mono text-[9px] tracking-[0.12em]" style={REFUSAL_HEADER_STYLE}>
          ⌀ CAN&rsquo;T ANSWER FROM SOURCE
        </div>
        <p className="font-sans text-[13.5px] leading-relaxed text-white/80">{answer.answer_text}</p>
      </div>
    </div>
  );
}
