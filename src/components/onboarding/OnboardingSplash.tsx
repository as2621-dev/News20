"use client";

/**
 * OnboardingSplash — the brand intro that opens onboarding (Phase 1e SP4).
 *
 * The first frame a new user sees: the `blip` wordmark (glowing radar ping), the
 * one-line product promise, and a single "get started" affordance that advances
 * the flow to email sign-in. No data, no auth here — purely the brand-forward
 * hand-off into {@link OnboardingFlow}.
 *
 * Visual register matches the reel surface (`TapToStart`, `EmailSignIn`): near-
 * black canvas, the wordmark, Inter + JetBrains Mono chrome, a soft pill CTA.
 */

import { BlipLogo } from "@/components/BlipLogo";

export interface OnboardingSplashProps {
  /** Fired when the user taps "get started" — the flow advances to email sign-in. */
  onGetStarted: () => void;
}

/**
 * Render the onboarding splash.
 *
 * @param props - {@link OnboardingSplashProps}.
 */
export function OnboardingSplash({ onGetStarted }: OnboardingSplashProps) {
  return (
    <section className="flex min-h-full flex-1 flex-col items-center justify-center gap-7 px-10 text-center">
      <BlipLogo size={36} glow />

      <div>
        <h1 className="font-sans text-[20px] font-semibold leading-tight text-text-primary">
          30 stories. 30 minutes. Caught up.
        </h1>
        <p className="mt-3 font-sans text-[14px] leading-relaxed text-text-secondary">
          Your daily briefing, read aloud and tuned to what you actually care about.
        </p>
      </div>

      <button
        type="button"
        onClick={onGetStarted}
        className="w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-80"
      >
        Get started
      </button>

      <span className="font-mono text-[10px] tracking-wide text-white/40">EMAIL SIGN-IN · NO PASSWORD</span>
    </section>
  );
}
