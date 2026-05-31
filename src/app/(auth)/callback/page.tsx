"use client";

/**
 * Magic-link callback landing (Phase 1e SP2).
 *
 * Because the app is a static export (`output: "export"`, no Next.js server),
 * there is NO server to exchange the magic-link code. Instead the browser client
 * is configured with `detectSessionInUrl: true`, so supabase-js parses the auth
 * tokens out of the URL hash on load. This page just observes that: on mount it
 * checks for an existing session and subscribes to `onAuthStateChange`, rendering
 * a brief "signing you in…" state until the session is established, then a success
 * state.
 *
 * It deliberately does NOT redirect into the onboarding flow — that route is owned
 * by SP4 and does not exist yet. It exposes the established-session state cleanly
 * and avoids `useSearchParams` (which would force a Suspense boundary under static
 * export, and is unnecessary since the link uses the URL hash).
 */

import { useEffect, useState } from "react";
import { BlipLogo } from "@/components/BlipLogo";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** Callback lifecycle: waiting for supabase-js to parse the link, then signed in. */
type CallbackState = "signing_in" | "signed_in";

/**
 * Render the magic-link callback page.
 */
export default function AuthCallbackPage() {
  const [callbackState, setCallbackState] = useState<CallbackState>("signing_in");

  useEffect(() => {
    // Reason: static-export safety — bail if somehow evaluated without a window
    // (no DOM, no URL hash to parse). The client is browser-only.
    if (typeof window === "undefined") {
      return;
    }
    const supabase = getSupabaseBrowserClient();
    let isMounted = true;

    // Check for a session already established (detectSessionInUrl resolves on load).
    supabase.auth.getSession().then(({ data }) => {
      if (isMounted && data.session) {
        logger.info("magic_link_callback_session_established", { source: "get_session" });
        setCallbackState("signed_in");
      }
    });

    // And subscribe in case the URL parse completes a tick after mount.
    const { data: authSubscription } = supabase.auth.onAuthStateChange((authEvent, session) => {
      if (isMounted && session) {
        logger.info("magic_link_callback_session_established", { source: "auth_state_change", auth_event: authEvent });
        setCallbackState("signed_in");
      }
    });

    return () => {
      isMounted = false;
      authSubscription.subscription.unsubscribe();
    };
  }, []);

  return (
    <main className="bg-background text-text-primary flex min-h-dvh flex-col items-center justify-center gap-5 px-10 text-center">
      <BlipLogo size={28} glow />
      {callbackState === "signed_in" ? (
        <div>
          <h1 className="font-sans text-[17px] font-semibold text-text-primary">You&apos;re signed in</h1>
          <p className="mt-2 font-sans text-[13px] leading-relaxed text-text-secondary">
            Hang tight — setting up your briefing.
          </p>
        </div>
      ) : (
        <p className="font-mono text-[11px] tracking-wide text-text-secondary">SIGNING YOU IN…</p>
      )}
    </main>
  );
}
