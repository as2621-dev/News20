"use client";

/**
 * AppRouter — the root (`/`) auth/onboarding gate (Phase 4b SP2).
 *
 * Mounts the reel ONLY for a signed-in, onboarded user; everyone else is sent to
 * `/onboarding` (the {@link OnboardingFlow}, which itself handles signed-out →
 * email sign-in and signed-in-not-onboarded → interest chips). The decision is
 * resolved by {@link resolveRootGate}; this component is the thin render half.
 *
 * No reel flash (the phase DoD): {@link BlipReel} is never mounted until the gate
 * has resolved to `"reel"`. While resolving — and on the static-export prerender,
 * where there is no `window` yet — we render a neutral LOADING state (matching the
 * onboarding route's), so the static shell never bakes in the reel.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BlipReel } from "@/components/blip/reel/BlipReel";
import { PhoneShell } from "@/components/PhoneShell";
import { resolveRootGate } from "@/lib/auth/routeGuard";
import { logger } from "@/lib/logger";

/** Render lifecycle: still deciding, or cleared to mount the reel. */
type RouterState = "checking" | "reel";

/**
 * Render the gated app home: a loading state until the gate resolves, then either
 * the reel (onboarded) or a redirect to `/onboarding` (everyone else).
 */
export function AppRouter() {
  const router = useRouter();
  const [routerState, setRouterState] = useState<RouterState>("checking");

  useEffect(() => {
    // Reason: static-export safety — the gate reads the browser-only Supabase
    // session, so it can only run client-side. The prerendered shell stays "checking".
    if (typeof window === "undefined") {
      return;
    }
    let isMounted = true;

    void resolveRootGate().then((decision) => {
      if (!isMounted) {
        return;
      }
      if (decision === "reel") {
        setRouterState("reel");
        return;
      }
      // Both "sign_in" and "onboarding" route into the onboarding flow, which gates
      // the right entry step itself. `replace` so the gated `/` is not a back target.
      logger.info("root_gate_redirect_onboarding", { decision });
      router.replace("/onboarding");
    });

    return () => {
      isMounted = false;
    };
  }, [router]);

  if (routerState === "reel") {
    return (
      <PhoneShell>
        <BlipReel />
      </PhoneShell>
    );
  }

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-background px-10 text-center text-text-primary">
      <span className="font-mono text-[11px] tracking-wide text-text-secondary">LOADING…</span>
    </main>
  );
}
