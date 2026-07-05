"use client";

/**
 * OnboardingFlow — the client state machine wiring the conversational interview
 * (FSR slice #4) into the onboarding flow (supersedes the roots-only topic picker).
 *
 * Step order (spec §1): `splash → email → wait_session → interview → loading → sources`.
 * The interview's CLOSING ARC (the in-chat story-budget card + YOUR-30 summary, #19) captures the
 * feed split and the terminal persist writes the allocation, so there is no separate "build" step —
 * "Build my 30" happens inside the chat.
 *   1. `splash`   — {@link OnboardingSplash}; "get started" → `email` (or straight
 *      to `interview` when a session already exists — signed-in users never re-auth).
 *   2. `email`    — {@link EmailSignIn}; its `onSent` advances to waiting-for-session.
 *      The magic-link callback (a separate `/callback` page) establishes the
 *      session on this device; the flow watches `getCurrentSession` +
 *      `onAuthStateChange` and moves to `interview` once a session exists. If the user
 *      is ALREADY signed in when they reach `email` (re-onboarding), we skip
 *      straight to `interview`.
 *   3. `interview` — {@link InterviewChat}; the dark-editorial chat interview that
 *      REPLACES the picker. Tap-through bubbles → a confirm screen; on confirm it hands
 *      back an {@link InterviewTerminalPayload}. It is **skippable** (spec §5) — a
 *      skip-everything roots-only payload is valid and not punished.
 *   4. `loading`  — calls {@link persistOnboardingTerminal} (scoped to the session user):
 *      the ONE terminal persist — interests + mutes + the budget-card allocation + deferred
 *      skips — in a single call. On success → the `sources` step. Any rejected micro-interests
 *      (backstop-invalid) are surfaced inline (Rule 12 — not silently dropped). Persistence
 *      fires ONLY on the closing-arc confirm (no half-profiles).
 *   5. `sources`  — {@link SourceClusterScreen}; the M6 source/cluster onboarding step
 *      (Phase FSR-M6a). It loads the chosen categories' resolved clusters (no-dup
 *      applied), renders the opt-out cluster/member grid, and on continue commits the
 *      resolved follow set to `user_content_sources`/`user_personalities`. It then
 *      marks the source step complete ({@link markSourceOnboardingComplete}), then stamps
 *      onboarding genuinely complete ({@link markOnboardingComplete}, the TRUE flow end) and
 *      routes to the reel. A returning user who already completed the source step skips it
 *      (gated in `onboarding/page.tsx` via {@link isSourceOnboardingComplete}).
 *
 * Static-export safe: client-only (`"use client"`), `window`-guarded, no
 * `useSearchParams` (the magic link uses the URL hash, handled in `/callback`).
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { EmailSignIn } from "@/components/onboarding/EmailSignIn";
import { InterviewChat } from "@/components/onboarding/InterviewChat";
import { OnboardingSplash } from "@/components/onboarding/OnboardingSplash";
import { OtpCodeEntry } from "@/components/onboarding/OtpCodeEntry";
import { SourceClusterScreen } from "@/components/sources/SourceClusterScreen";
import { resolveRootGate } from "@/lib/auth/routeGuard";
import { categoryBucketsFromMicroInterests, type DesignBucketId } from "@/lib/feedBuckets";
import { clearInterviewSession } from "@/lib/interview/session";
import { logger } from "@/lib/logger";
import {
  isSourceOnboardingComplete,
  markOnboardingComplete,
  markSourceOnboardingComplete,
} from "@/lib/onboardingProfile";
import { persistOnboardingTerminal } from "@/lib/onboardingTerminal";
import { getCurrentSession, TEST_AUTH_CODE, TEST_AUTH_MODE } from "@/lib/supabase/auth";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { InterviewTerminalPayload } from "@/types/interview";

/** The ordered onboarding steps (spec §1: the interview + its in-chat budget card replace the picker). */
type OnboardingStep = "splash" | "email" | "wait_session" | "interview" | "loading" | "sources";

