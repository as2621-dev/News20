"use client";

/**
 * OnboardingFlow — the client state machine wiring SP2/SP3 into one flow (SP4).
 *
 * Step order (phase DoD): `splash → email → chips → loading → reel`.
 *   1. `splash`   — {@link OnboardingSplash}; "get started" → `email`.
 *   2. `email`    — {@link EmailSignIn}; its `onSent` advances to waiting-for-session.
 *      The magic-link callback (a separate `/callback` page) establishes the
 *      session on this device; the flow watches `getCurrentSession` +
 *      `onAuthStateChange` and moves to `chips` once a session exists. If the user
 *      is ALREADY signed in when they reach `email` (re-onboarding), we skip
 *      straight to `chips`.
 *   3. `chips`    — {@link InterestChips}; a "continue" CTA gated by "pick ≥1"
 *      (phase Open Q3) — disabled until the selection has ≥1 taxonomy pick or ≥1
 *      custom. Continue → `loading`.
 *   4. `loading`  — calls {@link persistInterestProfile} (scoped to the session
 *      user). On success → `router.push("/")` (the reel). Any unmatched customs
 *      are surfaced inline (Rule 12 — not silently dropped).
 *
 * Static-export safe: client-only (`"use client"`), `window`-guarded, no
 * `useSearchParams` (the magic link uses the URL hash, handled in `/callback`).
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { EmailSignIn } from "@/components/onboarding/EmailSignIn";
import { InterestChips, type InterestSelection } from "@/components/onboarding/InterestChips";
import { OnboardingSplash } from "@/components/onboarding/OnboardingSplash";
import { logger } from "@/lib/logger";
import { persistInterestProfile } from "@/lib/onboardingProfile";
import { getCurrentSession } from "@/lib/supabase/auth";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** The ordered onboarding steps (phase DoD names this exact sequence). */
type OnboardingStep = "splash" | "email" | "wait_session" | "chips" | "loading";

const EMPTY_SELECTION: InterestSelection = { taxonomy_selections: [], custom_selections: [] };

/** "Pick ≥1" gate (phase Open Q3): at least one taxonomy pick OR one custom. */
function hasMinimumSelection(selection: InterestSelection): boolean {
  return selection.taxonomy_selections.length > 0 || selection.custom_selections.length > 0;
}

/**
 * Render the onboarding flow state machine.
 */
export function OnboardingFlow() {
  const router = useRouter();
  const [step, setStep] = useState<OnboardingStep>("splash");
  const [selection, setSelection] = useState<InterestSelection>(EMPTY_SELECTION);
  const [persistError, setPersistError] = useState<string | null>(null);
  const [unpersistedCustoms, setUnpersistedCustoms] = useState<string[]>([]);
  // Hold the established session's user id so `loading` can scope the writes.
  const sessionUserIdRef = useRef<string | null>(null);

  // Once on the `wait_session` step, advance to `chips` as soon as a session
  // exists (the callback page may establish it in another tab/this tab on return).
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
        sessionUserIdRef.current = session.user.id;
        setStep("chips");
      }
    });

    const { data: authSubscription } = supabase.auth.onAuthStateChange((_authEvent, session) => {
      if (isMounted && session) {
        sessionUserIdRef.current = session.user.id;
        setStep("chips");
      }
    });

    return () => {
      isMounted = false;
      authSubscription.subscription.unsubscribe();
    };
  }, [step]);

  const handleSelectionChange = useCallback((next: InterestSelection) => {
    setSelection(next);
  }, []);

  /** Email link sent: if already authed, skip the wait; else wait for the session. */
  const handleEmailSent = useCallback(async () => {
    if (typeof window === "undefined") {
      return;
    }
    const session = await getCurrentSession();
    if (session) {
      sessionUserIdRef.current = session.user.id;
      setStep("chips");
      return;
    }
    setStep("wait_session");
  }, []);

  /** Continue from chips: persist the profile, then route to the reel. */
  const handleContinue = useCallback(async () => {
    const userId = sessionUserIdRef.current;
    if (!userId) {
      // Defensive: we should only reach `chips` with a session, but never write
      // an un-scoped profile (Rule 12). Send the user back to sign in.
      logger.error("onboarding_continue_without_session", {
        fix_suggestion: "A session must exist before persisting the profile; returning to email step.",
      });
      setPersistError("Your session expired — please sign in again.");
      setStep("email");
      return;
    }
    setPersistError(null);
    setStep("loading");
    try {
      const result = await persistInterestProfile(userId, selection, { profile_source: "typed" });
      if (result.unpersisted_customs.length > 0) {
        setUnpersistedCustoms(result.unpersisted_customs);
      }
      logger.info("onboarding_completed", {
        persisted_count: result.persisted_count,
        unpersisted_count: result.unpersisted_customs.length,
      });
      router.push("/");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Couldn't save your interests.";
      logger.error("onboarding_persist_failed", {
        error_message: message,
        fix_suggestion: "Retry; if it persists confirm migration 0003 RLS permits the owner write.",
      });
      setPersistError(message);
      setStep("chips");
    }
  }, [router, selection]);

  const canContinue = hasMinimumSelection(selection);

  return (
    <main className="flex min-h-dvh w-full flex-col bg-background text-text-primary">
      {step === "splash" ? <OnboardingSplash onGetStarted={() => setStep("email")} /> : null}

      {step === "email" ? <EmailSignIn onSent={() => void handleEmailSent()} /> : null}

      {step === "wait_session" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-3 px-10 text-center">
          <p className="font-sans text-[15px] font-semibold text-text-primary">Check your inbox</p>
          <p className="font-sans text-[13px] leading-relaxed text-text-secondary">
            Tap the magic link on this device — we&apos;ll pick up automatically once you&apos;re signed in.
          </p>
          <span className="mt-2 font-mono text-[10px] tracking-wide text-white/40">WAITING FOR SIGN-IN…</span>
        </section>
      ) : null}

      {step === "chips" ? (
        <div className="flex min-h-full flex-1 flex-col">
          <div className="flex-1">
            <InterestChips onSelectionChange={handleSelectionChange} />
          </div>
          <div className="sticky bottom-0 flex flex-col gap-2 bg-background/95 px-6 py-4 backdrop-blur-sm">
            {persistError ? (
              <p role="alert" className="font-mono text-[11px] tracking-wide text-seg-wildcard">
                {persistError}
              </p>
            ) : null}
            <button
              type="button"
              onClick={() => void handleContinue()}
              disabled={!canContinue}
              className="w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity disabled:opacity-40"
            >
              {canContinue ? "Continue" : "Pick at least one"}
            </button>
          </div>
        </div>
      ) : null}

      {step === "loading" ? (
        <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-3 px-10 text-center">
          <span className="font-mono text-[11px] tracking-wide text-text-secondary">SETTING UP YOUR BRIEFING…</span>
          {unpersistedCustoms.length > 0 ? (
            <p className="font-sans text-[12px] leading-relaxed text-text-secondary">
              We couldn&apos;t match {unpersistedCustoms.join(", ")} to a topic yet — we saved everything else and
              we&apos;ll add it soon.
            </p>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}
