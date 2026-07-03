"use client";

/**
 * InterviewChat — the FSR conversational onboarding stage (slice #4) that REPLACES
 * the topic picker in {@link import("./OnboardingFlow").OnboardingFlow}. The user
 * sees a short chat: one question, tappable bubbles (real options + a "skip" and a
 * "type it" affordance), each tap fetching the next turn from the stateless worker
 * (`fetchInterviewTurn`). Progress is visible; the interview ends on a confirm
 * screen ({@link InterviewConfirm}) showing the extracted interests in the user's
 * OWN words. The parent persists ONLY on confirm — no half-profiles, and
 * `users.user_onboarded_at` is NEVER stamped here (onboarding-gate rule, 2026-06-30).
 *
 * State machine (client-local; the worker holds no session):
 *   resume_prompt → (resume|restart) → loading ⇄ question → confirm
 *                                          └──────→ retry ──(retry)──┘
 * Every worker call goes through `loading`; a failed turn lands on `retry` with an
 * in-place affordance — never a dead-end screen (spec §2). Back-navigation pops the
 * last answer and re-fetches the prior question, invalidating downstream taps.
 *
 * Static-export safe: client-only (`"use client"`), `window` access is guarded
 * inside effects/handlers (never at module scope).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { InterviewConfirm } from "@/components/onboarding/InterviewConfirm";
import { clearInterviewSession, loadInterviewSession, saveInterviewSession } from "@/lib/interview/session";
import { fetchInterviewTurn } from "@/lib/interview/turnClient";
import { logger } from "@/lib/logger";
import type {
  InterviewBubble,
  InterviewExchange,
  InterviewQuestionTurn,
  InterviewRetryTurn,
  InterviewTerminalPayload,
  InterviewTerminalTurn,
} from "@/types/interview";

/**
 * Tap budget the progress bar targets (spec §3: ~15 taps, engine steers to terminal
 * by ~18). Purely cosmetic — the WORKER decides termination; this only paces the bar
 * so it reads as "nearly there" as the user approaches the target, never as a hard gate.
 */
const TAP_TARGET = 15;

/** The chat's visible phases. */
type ChatPhase = "resume_prompt" | "loading" | "question" | "retry" | "confirm";

export interface InterviewChatProps {
  /**
   * Called once the user CONFIRMS the terminal list — the single trigger for
   * persistence. Receives the terminal payload (empty `micro_interests` on the
   * skip-everything path). The parent mints the profile and advances the flow.
   */
  onComplete: (payload: InterviewTerminalPayload) => void;
  /**
   * Injectable turn fetcher (tests + the browser-verification harness pass a stub;
   * production defaults to the real JWT-scoped {@link fetchInterviewTurn}).
   */
  fetchTurn?: typeof fetchInterviewTurn;
}

/**
 * Render the conversational interview stage.
 */
