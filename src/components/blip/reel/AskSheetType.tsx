"use client";

/**
 * AskSheetType — the TYPE body of the ask sheet (prototype `blip-reel.js`
 * `composerEmpty()` → `thinking()` → `answerBody()` / `refusalBody()`). Renders
 * only the `.sheet-body` (+ composer footer / answer footer) — the shared
 * `sheet-grab` + `ask-head` header is rendered by the parent {@link AskSheet}.
 *
 * State machine (4 states):
 *   1. **empty** — suggested questions + composer + grounding note
 *   2. **thinking** — question bubble + animated typing indicator
 *   3. **answered** — question bubble + answer bubble + "Read the full story" + follow-up
 *   4. **refusal** — question bubble + refusal card + follow-up
 *
 * Wired to the REAL grounded Q&A via {@link askQuestion}: calls
 * `askQuestion(story.digest_id, question_text)` and branches on
 * `answer_is_grounded`.
 *
 * **On-screen keyboard:** The prototype rendered a fake `keyboard()` / `.kbd-pane`
 * div because it is a web demo. This component uses a real, focusable `<input>`,
 * which triggers the iOS native keyboard automatically. The `.kbd-pane` div is
 * intentionally omitted.
 *
 * @example
 * <AskSheetType story={activeStory} onClose={closeSheet} onOpenArticle={openArticle} />
 */

import { type FormEvent, useRef, useState } from "react";
import { ic } from "@/components/blip/reel/icons";
import { logger } from "@/lib/logger";
import { askQuestion } from "@/lib/qa/askQuestion";
import type { Story } from "@/types/feed";
import type { QuestionAnswer } from "@/types/qa";

export interface AskSheetTypeProps {
  /** The active story to ground answers in (`story.digest_id` is the Q&A `story_id`). */
  story: Story;
  /** Close the sheet. */
  onClose: () => void;
  /** Hand off to the full-article layer ("read the full story"). */
  onOpenArticle: () => void;
}

/** Generic suggested questions — the Story type carries no per-story suggestions. */
const SUGGESTED_QUESTIONS: readonly string[] = [
  "What led to this?",
  "Why does this matter?",
  "Who’s affected?",
] as const;

/** The four states of the type-ask body (matches prototype composerEmpty/thinking/answerBody/refusalBody). */
type AskState =
  | { phase: "empty" }
  | { phase: "thinking"; question_text: string }
  | { phase: "answered"; question_text: string; answer: QuestionAnswer }
  | { phase: "refusal"; question_text: string; answer: QuestionAnswer };

/**
 * Run a question through grounded Q&A and transition the state machine.
 *
 * @param story_id - The `story.digest_id` used as the Q&A story key.
 * @param question_text - The trimmed user question.
 * @param setState - The AskState setter.
 */
async function runAsk(story_id: string, question_text: string, setState: (state: AskState) => void): Promise<void> {
  logger.info("type_ask_submitted", { story_id, question_length: question_text.length });

  // Move to thinking immediately so the user sees the question bubble + dots.
  setState({ phase: "thinking", question_text });

  try {
    const answer = await askQuestion(story_id, question_text);

    if (answer.answer_is_grounded) {
      logger.info("type_ask_answered", {
        story_id,
        answer_is_grounded: true,
        citation_count: answer.answer_citations.length,
      });
      setState({ phase: "answered", question_text, answer });
    } else {
      logger.info("type_ask_refusal", {
        story_id,
        answer_is_grounded: false,
        fix_suggestion: "Question was off-topic for this story; the refusal card is shown.",
      });
      setState({ phase: "refusal", question_text, answer });
    }
  } catch (error: unknown) {
    // askQuestion already degrades to a safe refusal — this guard is belt-and-suspenders.
    logger.error("type_ask_unexpected_error", {
      story_id,
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion: "askQuestion should never reject — check askQuestion.ts for the safe refusal fallback.",
    });
    setState({
      phase: "refusal",
      question_text,
      answer: {
        answer_text:
          "I can only answer from this story’s source — that isn’t available right now. Try a suggested question, or ask again in a moment.",
        answer_citations: [],
        answer_is_grounded: false,
      },
    });
  }
}

