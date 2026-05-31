"use client";

/**
 * `/onboarding` route (Phase 1e SP4) — entry point to the chip onboarding flow.
 *
 * On mount it checks the current session and (if signed in) the user's
 * `users.user_onboarded_at`:
 *   - an ALREADY-onboarded user is sent straight to the reel (`router.replace("/")`),
 *     skipping the flow entirely;
 *   - everyone else (signed out, or signed in but not yet onboarded) renders
 *     {@link OnboardingFlow}, which itself gates the un-onboarded user into chips.
 *
 * It deliberately does NOT gate the ROOT (`/`) — adding the app-wide "un-onboarded
 * users get redirected here" auth-gate on the reel home is Phase 1c's job. This
 * page owns only the inverse: the onboarded-skip for THIS route.
 *
 * Static-export safe: client-only (`"use client"`, the static export emits the
 * shell and this resolves in the browser), `window`-guarded, no `useSearchParams`.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { OnboardingFlow } from "@/components/onboarding/OnboardingFlow";
import { logger } from "@/lib/logger";
import { getCurrentSession } from "@/lib/supabase/auth";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** Mount-time gate: are we still deciding, or should we render the flow? */
type GateState = "checking" | "show_flow";

/**
 * Render the onboarding route. Resolves the onboarded-skip, then the flow.
 */
export default function OnboardingPage() {
  const router = useRouter();
  const [gateState, setGateState] = useState<GateState>("checking");

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const supabase = getSupabaseBrowserClient();
    let isMounted = true;

    async function resolveOnboardedSkip() {
      const session = await getCurrentSession(supabase);
      if (!session) {
        // Signed out → run the flow (it starts at the splash → email sign-in).
        if (isMounted) {
          setGateState("show_flow");
        }
        return;
      }

      const { data, error } = await supabase
        .from("users")
        .select("user_onboarded_at")
        .eq("user_id", session.user.id)
        .maybeSingle();

      if (error) {
        // Surface, don't swallow (Rule 12); fall through to the flow rather than
        // trapping the user on a blank screen.
        logger.error("onboarding_gate_check_failed", {
          error_message: error.message,
          fix_suggestion: "Confirm users select-self RLS permits the read and the users row exists.",
        });
        if (isMounted) {
          setGateState("show_flow");
        }
        return;
      }

      if (data?.user_onboarded_at) {
        // Already onboarded → skip straight to the reel.
        logger.info("onboarding_skip_already_onboarded", {});
        router.replace("/");
        return;
      }

      if (isMounted) {
        setGateState("show_flow");
      }
    }

    void resolveOnboardedSkip();
    return () => {
      isMounted = false;
    };
  }, [router]);

  if (gateState === "checking") {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center bg-background px-10 text-center text-text-primary">
        <span className="font-mono text-[11px] tracking-wide text-text-secondary">LOADING…</span>
      </main>
    );
  }

  return <OnboardingFlow />;
}
