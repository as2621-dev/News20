"use client";

/**
 * AskSheetType — the TYPE body of the ask sheet (prototype `blip-reel.js`
 * `composerEmpty()` → `thinking()` → `answerBody()` / `refusalBody()`). Renders
 * only the `.sheet-body` (+ composer footer / answer footer) — the shared
 * `sheet-grab` + `ask-head` header is rendered by the parent {@link AskSheet}.
 *
 * **Thread model (Bug 3).** The sheet holds the FULL conversation: every
 * completed turn renders as a question bubble + answer bubble (or refusal
 * card), with the in-flight question + typing dots appended while thinking.
 * Each ask ships the recent prior turns to the worker
 * (`askQuestion(..., conversation_turns)`) so follow-ups like "what about its
 * margins?" resolve against the thread, not in isolation.
 *
 * **Persistence (Bug 5).** The thread + composer draft hydrate from and save to
 * {@link loadQaThreadForStory}/{@link saveQaThreadForStory} (localStorage, per
 * story) — closing the sheet, swiping stories, or restarting the app never
 * loses the conversation. Saves happen on every completed turn and once on
 * unmount (captures the latest draft without per-keystroke writes).
 *
 * **On-screen keyboard:** The prototype rendered a fake `keyboard()` / `.kbd-pane`
 * div because it is a web demo. This component uses a real, focusable `<input>`,
 * which triggers the iOS native keyboard automatically. The `.kbd-pane` div is
 * intentionally omitted.
 *
 * @example
 * <AskSheetType story={activeStory} onClose={closeSheet} onOpenArticle={openArticle} />
 */

import { type FormEvent, useEffect, useRef, useState } from "react";
import { ic } from "@/components/blip/reel/icons";
import { logger } from "@/lib/logger";
import { askQuestion } from "@/lib/qa/askQuestion";
import { loadQaThreadForStory, saveQaThreadForStory } from "@/lib/qa/qaHistoryStore";
import type { Story } from "@/types/feed";
import type { CompletedQaTurn, QaConversationTurn } from "@/types/qa";

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

/**
 * Flatten completed turns into the wire-shape conversation turns
 * (question → `user`, answer/refusal text → `model`), oldest first.
 */
function buildConversationTurns(completedTurns: CompletedQaTurn[]): QaConversationTurn[] {
  return completedTurns.flatMap((turn): QaConversationTurn[] => [
    { role: "user", text: turn.question_text },
    { role: "model", text: turn.answer.answer_text },
  ]);
}

/** Render one completed turn: question bubble + answer bubble or refusal card. */
function CompletedTurnRows({ turn }: { turn: CompletedQaTurn }) {
  return (
    <>
      <div className="row-q">
        <div className="bub-q">{turn.question_text}</div>
      </div>
      <div className="row-a">
        {turn.answer.answer_is_grounded ? (
          <div className="bub-a">
            <p>{turn.answer.answer_text}</p>
          </div>
        ) : (
          <div className="refusal">
            <div className="rl">⌀ CAN&rsquo;T ANSWER FROM SOURCE</div>
            <p>{turn.answer.answer_text}</p>
          </div>
        )}
      </div>
    </>
  );
}

