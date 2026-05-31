"use client";

/**
 * EmailSignIn — passwordless email magic-link sign-in (Phase 1e SP2).
 *
 * An email field + submit driving an explicit 5-state machine the phase DoD
 * names: `empty | invalid | sending | sent | error`. An invalid email renders an
 * inline error and NEVER calls the API (Rule 12 — the guard lives in
 * {@link sendMagicLink}, this component just surfaces its `{ ok: false }`). A
 * valid email transitions `sending → sent` ("check your inbox") or `error`.
 *
 * Visual register matches the reel surface (e.g. `TapToStart`): near-black
 * canvas, the `blip` wordmark, Inter chrome, soft pill controls. No flow wiring
 * here — an optional {@link EmailSignInProps.onSent} lets SP4 advance to the
 * chip step once the link is sent.
 */

import { type FormEvent, useState } from "react";
import { BlipLogo } from "@/components/BlipLogo";
import { sendMagicLink } from "@/lib/supabase/auth";

/** The explicit sign-in state machine (phase DoD names these five states). */
type SignInState = "empty" | "invalid" | "sending" | "sent" | "error";

export interface EmailSignInProps {
  /**
   * Optional callback fired once the magic link has been sent. SP4 wires this to
   * advance the onboarding flow; omit it to leave the component standalone.
   */
  onSent?: (email: string) => void;
}

/**
 * Render the email magic-link sign-in form.
 *
 * @param props - {@link EmailSignInProps}.
 */
export function EmailSignIn({ onSent }: EmailSignInProps) {
  const [emailInput, setEmailInput] = useState("");
  const [signInState, setSignInState] = useState<SignInState>("empty");
  const [errorMessage, setErrorMessage] = useState("");

  const isSubmitting = signInState === "sending";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    setErrorMessage("");
    setSignInState("sending");

    const result = await sendMagicLink(emailInput);
    if (result.ok) {
      setSignInState("sent");
      onSent?.(emailInput.trim());
      return;
    }
    // Reason: sendMagicLink returns ok:false for a locally-rejected invalid email
    // (no API call) AND for a server-side failure — map to the matching state so
    // the user sees an inline message either way.
    setErrorMessage(result.error_message);
    setSignInState(result.error_message === "Enter a valid email address." ? "invalid" : "error");
  }

  if (signInState === "sent") {
    return (
      <section className="flex min-h-full flex-col items-center justify-center gap-5 px-10 text-center">
        <BlipLogo size={28} glow />
        <div>
          <h1 className="font-sans text-[17px] font-semibold text-text-primary">Check your inbox</h1>
          <p className="mt-2 font-sans text-[13px] leading-relaxed text-text-secondary">
            We sent a sign-in link to <span className="text-text-primary">{emailInput.trim()}</span>. Tap it on this
            device to continue.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="flex min-h-full flex-col items-center justify-center gap-6 px-10 text-center">
      <BlipLogo size={28} glow />
      <div>
        <h1 className="font-sans text-[17px] font-semibold text-text-primary">Sign in to blip</h1>
        <p className="mt-2 font-sans text-[13px] leading-relaxed text-text-secondary">
          Enter your email and we&apos;ll send you a magic link — no password.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex w-full flex-col gap-3" noValidate>
        <input
          type="email"
          name="email"
          inputMode="email"
          autoComplete="email"
          placeholder="you@example.com"
          aria-label="Email address"
          aria-invalid={signInState === "invalid"}
          value={emailInput}
          disabled={isSubmitting}
          onChange={(event) => {
            setEmailInput(event.target.value);
            // Reason: clear transient error states the moment the user edits, so a
            // prior invalid/error message doesn't linger over fresh input. Return
            // to `empty` — re-validation happens on the next submit, not per keystroke.
            if (signInState === "invalid" || signInState === "error") {
              setSignInState("empty");
              setErrorMessage("");
            }
          }}
          className="w-full rounded-control border border-white/15 bg-white/5 px-4 py-3 font-sans text-[15px] text-text-primary placeholder:text-white/35 focus:border-white/40 focus:outline-none disabled:opacity-50"
        />

        {(signInState === "invalid" || signInState === "error") && errorMessage ? (
          <p role="alert" className="font-mono text-[11px] tracking-wide text-seg-wildcard">
            {errorMessage}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={isSubmitting || emailInput.trim() === ""}
          className="w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity disabled:opacity-40"
        >
          {isSubmitting ? "Sending…" : "Send magic link"}
        </button>
      </form>
    </section>
  );
}