/**
 * Dev-only bypass: skip the email/magic-link auth gate and drop straight into the
 * interview, with interest-persistence no-op'd (there is no session to scope the
 * RLS writes to, so we deliberately write nothing). Enabled ONLY when
 * `NEXT_PUBLIC_ONBOARDING_SKIP_AUTH=true` — leave UNSET in production. NEXT_PUBLIC_*
 * vars are inlined at build time, so a build is required to flip this.
 *
 * Reason: lets the onboarding UI (interview + its budget card → sources) be walked locally
 * before email auth is configured, without ever persisting un-scoped data (Rule 12).
 */
const SKIP_AUTH = process.env.NEXT_PUBLIC_ONBOARDING_SKIP_AUTH === "true";

/**
 * Render the onboarding flow state machine.
 */
export function OnboardingFlow() {
  const router = useRouter();
  // Dev bypass starts straight in the interview; normal flow starts at splash.
  const [step, setStep] = useState<OnboardingStep>(SKIP_AUTH ? "interview" : "splash");
  const [persistError, setPersistError] = useState<string | null>(null);
  const [unpersistedFollows, setUnpersistedFollows] = useState<string[]>([]);
  // The email the magic-link/code email was sent to — the wait_session step's
  // OtpCodeEntry needs it (verifyOtp takes email + code).
  const [sentEmail, setSentEmail] = useState("");
  // The category buckets the confirmed interests touch, derived from their canonical slugs.
  // Captured in `handleInterviewComplete` so the `sources` step loads ONLY those categories'
  // clusters. Empty (interview skipped) → the source step falls back to the broad roots set.
  const [selectedCategoryBuckets, setSelectedCategoryBuckets] = useState<DesignBucketId[]>([]);
  // Hold the established session's user id so `loading` can scope the writes.
  const sessionUserIdRef = useRef<string | null>(null);

  /**
   * Session just established (magic link or OTP code): an ALREADY-onboarded user
   * (`user_onboarded_at` set — e.g. signing in on a new device) goes straight to
   * the reel; only a not-yet-onboarded user enters the interview. Without this gate
   * a returning user would silently re-onboard and overwrite their follows.
   */
  const handleSessionEstablished = useCallback(
    async (sessionUserId: string) => {
      sessionUserIdRef.current = sessionUserId;
      const decision = await resolveRootGate();
      if (decision === "reel") {
        logger.info("onboarding_signed_in_already_onboarded", { user_id: sessionUserId });
        router.replace("/");
        return;
      }
      setStep("interview");
    },
    [router],
  );

  // Once on the `wait_session` step, advance as soon as a session exists (the
  // callback page or the OTP code entry may establish it in this/another tab).
  useEffect(() => {
    if (step !== "wait_session") {
      return;
    }
    if (typeof window === "undefined") {
      return;
    }
    const supabase = getSupabaseBrowserClient();
    let isMounted = true;

    void getCurrentSession(supabase).then((session) => {
      if (isMounted && session) {
        void handleSessionEstablished(session.user.id);
      }
    });

    const { data: authSubscription } = supabase.auth.onAuthStateChange((_authEvent, session) => {
      if (isMounted && session) {
        void handleSessionEstablished(session.user.id);
      }
    });

    return () => {
      isMounted = false;
      authSubscription.subscription.unsubscribe();
    };
  }, [step, handleSessionEstablished]);

  /**
   * Splash "get started": an already-signed-in user (re-onboarding, or a session
   * established outside the email step — e.g. a restored session) skips the email
   * sign-in entirely and goes straight to the interview. A signed-in user must never
   * be asked to sign in again.
   */
  const handleGetStarted = useCallback(async () => {
    if (typeof window !== "undefined") {
      const session = await getCurrentSession();
      if (session) {
        sessionUserIdRef.current = session.user.id;
        logger.info("onboarding_splash_session_skip", { user_id: session.user.id });
        setStep("interview");
        return;
      }
    }
    setStep("email");
  }, []);

  /** Email link sent: if already authed, skip the wait; else wait for the session. */
  const handleEmailSent = useCallback(async () => {
    if (typeof window === "undefined") {
      return;
    }
    const session = await getCurrentSession();
    if (session) {
      sessionUserIdRef.current = session.user.id;
      setStep("interview");
      return;
    }
    setStep("wait_session");
  }, []);

  /**
   * Stamp onboarding genuinely COMPLETE, then route to the reel. This is the SINGLE
   * place `users.user_onboarded_at` is written in the flow (the interview + its budget
   * card and the source step never stamp it) — so the root gate only routes to the reel
   * once the user has actually reached the TRUE flow end (onboarding-gate rule, 2026-06-30).
   * A stamp failure is surfaced (Rule 12) but still routes: the user finished onboarding,
   * and the gate self-heals on the next read.
   */
  const finishOnboarding = useCallback(async () => {
    const userId = sessionUserIdRef.current;
    if (!userId) {
      // Dev bypass (SKIP_AUTH) — no session to scope the stamp to; just route.
      router.push("/");
      return;
    }
    try {
      await markOnboardingComplete(userId);
    } catch (error) {
      logger.error("onboarding_complete_stamp_failed", {
        error_message: error instanceof Error ? error.message : "unknown",
        fix_suggestion: "Routing to the reel anyway; confirm users update-self RLS permits the write.",
      });
    }
    router.push("/");
  }, [router]);

  /**
   * Complete the interview's closing arc: persist the confirmed terminal payload — interests,
   * mutes, the budget-card allocation, and deferred skips — in ONE call
   * ({@link persistOnboardingTerminal}), then advance to the source step. Persistence fires ONLY
   * here (on the "Build my 30" confirm) — no half-profiles — and NEVER stamps `user_onboarded_at`
   * (owned solely by {@link finishOnboarding} at the true flow end; onboarding-gate rule 2026-06-30).
   *
   * The `top_split` on the payload is the client-computed budget-card split; the orchestrator
   * rejects any split ≠ 30 BEFORE any write, so a torn allocation can never land. Unlike the retired
   * picker, the interview is SKIPPABLE (spec §5): a skip-everything roots-only payload (empty
   * interests, default 20/7/3 budget) persists cleanly (feed falls back to broad categories across
   * all 8 roots) and STILL advances — skipping is not punished. A persist failure keeps the
   * resumable transcript (InterviewChat did not clear it) and returns to the interview to retry.
   */
  const handleInterviewComplete = useCallback(
    async (payload: InterviewTerminalPayload) => {
      // Capture which CATEGORY blocks the confirmed interests touch so the source step loads only
      // those clusters (an empty set — skip-everything — falls back to the broad roots seed). One
      // root→bucket fold, single-sourced in feedBuckets (Rule 7; #30 dedupe residual).
      setSelectedCategoryBuckets(categoryBucketsFromMicroInterests(payload.micro_interests));

      const userId = sessionUserIdRef.current;
      if (!userId) {
        if (SKIP_AUTH) {
          // Dev bypass: no session to scope writes to — persist NOTHING (Rule 12),
          // just advance the UI so the rest of the flow can be walked locally.
          logger.info("onboarding_skip_auth_no_persist", {
            interest_count: payload.micro_interests.length,
            fix_suggestion: "Dev bypass only; unset NEXT_PUBLIC_ONBOARDING_SKIP_AUTH for real onboarding.",
          });
          clearInterviewSession();
          setPersistError(null);
          if (isSourceOnboardingComplete()) {
            void finishOnboarding();
          } else {
            setStep("sources");
          }
          return;
        }
        // Defensive: we should only reach `interview` with a session, but never write
        // un-scoped interests (Rule 12). Send the user back to sign in.
        logger.error("onboarding_complete_without_session", {
          fix_suggestion: "A session must exist before persisting interests; returning to email step.",
        });
        setPersistError("Your session expired — please sign in again.");
        setStep("email");
        return;
      }
      setPersistError(null);
      setStep("loading");
      try {
        // ONE terminal persist: interests + mutes + budget-card allocation + deferred skips, in
        // destructive-last order, scoped to the authed user. First-run onboarding, so no replace.
        const result = await persistOnboardingTerminal(userId, payload);
        // Rejected micro-interests (backstop-invalid) are surfaced via structured logs, never
        // silently minted (Rule 12) — the rest still persist. Stash the slugs for the loading note.
        if (result.interests.rejected_interests.length > 0) {
          setUnpersistedFollows(result.interests.rejected_interests.map((rejected) => rejected.canonical_slug));
        }
        logger.info("onboarding_completed", {
          minted_interest_count: result.interests.minted_interest_count,
          rejected_count: result.interests.rejected_interests.length,
          allocation_persisted_count: result.allocation.persisted_count,
          roots_only_fallback: payload.roots_only_fallback ?? false,
        });
        // The confirmed profile is now persisted — the resumable transcript is stale.
        clearInterviewSession();
        // A returning user who already finished the source step goes straight to the reel (still
        // stamping at the true end); everyone else runs the source step before the reel.
        if (isSourceOnboardingComplete()) {
          void finishOnboarding();
        } else {
          setStep("sources");
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "Couldn't save your interests.";
        logger.error("onboarding_persist_failed", {
          error_message: message,
          fix_suggestion: "Retry; if it persists confirm migration 0025/0030 + RLS permit the owner write.",
        });
        // Keep the resumable transcript (not cleared) so the retry re-confirms cleanly.
        setPersistError(message);
        setStep("interview");
      }
    },
    [finishOnboarding],
  );

  /** Complete the source/cluster step: mark it done, then stamp onboarding complete + route to the reel. */
  const handleSourcesDone = useCallback(() => {
    logger.info("source_onboarding_completed", {});
    markSourceOnboardingComplete();
    void finishOnboarding();
  }, [finishOnboarding]);

  return (
    <main
      className="flex min-h-dvh w-full flex-col bg-background text-text-primary"
      style={{
        // Reason: with viewport-fit=cover the page extends under the Dynamic Island /
        // home indicator on iOS; pad by the real insets so onboarding chrome (the
        // wordmark, CTAs) never clips behind them. 0px in a plain browser.
        paddingTop: "env(safe-area-inset-top)",
        paddingBottom: "env(safe-area-inset-bottom)",
      }}
    >
      {step === "splash" ? <OnboardingSplash onGetStarted={() => void handleGetStarted()} /> : null}

      {step === "email" ? (
        <EmailSignIn
          onSent={(email) => {
            setSentEmail(email);
            void handleEmailSent();
          }}
          onHaveCode={(email) => {
            // A still-valid code from an earlier email signs in without a fresh
            // send (the 2/hr mailer cap must not lock the user out).
            setSentEmail(email);
            setStep("wait_session");
          }}
        />
      ) : null}

      {step === "wait_session" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-3 px-10 text-center">
          <p className="font-sans text-[15px] font-semibold text-text-primary">
            {TEST_AUTH_MODE ? "Enter your code" : "Check your inbox"}
          </p>
          <p className="font-sans text-[13px] leading-relaxed text-text-secondary">
            {TEST_AUTH_MODE
              ? `Test mode — enter the code ${TEST_AUTH_CODE} to continue.`
              : "Enter the code from the email below — or tap the magic link on this device."}
          </p>
          <OtpCodeEntry email={sentEmail} />
          <span className="mt-2 font-mono text-[10px] tracking-wide text-white/40">WAITING FOR SIGN-IN…</span>
        </section>
      ) : null}

      {step === "interview" ? (
        <div className="flex min-h-full flex-1 flex-col">
          {persistError ? (
            <p role="alert" className="px-6 pt-3 font-mono text-[11px] tracking-wide text-seg-wildcard">
              {persistError}
            </p>
          ) : null}
          <InterviewChat onComplete={(payload) => void handleInterviewComplete(payload)} />
        </div>
      ) : null}

      {step === "loading" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-3 px-10 text-center">
          <span className="font-mono text-[11px] tracking-wide text-text-secondary">SETTING UP YOUR BRIEFING…</span>
          {unpersistedFollows.length > 0 ? (
            <p className="font-sans text-[12px] leading-relaxed text-text-secondary">
              We couldn&apos;t match {unpersistedFollows.join(", ")} yet — we saved everything else and we&apos;ll add
              it soon.
            </p>
          ) : null}
        </section>
      ) : null}

      {step === "sources" ? (
        <SourceClusterScreen categories={selectedCategoryBuckets} onDone={handleSourcesDone} />
      ) : null}
    </main>
  );
}