/** Render the type-ask body. */
export function AskSheetType({ story, onOpenArticle }: AskSheetTypeProps) {
  // Hydrate the thread + draft from the per-story persisted history (Bug 5).
  // Lazy initializers: one storage read per mount, captured for both states.
  const [completedTurns, setCompletedTurns] = useState<CompletedQaTurn[]>(
    () => loadQaThreadForStory(story.digest_id)?.completed_turns ?? [],
  );
  const [draftQuestion, setDraftQuestion] = useState<string>(
    () => loadQaThreadForStory(story.digest_id)?.draft_question_text ?? "",
  );
  const [pendingQuestionText, setPendingQuestionText] = useState<string | null>(null);
  const followupInputRef = useRef<HTMLInputElement>(null);
  const threadEndRef = useRef<HTMLDivElement>(null);

  const isThinking = pendingQuestionText !== null;
  const hasThread = completedTurns.length > 0 || isThinking;
  const lastCompletedTurn = completedTurns.length > 0 ? completedTurns[completedTurns.length - 1] : null;

  // Persist the latest thread + draft once on unmount (sheet close / story
  // swipe) — catches the unsent draft without per-keystroke writes.
  const latestThreadRef = useRef<{ completed_turns: CompletedQaTurn[]; draft_question_text: string }>({
    completed_turns: completedTurns,
    draft_question_text: draftQuestion,
  });
  latestThreadRef.current = { completed_turns: completedTurns, draft_question_text: draftQuestion };
  useEffect(() => {
    return () => {
      saveQaThreadForStory(story.digest_id, latestThreadRef.current);
    };
  }, [story.digest_id]);

  // Keep the newest bubbles in view as the thread grows.
  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll on thread growth only.
  useEffect(() => {
    // Reason: guarded — scrollIntoView is missing in jsdom (tests) and some WebViews.
    if (typeof threadEndRef.current?.scrollIntoView === "function") {
      threadEndRef.current.scrollIntoView({ block: "end" });
    }
  }, [completedTurns.length, pendingQuestionText]);

  /** Run a question through grounded Q&A and append the completed turn. */
  async function runAsk(question_text: string): Promise<void> {
    const story_id = story.digest_id;
    logger.info("type_ask_submitted", {
      story_id,
      question_length: question_text.length,
      turn_count: completedTurns.length,
    });
    setPendingQuestionText(question_text);

    let answer: CompletedQaTurn["answer"];
    try {
      answer = await askQuestion(story_id, question_text, buildConversationTurns(completedTurns));
      logger.info(answer.answer_is_grounded ? "type_ask_answered" : "type_ask_refusal", {
        story_id,
        answer_is_grounded: answer.answer_is_grounded,
        citation_count: answer.answer_citations.length,
      });
    } catch (error: unknown) {
      // askQuestion already degrades to a safe refusal — this guard is belt-and-suspenders.
      logger.error("type_ask_unexpected_error", {
        story_id,
        error_message: error instanceof Error ? error.message : "Unknown error",
        fix_suggestion: "askQuestion should never reject — check askQuestion.ts for the safe refusal fallback.",
      });
      answer = {
        answer_text:
          "I can only answer from this story’s source — that isn’t available right now. Try a suggested question, or ask again in a moment.",
        answer_citations: [],
        answer_is_grounded: false,
      };
    }

    setCompletedTurns((previousTurns) => {
      const nextTurns = [...previousTurns, { question_text, answer }];
      // Persist on every completed turn so a hard app kill loses nothing.
      saveQaThreadForStory(story_id, {
        completed_turns: nextTurns,
        draft_question_text: latestThreadRef.current.draft_question_text,
      });
      return nextTurns;
    });
    setPendingQuestionText(null);
  }

  /** Submit a question from either the composer or a follow-up input. */
  function submitQuestion(question_text: string): void {
    const trimmed = question_text.trim();
    if (trimmed.length === 0 || isThinking) {
      return;
    }
    setDraftQuestion("");
    void runAsk(trimmed);
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
      {/* ── BODY: EMPTY (suggested questions) or the full THREAD ── */}
      {hasThread ? (
        <div className="sheet-body">
          <div className="thread">
            {completedTurns.map((turn, turnIndex) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: append-only thread; index IS the turn identity.
              <CompletedTurnRows key={turnIndex} turn={turn} />
            ))}
            {isThinking ? (
              <>
                <div className="row-q">
                  <div className="bub-q">{pendingQuestionText}</div>
                </div>
                <div className="row-a">
                  <div className="bub-a typing">
                    <i />
                    <i />
                    <i />
                  </div>
                </div>
              </>
            ) : null}
            <div ref={threadEndRef} />
          </div>
        </div>
      ) : (
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

      {/* ── FOOTER: COMPOSER (no thread yet) ── */}
      {!hasThread && (
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

      {/* ── FOOTER: ANSWER FOOT (thread view) ── */}
      {hasThread && (
        <div className="ans-foot">
          {/* "Read the full story" button — last turn grounded only */}
          {!isThinking && lastCompletedTurn?.answer.answer_is_grounded && (
            <button type="button" className="read-full" onClick={onOpenArticle}>
              {ic("doc")}Read the full story
            </button>
          )}

          {/* Follow-up composer */}
          <form
            className="followup"
            style={!isThinking && lastCompletedTurn?.answer.answer_is_grounded ? { marginTop: "10px" } : undefined}
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