/** Render the type-ask body. */
export function AskSheetType({ story, onOpenArticle }: AskSheetTypeProps) {
  const [askState, setAskState] = useState<AskState>({ phase: "empty" });
  const [draftQuestion, setDraftQuestion] = useState<string>("");
  const followupInputRef = useRef<HTMLInputElement>(null);

  const isThinking = askState.phase === "thinking";

  /** Submit a question from either the composer or a follow-up input. */
  function submitQuestion(question_text: string): void {
    const trimmed = question_text.trim();
    if (trimmed.length === 0 || isThinking) {
      return;
    }
    setDraftQuestion("");
    void runAsk(story.digest_id, trimmed, setAskState);
  }

  /** Handle the main composer form submit. */
  function handleComposerSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    submitQuestion(draftQuestion);
  }

  /** Handle follow-up form submit. */
  function handleFollowupSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (followupInputRef.current) {
      submitQuestion(followupInputRef.current.value);
      followupInputRef.current.value = "";
    }
  }

  return (
    <>
      {/* ── STATE: EMPTY / COMPOSER ── */}
      {askState.phase === "empty" && (
        <div className="sheet-body">
          <div className="sq-label">{ic("spark")} TRY ASKING</div>
          <div className="sq-list">
            {SUGGESTED_QUESTIONS.map((question_text, index) => (
              <button
                key={question_text}
                type="button"
                className={`sq${index === 1 ? " hot" : ""}`}
                onClick={() => submitQuestion(question_text)}
              >
                {ic("search")}
                {question_text}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── STATE: THINKING ── */}
      {askState.phase === "thinking" && (
        <div className="sheet-body">
          <div className="thread">
            <div className="row-q">
              <div className="bub-q">{askState.question_text}</div>
            </div>
            <div className="row-a">
              <div className="bub-a typing">
                <i />
                <i />
                <i />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── STATE: ANSWERED ── */}
      {askState.phase === "answered" && (
        <div className="sheet-body">
          <div className="thread">
            <div className="row-q">
              <div className="bub-q">{askState.question_text}</div>
            </div>
            <div className="row-a">
              <div className="bub-a">
                <p>{askState.answer.answer_text}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── STATE: REFUSAL ── */}
      {askState.phase === "refusal" && (
        <div className="sheet-body">
          <div className="thread">
            <div className="row-q">
              <div className="bub-q">{askState.question_text}</div>
            </div>
            <div className="row-a">
              <div className="refusal">
                <div className="rl">⌀ CAN&rsquo;T ANSWER FROM SOURCE</div>
                <p>{askState.answer.answer_text}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── FOOTER: COMPOSER (empty state only) ── */}
      {askState.phase === "empty" && (
        <form className="composer" onSubmit={handleComposerSubmit}>
          <div className="cbar">
            <input
              value={draftQuestion}
              onChange={(event) => setDraftQuestion(event.target.value)}
              placeholder="Ask anything about this story…"
              autoComplete="off"
              aria-label="Ask a question about this story"
            />
            <button
              type="submit"
              className={`csend${draftQuestion.trim().length === 0 ? " off" : ""}`}
              aria-label="Send question"
            >
              {ic("send")}
            </button>
          </div>
          <div className="grounded">Answers grounded in this story&rsquo;s source</div>
        </form>
      )}

      {/* ── FOOTER: ANSWER FOOT (thinking / answered / refusal states) ── */}
      {(askState.phase === "thinking" || askState.phase === "answered" || askState.phase === "refusal") && (
        <div className="ans-foot">
          {/* "Read the full story" button — answered state only */}
          {askState.phase === "answered" && (
            <button type="button" className="read-full" onClick={onOpenArticle}>
              {ic("doc")}Read the full story
            </button>
          )}

          {/* Follow-up composer — thinking / answered / refusal */}
          <form
            className="followup"
            style={askState.phase === "answered" ? { marginTop: "10px" } : undefined}
            onSubmit={handleFollowupSubmit}
          >
            <input
              ref={followupInputRef}
              placeholder="Ask a follow-up…"
              autoComplete="off"
              disabled={isThinking}
              aria-label="Ask a follow-up question"
            />
            <button type="submit" className="fsend" disabled={isThinking} aria-label="Send follow-up question">
              {ic("send")}
            </button>
          </form>
        </div>
      )}
    </>
  );
}
