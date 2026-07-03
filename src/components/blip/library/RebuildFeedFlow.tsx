"use client";

/**
 * RebuildFeedFlow — the "Rebuild my feed" entry point (issue #9, PRD stories #18/#19).
 * Launched from the Settings tab ({@link import("../reel/SettingsLayer").SettingsLayer}),
 * it re-runs the SAME chat interview an onboarding user gets ({@link InterviewChat},
 * `forceRestart` so a stale transcript never resumes) and, on terminal confirm,
 * REPLACES the user's interest profile via `persistInterviewInterests` with
 * `replace_existing: true` — old and new profiles never blend; the next daily
 * allocation/feed builds from the new micro-interest rows.
 *
 * Invariants (issue #9 acceptance criteria):
 *  - Persistence fires ONLY on a terminal confirm carrying at least one interest.
 *    Abandoning mid-interview (✕ Cancel) or confirming an EMPTY list writes nothing —
 *    the previous profile and feed stay completely untouched (an empty rebuild must
 *    never wipe a working profile).
 *  - First-run onboarding side effects NEVER re-fire here: `user_onboarded_at` is
 *    already set and stays set (this flow never touches it), and sources/follows
 *    are untouched (no source step, no follow writes).
 *  - A failed save surfaces a RETRY. A failure BEFORE the new rows landed leaves the
 *    old profile intact ("keep my current feed" is honest); a failure AFTER they
 *    landed (`REPLACE_PARTIAL_ERROR_NAME`) is a retryable old∪new superset — the copy
 *    says so instead of claiming "untouched", and the retry finishes the replace
 *    idempotently. Either way the user always has a working feed.
 *
 * Rendered as a full-screen overlay inside the library surface (`absolute inset-0`,
 * z-70 — the same stacking the sources add-interest overlay uses).
 */

import { useCallback, useRef, useState } from "react";
import { InterviewChat } from "@/components/onboarding/InterviewChat";
import { clearInterviewSession } from "@/lib/interview/session";
import { persistInterviewInterests, REPLACE_PARTIAL_ERROR_NAME } from "@/lib/interviewProfile";
import { logger } from "@/lib/logger";
import { getCurrentSession } from "@/lib/supabase/auth";
import type { InterviewTerminalPayload } from "@/types/interview";

/** The rebuild flow's visible phases. */
type RebuildPhase = "interview" | "saving" | "error" | "done";

/** What the done screen reports: the profile was replaced, or kept (empty confirm). */
type RebuildOutcome = "replaced" | "kept";

export interface RebuildFeedFlowProps {
  /** Close the flow and return to Settings (abandon, bail-out, or after done). */
  onClose: () => void;
}

/**
 * Render the rebuild-my-feed flow: interview → replace-persist → done/error.
 */