export function InterviewChat({ onComplete, fetchTurn = fetchInterviewTurn }: InterviewChatProps) {
  // The exchanges that produced the CURRENTLY shown question (the stateless-worker
  // conversation state). Grows by one per answered turn; shrinks on back-navigation.
  const [conversation, setConversation] = useState<InterviewExchange[]>([]);
  const [phase, setPhase] = useState<ChatPhase>("loading");
  const [questionTurn, setQuestionTurn] = useState<InterviewQuestionTurn | null>(null);
  const [retryTurn, setRetryTurn] = useState<InterviewRetryTurn | null>(null);
  const [terminal, setTerminal] = useState<InterviewTerminalTurn | null>(null);
  const [resumeCandidate, setResumeCandidate] = useState<InterviewExchange[] | null>(null);
  const [showFreeText, setShowFreeText] = useState(false);
  const [freeTextValue, setFreeTextValue] = useState("");

  // The conversation state a failed turn was fetching — so `retry` re-sends EXACTLY
  // it (not the last committed state), keeping the retry idempotent.
  const pendingStateRef = useRef<InterviewExchange[]>([]);
  // Double-tap / in-flight guard: ignore any action while a turn request is pending
  // so a fast double-tap can't fire two turns or advance past the answer (Rule 12).
  const isBusyRef = useRef(false);
  // Run the initial start/resume decision exactly once.
  const startedRef = useRef(false);

  /**
   * Fetch the next turn for `nextState` and route to the resulting phase. The SINGLE
   * seam that talks to the worker — every advance (start, answer, back, retry, resume)
   * funnels through it, so the busy-guard, session-save, and error routing live in one place.
   */
  const advance = useCallback(
    async (nextState: InterviewExchange[]) => {
      if (isBusyRef.current) {
        return;
      }
      isBusyRef.current = true;
      pendingStateRef.current = nextState;
      setShowFreeText(false);
      setFreeTextValue("");
      setPhase("loading");
      // Persist the transcript BEFORE the network call so an abandon mid-request still resumes.
      saveInterviewSession(nextState);

      try {
        const turn = await fetchTurn(nextState);
        if (turn.response_kind === "question") {
          setConversation(nextState);
          setQuestionTurn(turn);
          setPhase("question");
          return;
        }
        if (turn.response_kind === "terminal") {
          setConversation(nextState);
          setTerminal(turn);
          setPhase("confirm");
          logger.info("interview_reached_terminal", {
            interest_count: turn.micro_interests.length,
            roots_only_fallback: turn.roots_only_fallback,
            tap_count: nextState.length,
          });
          return;
        }
        // retry: keep the last committed conversation on screen conceptually, but show
        // the retry affordance; `pendingStateRef` holds what to re-send.
        setRetryTurn(turn);
        setPhase("retry");
      } finally {
        isBusyRef.current = false;
      }
    },
    [fetchTurn],
  );

  // Start or offer resume, exactly once on mount.
  useEffect(() => {
    if (startedRef.current) {
      return;
    }
    startedRef.current = true;
    const candidate = loadInterviewSession();
    if (candidate && candidate.length > 0) {
      setResumeCandidate(candidate);
      setPhase("resume_prompt");
      logger.info("interview_resume_offered", { exchange_count: candidate.length });
      return;
    }
    void advance([]);
  }, [advance]);

  /** Answer the current question by tapping ONE bubble (option or skip). */
  const handleBubbleTap = useCallback(
    (bubble: InterviewBubble) => {
      if (isBusyRef.current || !questionTurn) {
        return;
      }
      if (bubble.bubble_kind === "type_your_own") {
        setShowFreeText(true);
        return;
      }
      const exchange: InterviewExchange = {
        question_text: questionTurn.question_text,
        bubbles_offered: questionTurn.bubbles.map((offered) => offered.bubble_label),
        bubbles_tapped: [bubble.bubble_label],
        free_text_entered: null,
      };
      void advance([...conversation, exchange]);
    },
    [advance, conversation, questionTurn],
  );

  /** Submit free text (the "something else — type it" path). */
  const handleFreeTextSubmit = useCallback(() => {
    if (isBusyRef.current || !questionTurn) {
      return;
    }
    const trimmed = freeTextValue.trim();
    if (trimmed === "") {
      return;
    }
    const exchange: InterviewExchange = {
      question_text: questionTurn.question_text,
      bubbles_offered: questionTurn.bubbles.map((offered) => offered.bubble_label),
      bubbles_tapped: [],
      free_text_entered: trimmed,
    };
    void advance([...conversation, exchange]);
  }, [advance, conversation, freeTextValue, questionTurn]);

  /**
   * Go back one step: drop the last answer and re-fetch the prior question. This
   * invalidates every downstream tap (they were derived from the answer we just
   * discarded) and regenerates from that point (acceptance criterion).
   */
  const handleBack = useCallback(() => {
    if (isBusyRef.current || conversation.length === 0) {
      return;
    }
    void advance(conversation.slice(0, -1));
  }, [advance, conversation]);

  /** Retry the failed turn — re-send the exact state it was fetching. */
  const handleRetry = useCallback(() => {
    void advance(pendingStateRef.current);
  }, [advance]);

  /** Resume the stored transcript — re-fetch the next question for it. */
  const handleResume = useCallback(() => {
    const candidate = resumeCandidate ?? [];
    setResumeCandidate(null);
    void advance(candidate);
  }, [advance, resumeCandidate]);

  /** Discard the stored transcript and start fresh. */
  const handleRestart = useCallback(() => {
    setResumeCandidate(null);
    clearInterviewSession();
    void advance([]);
  }, [advance]);

  /**
   * Confirm the terminal list → hand off for persistence. Guarded by `isBusyRef` so a
   * fast double-tap on "Looks good" can't fire `onComplete` (and thus persistence) twice;
   * the flag is never reset here because a confirm always unmounts this stage (the parent
   * advances past `interview`), and a persist failure REMOUNTS it fresh (guard resets).
   *
   * The transcript cache is NOT cleared here: the parent clears it only after a SUCCESSFUL
   * mint, so a persist failure leaves a resumable transcript (no half-profile, retry lands here).
   */
  const handleConfirm = useCallback(() => {
    if (isBusyRef.current || !terminal) {
      return;
    }
    isBusyRef.current = true;
    onComplete({
      micro_interests: terminal.micro_interests,
      roots_only_fallback: terminal.roots_only_fallback,
    });
  }, [onComplete, terminal]);

  const progressFraction = Math.min(conversation.length / TAP_TARGET, 0.92);

  return (
    <div className="flex min-h-full flex-1 flex-col bg-background text-text-primary">
      {/* Progress bar — hidden on the resume prompt (nothing in progress yet on screen). */}
      {phase !== "resume_prompt" ? (
        <div className="px-8 pt-6" aria-hidden="true">
          <div className="h-[3px] w-full overflow-hidden rounded-pill bg-white/10">
            <div
              className="h-full rounded-pill bg-primary transition-[width] duration-300"
              style={{ width: `${(phase === "confirm" ? 1 : progressFraction) * 100}%` }}
            />
          </div>
        </div>
      ) : null}

      {phase === "resume_prompt" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-5 px-10 text-center">
          <span className="font-mono text-[11px] tracking-wide text-text-secondary">WELCOME BACK</span>
          <h1 className="font-sans text-[18px] font-semibold text-text-primary">Pick up where you left off?</h1>
          <p className="font-sans text-[13px] leading-relaxed text-text-secondary">
            You started telling us what you follow. Resume, or start over.
          </p>
          <div className="mt-2 flex w-full flex-col gap-3">
            <button
              type="button"
              onClick={handleResume}
              className="w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-70"
            >
              Resume
            </button>
            <button
              type="button"
              onClick={handleRestart}
              className="font-sans text-[13px] text-text-secondary underline underline-offset-4 transition-opacity active:opacity-60"
            >
              Start over
            </button>
          </div>
        </section>
      ) : null}

      {phase === "loading" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-3 px-10 text-center">
          <span className="font-mono text-[11px] tracking-wide text-text-secondary">THINKING…</span>
        </section>
      ) : null}

      {phase === "retry" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-4 px-10 text-center">
          <span className="font-mono text-[11px] tracking-wide text-seg-wildcard">CONNECTION HICCUP</span>
          <p role="alert" className="font-sans text-[14px] leading-relaxed text-text-primary">
            {retryTurn?.error_message ?? "Something went wrong reaching the interview."}
          </p>
          {retryTurn?.retry_hint ? (
            <p className="font-sans text-[12px] leading-relaxed text-text-secondary">{retryTurn.retry_hint}</p>
          ) : null}
          <button
            type="button"
            onClick={handleRetry}
            className="mt-2 w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-70"
          >
            Retry
          </button>
        </section>
      ) : null}

      {phase === "question" && questionTurn ? (
        <section className="flex min-h-full flex-1 flex-col px-8 pt-8 pb-8">
          {conversation.length > 0 ? (
            <button
              type="button"
              onClick={handleBack}
              className="mb-4 self-start font-mono text-[11px] tracking-wide text-text-secondary transition-opacity active:opacity-60"
            >
              ← BACK
            </button>
          ) : null}

          <h1 className="font-sans text-[19px] font-semibold leading-snug text-text-primary">
            {questionTurn.question_text}
          </h1>

          <div className="mt-6 flex flex-1 flex-col gap-2.5">
            {questionTurn.bubbles.map((bubble) => (
              <button
                key={`${bubble.bubble_kind}:${bubble.bubble_label}`}
                type="button"
                onClick={() => handleBubbleTap(bubble)}
                className={
                  bubble.bubble_kind === "option"
                    ? "w-full rounded-pill border border-white/15 bg-white/5 px-5 py-3 text-left font-sans text-[15px] text-text-primary transition-colors active:bg-white/15"
                    : "w-full rounded-pill border border-dashed border-white/20 px-5 py-3 text-left font-sans text-[14px] text-text-secondary transition-colors active:bg-white/10"
                }
              >
                {bubble.bubble_label}
              </button>
            ))}
          </div>

          {showFreeText ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                handleFreeTextSubmit();
              }}
              className="mt-4 flex flex-col gap-3"
            >
              <input
                type="text"
                value={freeTextValue}
                onChange={(event) => setFreeTextValue(event.target.value)}
                maxLength={200}
                // biome-ignore lint/a11y/noAutofocus: continuation of the user's tap gesture — they just tapped "type it", so focus the field to raise the keyboard
                autoFocus
                placeholder="Type what you're into…"
                aria-label="Type your own interest"
                className="w-full rounded-control border border-white/15 bg-white/5 px-4 py-3 font-sans text-[15px] text-text-primary placeholder:text-white/35 focus:border-white/40 focus:outline-none"
              />
              <button
                type="submit"
                disabled={freeTextValue.trim() === ""}
                className="w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity disabled:opacity-40"
              >
                Send
              </button>
            </form>
          ) : null}
        </section>
      ) : null}

      {phase === "confirm" && terminal ? (
        <InterviewConfirm
          microInterests={terminal.micro_interests}
          rootsOnlyFallback={terminal.roots_only_fallback}
          onConfirm={handleConfirm}
          onEditBack={handleBack}
        />
      ) : null}
    </div>
  );
}
