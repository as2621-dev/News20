"use client";

/**
 * OnboardingFlow — the client state machine wiring the conversational interview
 * (FSR slice #4) into the onboarding flow (supersedes the roots-only topic picker).
 *
 * Step order (spec §1): `splash → email → wait_session → interview → loading → sources → build`.
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
 *   4. `loading`  — calls {@link persistInterviewInterests} (scoped to the session
 *      user; mints niche nodes + a deep profile). On success → the `sources` step. Any
 *      rejected micro-interests (backstop-invalid) are surfaced inline (Rule 12 — not
 *      silently dropped). Persistence fires ONLY on terminal confirm (no half-profiles).
 *   5. `sources`  — {@link SourceClusterScreen}; the M6 source/cluster onboarding step
 *      (Phase FSR-M6a). It loads the chosen categories' resolved clusters (no-dup
 *      applied), renders the opt-out cluster/member grid, and on continue commits the
 *      resolved follow set to `user_content_sources`/`user_personalities`. It then
 *      marks the source step complete ({@link markSourceOnboardingComplete}) and
 *      advances to the `build` step. A returning user who already completed the source
 *      step skips it (gated in `onboarding/page.tsx` via {@link isSourceOnboardingComplete}).
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
import { InterviewChat } from "@/components/onboarding/InterviewChat";
import { OnboardingSplash } from "@/components/onboarding/OnboardingSplash";
import { OtpCodeEntry } from "@/components/onboarding/OtpCodeEntry";
import { SourceClusterScreen } from "@/components/sources/SourceClusterScreen";
import { resolveRootGate } from "@/lib/auth/routeGuard";
import { type DesignBucketId, PICKER_ROOT_TO_CATEGORY_BUCKET, sourceBucketsFromFollows } from "@/lib/feedBuckets";
import { clearInterviewSession } from "@/lib/interview/session";
import { persistInterviewInterests, persistMuteTerms } from "@/lib/interviewProfile";
import { logger } from "@/lib/logger";
import {
  isSourceOnboardingComplete,
  markOnboardingComplete,
  markSourceOnboardingComplete,
} from "@/lib/onboardingProfile";
import { getFollowedSources } from "@/lib/sources";
import { getCurrentSession, TEST_AUTH_CODE, TEST_AUTH_MODE } from "@/lib/supabase/auth";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { InterviewTerminalPayload } from "@/types/interview";

/** The ordered onboarding steps (spec §1: the interview replaces the old picker). */
type OnboardingStep = "splash" | "email" | "wait_session" | "interview" | "loading" | "sources" | "build";

