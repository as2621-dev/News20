"use client";

/**
 * OnboardingFlow — the client state machine wiring the recursive interest picker
 * into the onboarding flow (Phase 5 SP4; supersedes phase-1e's `InterestChips`).
 *
 * Step order (phase DoD): `splash → email → picker → loading → reel`.
 *   1. `splash`   — {@link OnboardingSplash}; "get started" → `email`.
 *   2. `email`    — {@link EmailSignIn}; its `onSent` advances to waiting-for-session.
 *      The magic-link callback (a separate `/callback` page) establishes the
 *      session on this device; the flow watches `getCurrentSession` +
 *      `onAuthStateChange` and moves to `picker` once a session exists. If the user
 *      is ALREADY signed in when they reach `email` (re-onboarding), we skip
 *      straight to `picker`.
 *   3. `picker`   — {@link OnboardingPicker}; the recursive follow-set picker. It is
 *      **skippable** (spec §10/§11) — NO "pick ≥1" gate; Continue/Skip is always
 *      enabled and hands back `store.all()` (an empty array on a skip).
 *   4. `loading`  — calls {@link persistPickerFollows} (scoped to the session user).
 *      On success → `router.push("/")` (the reel). Any unpersisted follows (free-text
 *      customs / unmatched topics) are surfaced inline (Rule 12 — not silently dropped).
 *      A zero-follow completion persists nothing (no error) and still routes to the reel.
 *
 * Static-export safe: client-only (`"use client"`), `window`-guarded, no
 * `useSearchParams` (the magic link uses the URL hash, handled in `/callback`).
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { EmailSignIn } from "@/components/onboarding/EmailSignIn";
import { OnboardingPicker } from "@/components/onboarding/OnboardingPicker";
import { OnboardingSplash } from "@/components/onboarding/OnboardingSplash";
import { logger } from "@/lib/logger";
import { persistPickerFollows } from "@/lib/onboardingProfile";
import { getCurrentSession } from "@/lib/supabase/auth";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { FollowSelection } from "@/types/picker";

/** The ordered onboarding steps (phase DoD names this exact sequence). */
type OnboardingStep = "splash" | "email" | "wait_session" | "picker" | "loading";

/**
 * Render the onboarding flow state machine.
 */
export function OnboardingFlow() {
  const router = useRouter();
  const [step, setStep] = useState<OnboardingStep>("splash");
  const [persistError, setPersistError] = useState<string | null>(null);
  const [unpersistedFollows, setUnpersistedFollows] = useState<string[]>([]);
  // Hold the established session's user id so `loading` can scope the writes.
  const sessionUserIdRef = useRef<string | null>(null);

  // Once on the `wait_session` step, advance to `picker` as soon as a session
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
        setStep("picker");
      }
    });

    const { data: authSubscription } = supabase.auth.onAuthStateChange((_authEvent, session) => {
      if (isMounted && session) {
        sessionUserIdRef.current = session.user.id;
        setStep("picker");
      }
    });

    return () => {
      isMounted = false;
      authSubscription.subscription.unsubscribe();
    };
  }, [step]);

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

  /** Complete the picker: persist topic + entity follows, then route to the reel. */
  const handleComplete = useCallback(
    async (selections: FollowSelection[]) => {
      const userId = sessionUserIdRef.current;
      if (!userId) {
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
        router.push("/");
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

      {step === "picker" ? (
        <div className="flex min-h-full flex-1 flex-col">
          {persistError ? (
            <p role="alert" className="px-6 pt-3 font-mono text-[11px] tracking-wide text-seg-wildcard">
              {persistError}
            </p>
          ) : null}
          <OnboardingPicker onComplete={(selections) => void handleComplete(selections)} />
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
    </main>
  );
}
