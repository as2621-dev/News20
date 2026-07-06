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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { XClusterPicker } from "@/components/onboarding/XClusterPicker";
import { YoutubeChannelPicker } from "@/components/onboarding/YoutubeChannelPicker";
import { categoryBucketsFromMicroInterests } from "@/lib/feedBuckets";
import { TOP_SPLIT_TOTAL } from "@/lib/feedTopSplit";
import { clearInterviewSession, loadInterviewSession, saveInterviewSession } from "@/lib/interview/session";
import { fetchInterviewTurn } from "@/lib/interview/turnClient";
import { logger } from "@/lib/logger";
import { toggleInSet } from "@/lib/setUtils";
import type {
  InterviewClusterPick,
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

/** The three closing-arc feed axes the budget card splits the 30 daily slots across. */
type TopSplitAxis = "news" | "youtube" | "x";

/**
 * The default closing-arc split (spec: 20 news / 7 YouTube / 3 X) the budget card opens on. It
 * already sums to {@link TOP_SPLIT_TOTAL}, so a user who touches nothing ships a valid 30-split.
 * This DEFAULT is the UI's product decision; the exactly-30 invariant is single-sourced from
 * {@link TOP_SPLIT_TOTAL} (feedTopSplit) so the card and the persist gate can never disagree (Rule 7).
 */
const DEFAULT_TOP_SPLIT: Readonly<Record<TopSplitAxis, number>> = { news: 20, youtube: 7, x: 3 };

/** Human-readable label + one-line gloss for each budget axis (design copy, no emoji). */
const TOP_SPLIT_AXIS_META: ReadonlyArray<{ axis: TopSplitAxis; label: string; gloss: string }> = [
  { axis: "news", label: "News", gloss: "Reported stories across your topics" },
  { axis: "youtube", label: "YouTube", gloss: "Reels from the creators you follow" },
  { axis: "x", label: "X", gloss: "Posts from the voices you follow" },
];

/**
 * The chat's visible phases. The closing arc runs `confirm` (the extracted interests) → `budget`
 * (the ± story-budget card) → `youtube` (the channel grid, #20) → `x_clusters` (the cluster
 * checklist, #20) → `summary` (the YOUR-30 split recap) → the parent's `onComplete`.
 */
type ChatPhase =
  | "resume_prompt"
  | "loading"
  | "question"
  | "retry"
  | "confirm"
  | "budget"
  | "youtube"
  | "x_clusters"
  | "summary";

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
  // The closing-arc story budget (news/youtube/x). Opens on the 20/7/3 default and is remixed with
  // ± steppers that keep the running total ≤ 30 (never over), so the confirmed split always sums to
  // exactly TOP_SPLIT_TOTAL — the persist gate (#30) never sees a ≠30 payload from this UI.
  const [topSplit, setTopSplit] = useState<Record<TopSplitAxis, number>>({ ...DEFAULT_TOP_SPLIT });
  // The in-chat source picks (#20), captured across the YOUTUBE + X CLUSTERS phases and folded into
  // the terminal payload at "Build my 30" — persisted ONLY there, never on toggle (terminal-only invariant).
  const [youtubePicks, setYoutubePicks] = useState<string[]>([]);
  const [clusterPicks, setClusterPicks] = useState<InterviewClusterPick[]>([]);

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
    setSelected((current) => toggleInSet(current, label));
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
   * Accept the extracted interests → open the story-budget card (does NOT persist). The closing
   * arc is confirm → budget → summary; persistence fires only on "Build my 30" at the very end.
   */
  const handleAcceptInterests = useCallback(() => {
    if (isBusyRef.current || !terminal) {
      return;
    }
    setPhase("budget");
  }, [terminal]);

  const splitTotal = topSplit.news + topSplit.youtube + topSplit.x;
  const slotsRemaining = TOP_SPLIT_TOTAL - splitTotal;
  const splitIsComplete = splitTotal === TOP_SPLIT_TOTAL;

  /**
   * Nudge one budget axis by ±1. Decrement stops at 0; increment is refused when it would push the
   * running total past {@link TOP_SPLIT_TOTAL}, so the split is ALWAYS ≤ 30 and can only reach the
   * "Review" gate at exactly 30 — the UI can never emit a ≠30 split (the persist gate is the
   * authoritative backstop, not a duplicate check here).
   */
  const adjustBudget = useCallback((axis: TopSplitAxis, delta: 1 | -1) => {
    setTopSplit((current) => {
      const nextAxisValue = current[axis] + delta;
      if (nextAxisValue < 0) {
        return current;
      }
      const nextTotal = current.news + current.youtube + current.x + delta;
      if (nextTotal > TOP_SPLIT_TOTAL) {
        return current;
      }
      return { ...current, [axis]: nextAxisValue };
    });
  }, []);

  /**
   * Advance from the budget card to the in-chat YOUTUBE picker (#20) — only once the split lands on
   * exactly 30. The source pickers (youtube → x_clusters) run before the YOUR-30 recap.
   */
  const handleReviewSplit = useCallback(() => {
    if (splitTotal !== TOP_SPLIT_TOTAL) {
      return;
    }
    setPhase("youtube");
  }, [splitTotal]);

  /** Confirm the YouTube channel picks (#20) → advance to the X CLUSTERS picker (does NOT persist). */
  const handleYoutubeConfirm = useCallback((youtubeSourceIds: string[]) => {
    setYoutubePicks(youtubeSourceIds);
    setPhase("x_clusters");
  }, []);

  /** Confirm the X cluster picks (#20) → advance to the YOUR-30 recap (does NOT persist). */
  const handleClusterConfirm = useCallback((picks: InterviewClusterPick[]) => {
    setClusterPicks(picks);
    setPhase("summary");
  }, []);

  /**
   * The user's interview roots (most-wanted first) — drives the pickers' relevance sort + grouping.
   * Memoized on the terminal interests so it is a STABLE reference: the pickers wire it into their
   * load effects, so a fresh array each render would re-fire the catalog fetch on any parent re-render.
   */
  const orderedRoots = useMemo(
    () => (terminal ? categoryBucketsFromMicroInterests(terminal.micro_interests) : []),
    [terminal],
  );

  /**
   * Confirm the whole closing arc → hand off for the single terminal persist. Guarded by `isBusyRef`
   * so a fast double-tap can't fire `onComplete` (and thus persistence) twice; the flag is never
   * reset here because a confirm always unmounts this stage (the parent advances past `interview`),
   * and a persist failure REMOUNTS it fresh (guard resets). The transcript cache is NOT cleared here:
   * the parent clears it only after a SUCCESSFUL persist, so a failure leaves a resumable transcript.
   * ALL terminal outputs (interests + TUNE mutes + angle prefs + deferred skips) plus the
   * client-computed `top_split` are forwarded so the parent can persist the full profile in one call.
   */
  const handleBuildMy30 = useCallback(() => {
    if (isBusyRef.current || !terminal || splitTotal !== TOP_SPLIT_TOTAL) {
      return;
    }
    isBusyRef.current = true;
    onComplete({
      micro_interests: terminal.micro_interests,
      roots_only_fallback: terminal.roots_only_fallback,
      mute_terms: terminal.mute_terms,
      angle_preferences: terminal.angle_preferences,
      deferred_questions: terminal.deferred_questions,
      top_split: { news: topSplit.news, youtube: topSplit.youtube, x: topSplit.x },
      source_follows: { youtube_source_ids: youtubePicks, clusters: clusterPicks },
    });
  }, [onComplete, terminal, splitTotal, topSplit, youtubePicks, clusterPicks]);

  const inClosingArc =
    phase === "confirm" || phase === "budget" || phase === "youtube" || phase === "x_clusters" || phase === "summary";
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
            style={{ width: `${(inClosingArc ? 1 : progressFraction) * 100}%` }}
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
                onClick={handleAcceptInterests}
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

        {phase === "budget" ? (
          <section data-testid="budget-card" className="flex flex-col gap-4">
            <span className="font-mono text-[11px] tracking-wide text-text-secondary">YOUR 30, YOUR WAY</span>
            <h2 className="font-sans text-[17px] font-semibold leading-snug text-text-primary">
              How should your daily 30 split?
            </h2>
            <p className="font-sans text-[13px] leading-relaxed text-text-secondary">
              Every day we build you 30 stories. Dial each source up or down — the total always lands on 30.
            </p>
            <div className="flex flex-col gap-2.5">
              {TOP_SPLIT_AXIS_META.map(({ axis, label, gloss }) => (
                <BudgetStepperRow
                  key={axis}
                  label={label}
                  gloss={gloss}
                  value={topSplit[axis]}
                  canIncrement={slotsRemaining > 0}
                  canDecrement={topSplit[axis] > 0}
                  onDecrement={() => adjustBudget(axis, -1)}
                  onIncrement={() => adjustBudget(axis, 1)}
                />
              ))}
            </div>
            <div className="flex items-center justify-between font-mono text-[11px] tracking-wide">
              <span className="text-text-secondary">
                {splitIsComplete ? "ALL 30 ALLOCATED" : `${slotsRemaining} SLOT${slotsRemaining === 1 ? "" : "S"} LEFT`}
              </span>
              <span data-testid="budget-total" className="text-primary">
                {splitTotal} / {TOP_SPLIT_TOTAL}
              </span>
            </div>
            <button
              type="button"
              data-testid="budget-review"
              onClick={handleReviewSplit}
              disabled={!splitIsComplete}
              className="mt-1 w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity disabled:opacity-40"
            >
              Review my 30 →
            </button>
          </section>
        ) : null}

        {phase === "youtube" ? (
          <YoutubeChannelPicker orderedRoots={orderedRoots} onConfirm={handleYoutubeConfirm} />
        ) : null}

        {phase === "x_clusters" ? (
          <XClusterPicker orderedRoots={orderedRoots} onConfirm={handleClusterConfirm} />
        ) : null}

        {phase === "summary" ? (
          <section data-testid="your-30-summary" className="flex flex-col gap-3">
            <span className="font-mono text-[11px] tracking-wide text-text-secondary">YOUR 30</span>
            <h2 className="font-sans text-[17px] font-semibold leading-snug text-text-primary">
              Here&apos;s your daily 30.
            </h2>
            <ul className="flex flex-col gap-2" aria-label="Your daily 30 split">
              {TOP_SPLIT_AXIS_META.map(({ axis, label }) => (
                <li
                  key={axis}
                  data-testid={`summary-${axis}`}
                  className="flex items-center justify-between gap-3 rounded-control border border-white/12 bg-white/5 px-4 py-2.5 font-sans text-[14px] text-text-primary"
                >
                  <span className="flex flex-col gap-0.5">
                    <span>{label}</span>
                    {axis === "youtube" && youtubePicks.length > 0 ? (
                      <span data-testid="summary-youtube-picks" className="font-sans text-[11px] text-text-secondary">
                        from {youtubePicks.length} channel{youtubePicks.length === 1 ? "" : "s"} you picked
                      </span>
                    ) : null}
                    {axis === "x" && clusterPicks.length > 0 ? (
                      <span data-testid="summary-x-picks" className="font-sans text-[11px] text-text-secondary">
                        from {clusterPicks.length} cluster{clusterPicks.length === 1 ? "" : "s"} you follow
                      </span>
                    ) : null}
                  </span>
                  <span className="font-mono text-[13px] text-primary">{topSplit[axis]}</span>
                </li>
              ))}
            </ul>
            <div className="mt-2 flex flex-col gap-3">
              <button
                type="button"
                data-testid="build-my-30"
                onClick={handleBuildMy30}
                className="w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-70"
              >
                Build my 30 →
              </button>
              <button
                type="button"
                onClick={() => setPhase("budget")}
                className="font-sans text-[13px] text-text-secondary underline underline-offset-4 transition-opacity active:opacity-60"
              >
                Adjust the split
              </button>
            </div>
          </section>
        ) : null}
      </div>

      {/* Composer — live whenever the live turn offers "type it" (always-available typing). Hidden
          across the closing arc (confirm → budget → summary): there is nothing to type there, so a
          dead disabled input would only clutter the story-budget card. */}
      {inClosingArc ? null : (
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
      )}
    </div>
  );
}

