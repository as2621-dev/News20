"use client";

/**
 * OnboardingFlow — the client state machine wiring the recursive interest picker
 * into the onboarding flow (Phase 5 SP4; supersedes phase-1e's `InterestChips`).
 *
 * Step order (phase DoD): `splash → email → picker → loading → reel`.
 *   1. `splash`   — {@link OnboardingSplash}; "get started" → `email` (or straight
 *      to `picker` when a session already exists — signed-in users never re-auth).
 *   2. `email`    — {@link EmailSignIn}; its `onSent` advances to waiting-for-session.
 *      The magic-link callback (a separate `/callback` page) establishes the
 *      session on this device; the flow watches `getCurrentSession` +
 *      `onAuthStateChange` and moves to `picker` once a session exists. If the user
 *      is ALREADY signed in when they reach `email` (re-onboarding), we skip
 *      straight to `picker`.
 *   3. `picker`   — {@link TopicTree}; the dark-editorial Blip topic-tree picker
 *      (replaces the archived `OnboardingPicker`). It is **skippable** (spec §10/§11)
 *      — NO "pick ≥1" gate; Done is always enabled and hands back `store.all()`
 *      (an empty array on a skip).
 *   4. `loading`  — calls {@link persistPickerFollows} (scoped to the session user).
 *      On success → the `sources` step (the source swipe deck). Any unpersisted
 *      follows (free-text customs / unmatched topics) are surfaced inline (Rule 12 —
 *      not silently dropped). A zero-follow completion persists nothing (no error)
 *      and still advances to the source swipe.
 *   5. `sources`  — {@link SourceSwipe}; the Tinder-style source-onboarding deck
 *      (Phase 5c). On its final "You're all set." it marks the source step complete
 *      ({@link markSourceOnboardingComplete}) and advances to the `build` step.
 *      A returning user who already completed the source step skips it (gated in
 *      `onboarding/page.tsx` via {@link isSourceOnboardingComplete}).
 *   6. `build`    — {@link BuildYour30}; the Blip Flow Stage 3 "Build your 30, in order"
 *      feed-allocation screen. On "Save this order →" it persists the allocation
 *      ({@link saveUserFeedAllocation}, inside the component) and routes to the reel
 *      (`router.push("/")`). It is **skippable** — "I'll do this later" routes to the
 *      reel WITHOUT saving (the Python allocator has a balanced default for users with
 *      no allocation — phase-5a).
 *
 * Static-export safe: client-only (`"use client"`), `window`-guarded, no
 * `useSearchParams` (the magic link uses the URL hash, handled in `/callback`).
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { BuildYour30, type BuildYour30Segment } from "@/components/onboarding/BuildYour30";
import { EmailSignIn } from "@/components/onboarding/EmailSignIn";
import { OnboardingSplash } from "@/components/onboarding/OnboardingSplash";
import { OtpCodeEntry } from "@/components/onboarding/OtpCodeEntry";
import { TopicTree } from "@/components/onboarding/TopicTree";
import { SourceSwipe } from "@/components/sources/SourceSwipe";
import { resolveRootGate } from "@/lib/auth/routeGuard";
import { categoryBucketsFromFollows, type DesignBucketId, sourceBucketsFromFollows } from "@/lib/feedBuckets";
import { logger } from "@/lib/logger";
import {
  isSourceOnboardingComplete,
  markSourceOnboardingComplete,
  persistPickerFollows,
} from "@/lib/onboardingProfile";
import { getFollowedSources } from "@/lib/sources";
import { getCurrentSession, TEST_AUTH_CODE, TEST_AUTH_MODE } from "@/lib/supabase/auth";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { FollowSelection } from "@/types/picker";

/** The ordered onboarding steps (phase DoD names this exact sequence). */
type OnboardingStep = "splash" | "email" | "wait_session" | "picker" | "loading" | "sources" | "build";

/**
 * Dev-only bypass: skip the email/magic-link auth gate and drop straight into the
 * interest picker, with follow-persistence no-op'd (there is no session to scope the
 * RLS writes to, so we deliberately write nothing). Enabled ONLY when
 * `NEXT_PUBLIC_ONBOARDING_SKIP_AUTH=true` — leave UNSET in production. NEXT_PUBLIC_*
 * vars are inlined at build time, so a build is required to flip this.
 *
 * Reason: lets the onboarding UI (picker → sources → build) be walked locally before
 * email auth is configured, without ever persisting un-scoped data (Rule 12).
 */
const SKIP_AUTH = process.env.NEXT_PUBLIC_ONBOARDING_SKIP_AUTH === "true";

/**
 * Render the onboarding flow state machine.
 */