/**
 * Dev-only bypass: skip the email/magic-link auth gate and drop straight into the
 * interview, with interest-persistence no-op'd (there is no session to scope the
 * RLS writes to, so we deliberately write nothing). Enabled ONLY when
 * `NEXT_PUBLIC_ONBOARDING_SKIP_AUTH=true` — leave UNSET in production. NEXT_PUBLIC_*
 * vars are inlined at build time, so a build is required to flip this.
 *
 * Reason: lets the onboarding UI (interview → sources → build) be walked locally before
 * email auth is configured, without ever persisting un-scoped data (Rule 12).
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
  // Captured in `handleInterviewComplete` so the later `build` step ("Build your 30") seeds ONLY
  // those category blocks. Empty (interview skipped) → the build screen falls back to the full seed.
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
   * Complete the interview: persist the confirmed micro-interests, then advance to
   * the source step. Persistence fires ONLY here (on terminal confirm) — no
   * half-profiles — and NEVER stamps `user_onboarded_at` (owned solely by
   * {@link markOnboardingComplete} at the true flow end; onboarding-gate rule 2026-06-30).
   *
   * Unlike the retired picker, the interview is SKIPPABLE (spec §5): a skip-everything
   * roots-only payload persists cleanly (feed falls back to broad categories) and STILL
   * advances — skipping is not punished. A persist failure keeps the resumable transcript
   * (InterviewChat did not clear it) and returns to the interview so the user can retry.
   */
  const handleInterviewComplete = useCallback(
    async (payload: InterviewTerminalPayload) => {
      // Capture which CATEGORY blocks the confirmed interests touch so the later `build`
      // step seeds only those (an empty set — skip-everything — falls back to the full seed).
      // Each micro-interest's canonical slug is root-anchored, so its first segment is the root.
      const buckets = new Set<DesignBucketId>();
      for (const interest of payload.micro_interests) {
        const rootSlug = interest.canonical_slug.split(".")[0];
        const bucketId = PICKER_ROOT_TO_CATEGORY_BUCKET[rootSlug];
        if (bucketId === undefined) {
          // An unmapped root should be impossible (the payload is root-anchored to the 8
          // roots), but surface it rather than silently drop the block (Rule 12).
          logger.warn("interview_bucket_root_unmapped", {
            canonical_slug: interest.canonical_slug,
            root_segment: rootSlug,
            fix_suggestion: "Add the root to PICKER_ROOT_TO_CATEGORY_BUCKET if it should seed a 'Build your 30' block.",
          });
          continue;
        }
        buckets.add(bucketId);
      }
      setSelectedCategoryBuckets([...buckets]);

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
            router.push("/");
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
        const result = await persistInterviewInterests(userId, payload);
        // Persist the SKIP-TUNE mutes (issue #17) so the assembler hard-filters them. First-run
        // onboarding is a pure upsert (no replace); an empty mute list is a valid no-op.
        await persistMuteTerms(userId, payload.mute_terms ?? []);
        // Rejected micro-interests (backstop-invalid) are surfaced via structured logs, never
        // silently minted (Rule 12) — the rest still persist. (persistInterviewInterests logs
        // each rejection with its reason; we also stash the slugs for the transient loading note.)
        if (result.rejected_interests.length > 0) {
          setUnpersistedFollows(result.rejected_interests.map((rejected) => rejected.canonical_slug));
        }
        logger.info("onboarding_completed", {
          minted_interest_count: result.minted_interest_count,
          rejected_count: result.rejected_interests.length,
          roots_only_fallback: payload.roots_only_fallback ?? false,
        });
        // The confirmed profile is now minted — the resumable transcript is stale.
        clearInterviewSession();
        // A returning user who already finished the source step skips straight to the
        // reel; everyone else runs the source step before the reel.
        if (isSourceOnboardingComplete()) {
          router.push("/");
        } else {
          setStep("sources");
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "Couldn't save your interests.";
        logger.error("onboarding_persist_failed", {
          error_message: message,
          fix_suggestion: "Retry; if it persists confirm migration 0025 (mint RPC) + RLS permit the owner write.",
        });
        // Keep the resumable transcript (not cleared) so the retry re-confirms cleanly.
        setPersistError(message);
        setStep("interview");
      }
    },
    [router],
  );

  /** Complete the source/cluster step: mark it done, then advance to "Build your 30". */
  const handleSourcesDone = useCallback(async () => {
    logger.info("source_onboarding_completed", {});
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

  /**
   * Stamp onboarding genuinely COMPLETE, then route to the reel. This is the SINGLE
   * place `users.user_onboarded_at` is written in the flow (the picker/source steps no
   * longer stamp it) — so the root gate only routes to the reel once the user has
   * actually reached the end. A stamp failure is surfaced (Rule 12) but still routes:
   * the user finished onboarding, and the gate self-heals on the next read.
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

  /** Complete "Build your 30": the allocation is already persisted in the component — stamp + route. */
  const handleBuildDone = useCallback(
    (segments: BuildYour30Segment[]) => {
      logger.info("build_your_30_completed", {
        segment_count: segments.length,
        total_slots: segments.reduce((runningTotal, segment) => runningTotal + segment.count, 0),
      });
      void finishOnboarding();
    },
    [finishOnboarding],
  );

  /** Skip "Build your 30": route to the reel WITHOUT saving (the allocator has a balanced default). */
  const handleBuildSkip = useCallback(() => {
    logger.info("build_your_30_skipped", {});
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
        <SourceClusterScreen categories={selectedCategoryBuckets} onDone={() => void handleSourcesDone()} />
      ) : null}

      {step === "build" ? (
        <BuildYour30
          onDone={handleBuildDone}
          onSkip={handleBuildSkip}
          onPickInterests={() => setStep("interview")}
          selectedCategoryBuckets={selectedCategoryBuckets}
          followedSourceBuckets={followedSourceBuckets}
        />
      ) : null}
    </main>
  );
}
