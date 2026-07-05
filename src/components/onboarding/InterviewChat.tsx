"use client";

/**
 * InterviewChat — the FSR conversational onboarding stage (slice #18) that REPLACES
 * the rejected single-select tap-through interview. It renders ONE continuous
 * scrollback: the whole conversation stays on screen (no screen swaps), answered
 * turns collapse into the user's OWN bubbles, and the live turn is a multi-select
 * chip group + a "Done →" confirm + a skip affordance + a live composer (whenever the
 * turn offers "type it"). Wired to the stateless worker (`fetchInterviewTurn`) for the
 * INTERESTS → TUNE phases; the parent persists ONLY on terminal confirm and NEVER
 * stamps `users.user_onboarded_at` here (onboarding-gate rule, 2026-06-30).
 *
 * The engine emits uniform `question` turns (text + option/skip/type_your_own bubbles)
 * with no turn-kind field, so every turn renders the SAME way: tap chips (multi-select)
 * and/or type a custom, then confirm — one exchange carries both the taps and the typed
 * text (the engine's sub-niche replay folds both). A skip chip commits an empty answer
 * immediately, which drives the engine's skip fast-forward (category-skip / build-my-feed
 * offers arrive as ordinary question turns).
 *
 * Client-local state machine (the worker holds no session):
 *   resume_prompt → (resume|restart) → loading ⇄ question → confirm
 *                                          └──────→ retry ──(retry)──┘
 * Every worker call funnels through `advance`; a failed turn lands on an in-scrollback
 * retry affordance — never a dead-end screen (spec §2). Editing ANY earlier answer
 * (its "edit" affordance) truncates the conversation to that point and re-fetches,
 * invalidating every downstream answer.
 *
 * Static-export safe: client-only (`"use client"`), `window` access is guarded inside
 * effects/handlers (never at module scope).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { clearInterviewSession, loadInterviewSession, saveInterviewSession } from "@/lib/interview/session";
import { fetchInterviewTurn } from "@/lib/interview/turnClient";
import { logger } from "@/lib/logger";
import type {
  InterviewExchange,
  InterviewQuestionTurn,
  InterviewRetryTurn,
  InterviewTerminalPayload,
  InterviewTerminalTurn,
} from "@/types/interview";

/**
 * Tap budget the progress bar targets (spec §3: ~15 taps, engine steers to terminal by
 * ~18). Purely cosmetic — the WORKER decides termination; this only paces the bar so it
 * reads as "nearly there", never as a hard gate.
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
  /**
   * Start fresh unconditionally: clear any cached transcript and never offer the resume
   * prompt. The rebuild-my-feed entry (issue #9) passes this so a stale onboarding
   * transcript can't surface "Pick up where you left off?". Default `false`.
   */
  forceRestart?: boolean;
}

/** Render the collapsed one-line summary of an answered exchange (the user's own bubble). */
function summarizeAnswer(exchange: InterviewExchange): string {
  const taps = exchange.bubbles_tapped;
  const typed = (exchange.free_text_entered ?? "").trim();
  const parts: string[] = [...taps];
  if (typed !== "") {
    parts.push(`“${typed}”`);
  }
  return parts.length > 0 ? parts.join(" · ") : "Skipped";
}

/**
 * Render the conversational interview stage as one continuous scrollback.
 */
