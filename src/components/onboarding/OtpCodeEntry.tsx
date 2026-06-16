"use client";

/**
 * OtpCodeEntry — enter the one-time code from the magic-link email.
 *
 * Rendered on the flow's waiting-for-session screen as the in-app alternative to
 * tapping the magic link. Essential inside the Capacitor iOS shell: a link tapped
 * in a mail client opens the system browser, never the native WebView, so the
 * code is the only sign-in path that completes inside the app. On a successful
 * {@link verifyEmailOtp} the session is established and `onAuthStateChange`
 * (watched by {@link OnboardingFlow}) advances the flow — this component renders
 * a brief verified state and otherwise stays out of the wiring.
 *
 * Visual register matches the onboarding surface (`EmailSignIn`): soft pill
 * controls, Inter chrome, mono accents.
 */

import { type FormEvent, useState } from "react";
import {
  OTP_CODE_LENGTH,
  signInWithTestPassword,
  TEST_AUTH_CODE_LENGTH,
  TEST_AUTH_MODE,
  verifyEmailOtp,
} from "@/lib/supabase/auth";

/** Code length the input enforces: the fixed test code in test mode, else the real OTP length. */
const CODE_LENGTH = TEST_AUTH_MODE ? TEST_AUTH_CODE_LENGTH : OTP_CODE_LENGTH;

/** The code-entry state machine: idle → verifying → (verified | error → idle on edit). */
type OtpEntryState = "idle" | "verifying" | "verified" | "error";

export interface OtpCodeEntryProps {
  /** The email the magic-link/code email was sent to (verifyOtp needs both). */
  email: string;
}

/**
 * Render the one-time-code entry form.
 *
 * @param props - {@link OtpCodeEntryProps}.
 */
export function OtpCodeEntry({ email }: OtpCodeEntryProps) {
  const [codeInput, setCodeInput] = useState("");
  const [entryState, setEntryState] = useState<OtpEntryState>("idle");
  const [errorMessage, setErrorMessage] = useState("");

  const isVerifying = entryState === "verifying";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isVerifying) {
      return;
    }
    setErrorMessage("");
    setEntryState("verifying");

    const result = TEST_AUTH_MODE
      ? await signInWithTestPassword(email, codeInput)
      : await verifyEmailOtp(email, codeInput);
    if (result.ok) {
      // The session is live; OnboardingFlow's onAuthStateChange listener advances
      // the flow — this state just covers the gap until unmount.
      setEntryState("verified");
      return;
    }
    setErrorMessage(result.error_message);
    setEntryState("error");
  }

  if (entryState === "verified") {
    return <p className="font-mono text-[11px] tracking-wide text-text-secondary">SIGNING YOU IN…</p>;
  }

  return (
    <form onSubmit={handleSubmit} className="flex w-full max-w-[280px] flex-col gap-3" noValidate>
      <input
        type="text"
        name="otp_code"
        inputMode="numeric"
        autoComplete="one-time-code"
        placeholder={`${CODE_LENGTH}-digit code`}
        aria-label="One-time code from the email"
        aria-invalid={entryState === "error"}
        value={codeInput}
        disabled={isVerifying}
        maxLength={CODE_LENGTH}
        onChange={(event) => {
          // Digits only — strips spaces from a copy-paste of the emailed code.
          setCodeInput(event.target.value.replace(/\D/g, ""));
          if (entryState === "error") {
            setEntryState("idle");
            setErrorMessage("");
          }
        }}
        className="w-full rounded-control border border-white/15 bg-white/5 px-4 py-3 text-center font-mono text-[16px] tracking-[0.3em] text-text-primary placeholder:font-sans placeholder:text-[13px] placeholder:tracking-normal placeholder:text-white/35 focus:border-white/40 focus:outline-none disabled:opacity-50"
      />

      {entryState === "error" && errorMessage ? (
        <p role="alert" className="font-mono text-[11px] tracking-wide text-seg-wildcard">
          {errorMessage}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={isVerifying || codeInput.length !== CODE_LENGTH}
        className="w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity disabled:opacity-40"
      >
        {isVerifying ? "Verifying…" : "Sign in with code"}
      </button>
    </form>
  );
}