/** Props for one story-budget stepper row in the closing-arc budget card. */
interface BudgetStepperRowProps {
  /** The axis label (e.g. "News"). */
  label: string;
  /** A one-line gloss under the label. */
  gloss: string;
  /** The current slot count for this axis. */
  value: number;
  /** Whether the + control is enabled (there are unallocated slots left). */
  canIncrement: boolean;
  /** Whether the − control is enabled (this axis is above 0). */
  canDecrement: boolean;
  /** Decrement this axis by one. */
  onDecrement: () => void;
  /** Increment this axis by one. */
  onIncrement: () => void;
}

/** One ± stepper row (label + gloss + −/count/+) for a single budget axis. */
function BudgetStepperRow({
  label,
  gloss,
  value,
  canIncrement,
  canDecrement,
  onDecrement,
  onIncrement,
}: BudgetStepperRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-control border border-white/12 bg-white/5 px-4 py-3">
      <div className="flex flex-col gap-0.5">
        <span className="font-sans text-[14px] font-medium text-text-primary">{label}</span>
        <span className="font-sans text-[11px] leading-snug text-text-secondary">{gloss}</span>
      </div>
      <div className="flex items-center gap-3">
        <button
          type="button"
          aria-label={`Fewer ${label}`}
          data-testid={`budget-dec-${label.toLowerCase()}`}
          onClick={onDecrement}
          disabled={!canDecrement}
          className="flex h-8 w-8 items-center justify-center rounded-pill border border-white/20 font-sans text-[18px] leading-none text-text-primary transition-opacity active:bg-white/10 disabled:opacity-30"
        >
          −
        </button>
        <span
          data-testid={`budget-value-${label.toLowerCase()}`}
          className="w-6 text-center font-mono text-[15px] tabular-nums text-text-primary"
        >
          {value}
        </span>
        <button
          type="button"
          aria-label={`More ${label}`}
          data-testid={`budget-inc-${label.toLowerCase()}`}
          onClick={onIncrement}
          disabled={!canIncrement}
          className="flex h-8 w-8 items-center justify-center rounded-pill border border-white/20 font-sans text-[18px] leading-none text-text-primary transition-opacity active:bg-white/10 disabled:opacity-30"
        >
          +
        </button>
      </div>
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