export function InterviewChat({
  onComplete,
  fetchTurn = fetchInterviewTurn,
  forceRestart = false,
}: InterviewChatProps) {
  // The answered exchanges (the stateless-worker conversation state), set OPTIMISTICALLY
  // the moment an answer is given so the scrollback reflects it immediately (history stays
  // visible through the loading turn). Shrinks on back/edit.
  const [conversation, setConversation] = useState<InterviewExchange[]>([]);
  const [phase, setPhase] = useState<ChatPhase>("loading");
  const [questionTurn, setQuestionTurn] = useState<InterviewQuestionTurn | null>(null);
  const [retryTurn, setRetryTurn] = useState<InterviewRetryTurn | null>(null);
  const [terminal, setTerminal] = useState<InterviewTerminalTurn | null>(null);
  const [resumeCandidate, setResumeCandidate] = useState<InterviewExchange[] | null>(null);
  // Live-turn selection: tapped option labels + the composer's typed text. Both travel on
  // the SAME exchange (the engine folds taps and free text) so a typed interest is captured
  // alongside the chips.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [freeText, setFreeText] = useState("");

  // The conversation state a failed turn was fetching — so `retry` re-sends EXACTLY it.
  const pendingStateRef = useRef<InterviewExchange[]>([]);
  // In-flight guard: ignore any action while a turn request is pending so a fast double-tap
  // can't fire two turns or advance past the answer (Rule 12).
  const isBusyRef = useRef(false);
  // Run the initial start/resume decision exactly once.
  const startedRef = useRef(false);
  // The scroll viewport — pinned to the bottom as the conversation grows.
  const scrollRef = useRef<HTMLDivElement | null>(null);

  /**
   * Fetch the next turn for `nextState` and route to the resulting phase. The SINGLE seam
   * that talks to the worker — every advance (start, answer, back, edit, retry, resume)
   * funnels through it, so the busy-guard, optimistic scrollback, session-save and error
   * routing live in one place.
   */
  const advance = useCallback(
    async (nextState: InterviewExchange[]) => {
      if (isBusyRef.current) {
        return;
      }
      isBusyRef.current = true;
      pendingStateRef.current = nextState;
      // Optimistic: the scrollback reflects the answer (or the truncation) immediately.
      setConversation(nextState);
      setQuestionTurn(null);
      setSelected(new Set());
      setFreeText("");
      setPhase("loading");
      // Persist the transcript BEFORE the network call so an abandon mid-request still resumes.
      saveInterviewSession(nextState);

      try {
        const turn = await fetchTurn(nextState);
        if (turn.response_kind === "question") {
          setQuestionTurn(turn);
          setPhase("question");
          return;
        }
        if (turn.response_kind === "terminal") {
          setTerminal(turn);
          setPhase("confirm");
          logger.info("interview_reached_terminal", {
            interest_count: turn.micro_interests.length,
            roots_only_fallback: turn.roots_only_fallback,
            tap_count: nextState.length,
          });
          return;
        }
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
    if (forceRestart) {
      // Rebuild entry (issue #9): a stale transcript must neither resume nor linger.
      clearInterviewSession();
      void advance([]);
      return;
    }
    const candidate = loadInterviewSession();
    if (candidate && candidate.length > 0) {
      setResumeCandidate(candidate);
      setPhase("resume_prompt");
      logger.info("interview_resume_offered", { exchange_count: candidate.length });
      return;
    }
    void advance([]);
  }, [advance, forceRestart]);

  // Keep the scrollback pinned to the newest turn as it grows / the phase changes. The deps are
  // intentional trigger-only (the body reads just the stable ref + window), so re-pin on each turn.
  // biome-ignore lint/correctness/useExhaustiveDependencies: conversation.length + phase are trigger deps, not read in the body — the effect must re-run when a turn is added or the phase changes.
  useEffect(() => {
    const viewport = scrollRef.current;
    // Reason: `Element.scrollTo` is unimplemented in jsdom (tests) — guard so the effect is a
    // no-op there rather than throwing; in a real browser it pins the newest turn into view.
    if (!viewport || typeof viewport.scrollTo !== "function") {
      return;
    }
    const reduced =
      typeof window !== "undefined" && window.matchMedia
        ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
        : false;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: reduced ? "auto" : "smooth" });
  }, [conversation.length, phase]);

  /** Commit the live turn's answer (taps + typed text) as one exchange and advance. */
  const submitAnswer = useCallback(
    (tappedLabels: string[], typed: string | null) => {
      if (isBusyRef.current || !questionTurn) {
        return;
      }
      const exchange: InterviewExchange = {
        question_text: questionTurn.question_text,
        bubbles_offered: questionTurn.bubbles.map((offered) => offered.bubble_label),
        bubbles_tapped: tappedLabels,
        free_text_entered: typed && typed.trim() !== "" ? typed.trim() : null,
      };
      void advance([...conversation, exchange]);
    },
    [advance, conversation, questionTurn],
  );

  /** Toggle one option chip in/out of the live-turn selection. */
  const handleToggleChip = useCallback((label: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(label)) {
        next.delete(label);
      } else {
        next.add(label);
      }
      return next;
    });
  }, []);

  /** Confirm the live turn: send the selected chips + any typed text as one answer. */
  const handleSubmit = useCallback(() => {
    submitAnswer([...selected], freeText);
  }, [freeText, selected, submitAnswer]);

  /** Skip the live turn: commit an empty answer (drives the engine's skip fast-forward). */
  const handleSkip = useCallback(() => {
    submitAnswer([], null);
  }, [submitAnswer]);

  /**
   * Edit an EARLIER answer: drop it and everything after, then re-fetch the turn that
   * produced it. This invalidates every downstream answer (they were derived from the
   * answer we just discarded) and regenerates from that point (acceptance criterion).
   */
  const handleEditAt = useCallback(
    (index: number) => {
      if (isBusyRef.current) {
        return;
      }
      void advance(conversation.slice(0, index));
    },
    [advance, conversation],
  );

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
   * Confirm the terminal list → hand off for persistence. Guarded by `isBusyRef` so a fast
   * double-tap can't fire `onComplete` (and thus persistence) twice; the flag is never reset
   * here because a confirm always unmounts this stage (the parent advances past `interview`),
   * and a persist failure REMOUNTS it fresh (guard resets). The transcript cache is NOT cleared
   * here: the parent clears it only after a SUCCESSFUL mint, so a persist failure leaves a
   * resumable transcript. All terminal outputs (interests + TUNE mutes + angle prefs) are
   * forwarded so the parent can persist the full profile.
   */
  const handleConfirm = useCallback(() => {
    if (isBusyRef.current || !terminal) {
      return;
    }
    isBusyRef.current = true;
    onComplete({
      micro_interests: terminal.micro_interests,
      roots_only_fallback: terminal.roots_only_fallback,
      mute_terms: terminal.mute_terms,
      angle_preferences: terminal.angle_preferences,
    });
  }, [onComplete, terminal]);

  const progressFraction = Math.min(conversation.length / TAP_TARGET, 0.92);
  const optionBubbles = questionTurn?.bubbles.filter((bubble) => bubble.bubble_kind === "option") ?? [];
  const skipBubble = questionTurn?.bubbles.find((bubble) => bubble.bubble_kind === "skip") ?? null;
  const typeOwnBubble = questionTurn?.bubbles.find((bubble) => bubble.bubble_kind === "type_your_own") ?? null;
  const composerLive = phase === "question" && typeOwnBubble !== null;
  const canSubmit = selected.size > 0 || freeText.trim() !== "";

  if (phase === "resume_prompt") {
    return (
      <div className="flex min-h-full flex-1 flex-col bg-background text-text-primary">
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
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-1 flex-col bg-background text-text-primary">
      {/* Progress bar — paces toward the terminal (cosmetic; the worker decides the end). */}
      <div className="px-8 pt-6" aria-hidden="true">
        <div className="h-[3px] w-full overflow-hidden rounded-pill bg-white/10">
          <div
            className="h-full rounded-pill bg-primary transition-[width] duration-300"
            style={{ width: `${(phase === "confirm" ? 1 : progressFraction) * 100}%` }}
          />
        </div>
      </div>

      {/* The one continuous scrollback: every prior Q + A stays here, no screen swaps. */}
      <div ref={scrollRef} data-testid="chat-scroll" className="flex flex-1 flex-col gap-4 overflow-y-auto px-6 py-6">
        {conversation.map((exchange, index) => {
          // biome-ignore lint/suspicious/noArrayIndexKey: the transcript is append/truncate-only (never reordered), so a turn's POSITION is its stable identity — question_text can repeat.
          const key = `${index}:${exchange.question_text}`;
          return (
            <div key={key} className="flex flex-col gap-2">
              <BotBubble text={exchange.question_text} />
              <div className="flex flex-col items-end gap-1">
                <div
                  data-testid="user-bubble"
                  className="max-w-[85%] rounded-control bg-white px-4 py-2.5 text-right font-sans text-[14px] font-medium text-background"
                >
                  {summarizeAnswer(exchange)}
                </div>
                <button
                  type="button"
                  onClick={() => handleEditAt(index)}
                  className="font-mono text-[10px] tracking-wide text-text-secondary transition-opacity active:opacity-60"
                >
                  EDIT ›
                </button>
              </div>
            </div>
          );
        })}

        {phase === "question" && questionTurn ? (
          <section className="flex flex-col gap-4" data-testid="active-turn">
            <BotBubble text={questionTurn.question_text} />
            {optionBubbles.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {optionBubbles.map((bubble) => {
                  const isSelected = selected.has(bubble.bubble_label);
                  return (
                    <button
                      key={bubble.bubble_label}
                      type="button"
                      data-testid="option-chip"
                      aria-pressed={isSelected}
                      onClick={() => handleToggleChip(bubble.bubble_label)}
                      className={
                        isSelected
                          ? "rounded-pill bg-white px-4 py-2 font-sans text-[14px] font-medium text-background transition-colors"
                          : "rounded-pill border border-white/15 bg-white/5 px-4 py-2 font-sans text-[14px] text-text-primary transition-colors active:bg-white/15"
                      }
                    >
                      {bubble.bubble_label}
                    </button>
                  );
                })}
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                data-testid="confirm-turn"
                onClick={handleSubmit}
                disabled={!canSubmit}
                className="rounded-pill bg-white px-5 py-2.5 font-sans text-[14px] font-semibold text-background transition-opacity disabled:opacity-40"
              >
                Done →
              </button>
              {skipBubble ? (
                <button
                  type="button"
                  data-testid="skip-turn"
                  onClick={handleSkip}
                  className="rounded-pill border border-dashed border-white/20 px-4 py-2 font-sans text-[13px] text-text-secondary transition-colors active:bg-white/10"
                >
                  {skipBubble.bubble_label}
                </button>
              ) : null}
              {selected.size > 0 ? (
                <span className="font-mono text-[10px] tracking-wide text-text-secondary">
                  {selected.size} SELECTED
                </span>
              ) : null}
            </div>
          </section>
        ) : null}

        {phase === "loading" ? (
          <div data-testid="typing" role="status" className="flex items-center gap-1.5 px-1 py-2" aria-label="Thinking">
            <span className="h-1.5 w-1.5 animate-pulse rounded-pill bg-white/40" />
            <span className="h-1.5 w-1.5 animate-pulse rounded-pill bg-white/40" />
            <span className="h-1.5 w-1.5 animate-pulse rounded-pill bg-white/40" />
          </div>
        ) : null}

        {phase === "retry" ? (
          <section data-testid="retry-turn" className="flex flex-col items-start gap-3">
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
              className="rounded-pill bg-white px-5 py-2.5 font-sans text-[14px] font-semibold text-background transition-opacity active:opacity-70"
            >
              Retry
            </button>
          </section>
        ) : null}

        {phase === "confirm" && terminal ? (
          <section data-testid="terminal-turn" className="flex flex-col gap-3">
            <span className="font-mono text-[11px] tracking-wide text-text-secondary">HERE&apos;S WHAT WE HEARD</span>
            <h2 className="font-sans text-[17px] font-semibold leading-snug text-text-primary">
              {terminal.micro_interests.length > 0
                ? "Your feed will focus on these."
                : "We'll start you with the big picture."}
            </h2>
            {terminal.micro_interests.length > 0 ? (
              <ul className="flex flex-col gap-2" aria-label="Extracted interests">
                {terminal.micro_interests.map((interest) => (
                  <li
                    key={interest.canonical_slug}
                    className="rounded-control border border-white/12 bg-white/5 px-4 py-2.5 font-sans text-[14px] text-text-primary"
                  >
                    {interest.display_label}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="font-sans text-[13px] leading-relaxed text-text-secondary">
                You skipped through, so we&apos;ll build a broad briefing across the main categories. You can always
                sharpen it later — nothing is locked in.
              </p>
            )}
            <div className="mt-2 flex flex-col gap-3">
              <button
                type="button"
                data-testid="confirm-terminal"
                onClick={handleConfirm}
                className="w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-70"
              >
                {terminal.micro_interests.length > 0 ? "Looks good" : "Continue"}
              </button>
              {conversation.length > 0 ? (
                <button
                  type="button"
                  onClick={() => handleEditAt(conversation.length - 1)}
                  className="font-sans text-[13px] text-text-secondary underline underline-offset-4 transition-opacity active:opacity-60"
                >
                  Change an answer
                </button>
              ) : null}
            </div>
          </section>
        ) : null}
      </div>

      {/* Composer — live whenever the live turn offers "type it" (always-available typing). */}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          // Reason: gate on canSubmit so pressing Enter in an empty composer with no chips
          // selected can't commit an empty exchange (a de-facto skip) — the explicit skip chip
          // is the only path to skipping. Matches the disabled "Done →" / "Send" buttons.
          if (composerLive && canSubmit) {
            handleSubmit();
          }
        }}
        className="flex items-center gap-2 border-t border-white/10 px-6 py-4"
      >
        <input
          type="text"
          data-testid="composer-input"
          value={freeText}
          onChange={(event) => setFreeText(event.target.value)}
          disabled={!composerLive}
          maxLength={200}
          placeholder={composerLive ? (typeOwnBubble?.bubble_label ?? "Type your own…") : "Pick from the chips above"}
          aria-label="Type your own interest"
          className="flex-1 rounded-control border border-white/15 bg-white/5 px-4 py-2.5 font-sans text-[14px] text-text-primary placeholder:text-white/35 focus:border-white/40 focus:outline-none disabled:opacity-40"
        />
        <button
          type="submit"
          data-testid="composer-send"
          disabled={!composerLive || freeText.trim() === ""}
          className="rounded-control bg-white px-4 py-2.5 font-sans text-[14px] font-semibold text-background transition-opacity disabled:opacity-30"
        >
          Send
        </button>
      </form>
    </div>
  );
}

/** One left-aligned bot bubble carrying the interviewer's copy. */
function BotBubble({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-[10px] tracking-wide text-text-secondary">blip</span>
      <div
        data-testid="bot-bubble"
        className="max-w-[88%] rounded-control border border-white/10 bg-white/5 px-4 py-3 font-sans text-[15px] leading-snug text-text-primary"
      >
        {text}
      </div>
    </div>
  );
}