export function RebuildFeedFlow({ onClose }: RebuildFeedFlowProps) {
  const [phase, setPhase] = useState<RebuildPhase>("interview");
  const [outcome, setOutcome] = useState<RebuildOutcome>("replaced");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // A post-upsert failure: the new rows landed, the stale cleanup didn't — the error
  // copy must NOT claim the profile is untouched (review-panel finding, issue #9).
  const [isPartialFailure, setIsPartialFailure] = useState(false);
  // Backstop-rejected interests (worker-bug edge) — surfaced on the done screen, not
  // just logged (Rule 12; mirrors OnboardingFlow's unpersisted note).
  const [rejectedCount, setRejectedCount] = useState(0);
  // The confirmed terminal payload — held so a failed save can RETRY the exact same
  // replace without re-running the interview.
  const payloadRef = useRef<InterviewTerminalPayload | null>(null);
  // In-flight guard: a double-fired confirm/retry must not run two replaces.
  const isPersistingRef = useRef(false);

  /** Run (or re-run) the replace-persist for the confirmed payload. */
  const runReplace = useCallback(async () => {
    const payload = payloadRef.current;
    if (!payload || isPersistingRef.current) {
      return;
    }
    isPersistingRef.current = true;
    setPhase("saving");
    try {
      const session = await getCurrentSession();
      if (!session) {
        throw new Error("Your session expired — sign in again to rebuild your feed.");
      }
      const result = await persistInterviewInterests(session.user.id, payload, { replace_existing: true });
      // The profile is replaced — the cached transcript is stale.
      clearInterviewSession();
      logger.info("rebuild_feed_completed", {
        minted_interest_count: result.minted_interest_count,
        rejected_count: result.rejected_interests.length,
      });
      setRejectedCount(result.rejected_interests.length);
      setOutcome("replaced");
      setPhase("done");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Couldn't save your new interests.";
      const isPartial = error instanceof Error && error.name === REPLACE_PARTIAL_ERROR_NAME;
      logger.error("rebuild_feed_persist_failed", {
        error_message: message,
        is_partial: isPartial,
        fix_suggestion: isPartial
          ? "New rows landed but stale cleanup is pending; retry to finish the replace."
          : "Retry the confirm; the old profile is still live so the feed keeps working.",
      });
      setIsPartialFailure(isPartial);
      setErrorMessage(message);
      setPhase("error");
    } finally {
      isPersistingRef.current = false;
    }
  }, []);

  /**
   * Terminal confirm from the interview. An EMPTY confirmed list (skip-through) is
   * treated as "keep my current profile" — persisting a replace with an empty set
   * would wipe a working profile, the one thing a rebuild must never do.
   */
  const handleInterviewComplete = useCallback(
    (payload: InterviewTerminalPayload) => {
      if (payload.micro_interests.length === 0) {
        clearInterviewSession();
        logger.info("rebuild_feed_empty_confirm_kept_old", {});
        setOutcome("kept");
        setPhase("done");
        return;
      }
      payloadRef.current = payload;
      void runReplace();
    },
    [runReplace],
  );

  /**
   * Abandon without persisting — the previous profile and feed stay untouched
   * (nothing was written; persistence fires only on confirm). The partial
   * transcript is dropped so it never resurfaces elsewhere.
   */
  const handleAbandon = useCallback(() => {
    clearInterviewSession();
    logger.info("rebuild_feed_abandoned", {});
    onClose();
  }, [onClose]);

  return (
    <div data-testid="rebuild-flow" className="absolute inset-0 z-[70] flex flex-col bg-background text-text-primary">
      {phase === "interview" ? (
        <>
          <div className="flex items-center justify-between px-6 pt-4">
            <span className="font-mono text-[11px] tracking-wide text-text-secondary">REBUILD MY FEED</span>
            <button
              type="button"
              onClick={handleAbandon}
              className="font-mono text-[11px] tracking-wide text-text-secondary underline underline-offset-4 transition-opacity active:opacity-60"
            >
              ✕ Cancel
            </button>
          </div>
          <InterviewChat forceRestart onComplete={handleInterviewComplete} />
        </>
      ) : null}

      {phase === "saving" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-3 px-10 text-center">
          <span className="font-mono text-[11px] tracking-wide text-text-secondary">REBUILDING YOUR PROFILE…</span>
        </section>
      ) : null}

      {phase === "error" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-4 px-10 text-center">
          <span className="font-mono text-[11px] tracking-wide text-seg-wildcard">COULDN'T SAVE</span>
          <p role="alert" className="font-sans text-[14px] leading-relaxed text-text-primary">
            {errorMessage ?? "Something went wrong saving your new interests."}
          </p>
          <p className="font-sans text-[12px] leading-relaxed text-text-secondary">
            {isPartialFailure
              ? "Your new interests were saved, but some old ones may still be attached — retry to finish the cleanup."
              : "Your current feed is untouched — retry, or keep it as is."}
          </p>
          <button
            type="button"
            onClick={() => void runReplace()}
            className="mt-2 w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-70"
          >
            Retry
          </button>
          <button
            type="button"
            onClick={handleAbandon}
            className="font-sans text-[13px] text-text-secondary underline underline-offset-4 transition-opacity active:opacity-60"
          >
            {isPartialFailure ? "Close — I'll retry later" : "Keep my current feed"}
          </button>
        </section>
      ) : null}

      {phase === "done" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-4 px-10 text-center">
          <span className="font-mono text-[11px] tracking-wide text-text-secondary">
            {outcome === "replaced" ? "ALL SET" : "NO CHANGES"}
          </span>
          <h1 className="font-sans text-[18px] font-semibold text-text-primary">
            {outcome === "replaced" ? "Profile rebuilt" : "Your feed stays as is"}
          </h1>
          <p className="font-sans text-[13px] leading-relaxed text-text-secondary">
            {outcome === "replaced"
              ? "Your next briefing builds from your new interests."
              : "You didn't pick anything new, so we kept your current interests."}
          </p>
          {outcome === "replaced" && rejectedCount > 0 ? (
            <p className="font-sans text-[12px] leading-relaxed text-seg-wildcard">
              We couldn't keep {rejectedCount} of your picks — everything else is saved.
            </p>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            className="mt-2 w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-70"
          >
            Done
          </button>
        </section>
      ) : null}
    </div>
  );
}