export function OnboardingFlow() {
  const router = useRouter();
  // Dev bypass starts straight in the interest picker; normal flow starts at splash.
  const [step, setStep] = useState<OnboardingStep>(SKIP_AUTH ? "picker" : "splash");
  const [persistError, setPersistError] = useState<string | null>(null);
  const [unpersistedFollows, setUnpersistedFollows] = useState<string[]>([]);
  // The email the magic-link/code email was sent to — the wait_session step's
  // OtpCodeEntry needs it (verifyOtp takes email + code).
  const [sentEmail, setSentEmail] = useState("");
  // The category buckets the user picked, derived from their picker selections. Captured in
  // `handleComplete` so the later `build` step ("Build your 30") can seed ONLY those category
  // blocks. Empty (picker skipped) → the build screen falls back to the full default seed.
  const [selectedCategoryBuckets, setSelectedCategoryBuckets] = useState<DesignBucketId[]>([]);
  // The source buckets the user actually follows, derived from their source swipe in
  // `handleSourcesDone` so the `build` step seeds + offers ONLY backed source blocks. Empty
  // (no sources followed / read failed) → no source blocks appear (never a phantom block).
  const [followedSourceBuckets, setFollowedSourceBuckets] = useState<DesignBucketId[]>([]);
  // Hold the established session's user id so `loading` can scope the writes.
  const sessionUserIdRef = useRef<string | null>(null);

  /**
   * Session just established (magic link or OTP code): an ALREADY-onboarded user
   * (`user_onboarded_at` set — e.g. signing in on a new device) goes straight to
   * the reel; only a not-yet-onboarded user enters the picker. Without this gate
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
      setStep("picker");
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
   * sign-in entirely and goes straight to the picker. A signed-in user must never
   * be asked to sign in again.
   */
  const handleGetStarted = useCallback(async () => {
    if (typeof window !== "undefined") {
      const session = await getCurrentSession();
      if (session) {
        sessionUserIdRef.current = session.user.id;
        logger.info("onboarding_splash_session_skip", { user_id: session.user.id });
        setStep("picker");
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
      setStep("picker");
      return;
    }
    setStep("wait_session");
  }, []);

  /** Complete the picker: persist topic + entity follows, then advance to the source swipe. */
  const handleComplete = useCallback(
    async (selections: FollowSelection[]) => {
      // Capture which CATEGORY blocks the user picked so the later `build` step seeds only
      // those (not all 8). Set before any branch so every path that can reach `build` has it.
      setSelectedCategoryBuckets(categoryBucketsFromFollows(selections));
      const userId = sessionUserIdRef.current;
      if (!userId) {
        if (SKIP_AUTH) {
          // Dev bypass: no session to scope writes to — persist NOTHING (Rule 12),
          // just advance the UI so the rest of the flow can be walked locally.
          logger.info("onboarding_skip_auth_no_persist", {
            selection_count: selections.length,
            fix_suggestion: "Dev bypass only; unset NEXT_PUBLIC_ONBOARDING_SKIP_AUTH for real onboarding.",
          });
          setPersistError(null);
          if (isSourceOnboardingComplete()) {
            router.push("/");
          } else {
            setStep("sources");
          }
          return;
        }
        // Defensive: we should only reach `picker` with a session, but never write
        // un-scoped follows (Rule 12). Send the user back to sign in.
        logger.error("onboarding_complete_without_session", {
          fix_suggestion: "A session must exist before persisting follows; returning to email step.",
        });
        setPersistError("Your session expired — please sign in again.");
        setStep("email");
        return;
      }
      setPersistError(null);
      setStep("loading");
      try {
        const result = await persistPickerFollows(userId, selections);
        if (result.unpersisted.length > 0) {
          setUnpersistedFollows(result.unpersisted);
        }
        logger.info("onboarding_completed", {
          profile_count: result.profile_count,
          entity_follow_count: result.entity_follow_count,
          unpersisted_count: result.unpersisted.length,
        });
        // A returning user who already finished the source swipe skips straight to the
        // reel; everyone else runs the source swipe before the reel.
        if (isSourceOnboardingComplete()) {
          router.push("/");
        } else {
          setStep("sources");
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "Couldn't save your follows.";
        logger.error("onboarding_persist_failed", {
          error_message: message,
          fix_suggestion: "Retry; if it persists confirm migrations 0003/0007 RLS permit the owner write.",
        });
        setPersistError(message);
        setStep("picker");
      }
    },
    [router],
  );

  /** Complete the source swipe: mark the source step done, then advance to "Build your 30". */
  const handleSourcesDone = useCallback(async (total: number) => {
    logger.info("source_onboarding_completed", { total_followed: total });
    markSourceOnboardingComplete();
    // Derive which SOURCE axes the user actually follows so "Build your 30" seeds + offers ONLY
    // those source blocks (a followed-nothing axis must not appear — owner rule 2026-06-17). A
    // read failure is non-fatal: proceed with no source blocks (safe — never seeds a phantom).
    try {
      const followedSources = await getFollowedSources();
      setFollowedSourceBuckets(sourceBucketsFromFollows(followedSources));
    } catch (error) {
      logger.warn("onboarding_source_buckets_derive_failed", {
        error_message: error instanceof Error ? error.message : "unknown",
        fix_suggestion: "Could not read followed sources for the 30 seed; seeding no source blocks (safe fallback).",
      });
    }
    setStep("build");
  }, []);

  /** Complete "Build your 30": the allocation is already persisted in the component — route to the reel. */
  const handleBuildDone = useCallback(
    (segments: BuildYour30Segment[]) => {
      logger.info("build_your_30_completed", {
        segment_count: segments.length,
        total_slots: segments.reduce((runningTotal, segment) => runningTotal + segment.count, 0),
      });
      router.push("/");
    },
    [router],
  );

  /** Skip "Build your 30": route to the reel WITHOUT saving (the allocator has a balanced default). */
  const handleBuildSkip = useCallback(() => {
    logger.info("build_your_30_skipped", {});
    router.push("/");
  }, [router]);

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

      {step === "picker" ? (
        <div className="flex min-h-full flex-1 flex-col">
          {persistError ? (
            <p role="alert" className="px-6 pt-3 font-mono text-[11px] tracking-wide text-seg-wildcard">
              {persistError}
            </p>
          ) : null}
          <TopicTree onComplete={(selections) => void handleComplete(selections)} />
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

      {step === "sources" ? <SourceSwipe onDone={handleSourcesDone} /> : null}

      {step === "build" ? (
        <BuildYour30
          onDone={handleBuildDone}
          onSkip={handleBuildSkip}
          onPickInterests={() => setStep("picker")}
          selectedCategoryBuckets={selectedCategoryBuckets}
          followedSourceBuckets={followedSourceBuckets}
        />
      ) : null}
    </main>
  );
}
