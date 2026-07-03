"use client";

/**
 * InterviewConfirm — the terminal confirmation screen of the chat interview
 * (FSR slice #4). Shows the extracted interest list in the USER'S OWN vocabulary
 * (`display_label`, spec §4) so nothing wrong gets baked into the feed before the
 * user approves it (PRD story #8). Purely presentational: it renders the list and
 * two actions (confirm / go back to edit) — the parent {@link InterviewChat} owns
 * persistence and navigation.
 *
 * The skip-everything path lands here too, with an empty list: skipping is not
 * punished (spec §5), so the screen reassures the user their feed will use broad
 * categories rather than showing an error.
 */

import type { TerminalMicroInterest } from "@/types/interview";

export interface InterviewConfirmProps {
  /** The extracted micro-interests to confirm (empty on the skip-everything path). */
  microInterests: TerminalMicroInterest[];
  /** True when the interview ended on the skip path — show the roots-only reassurance. */
  rootsOnlyFallback: boolean;
  /** Confirm the list as-is → parent persists the profile. */
  onConfirm: () => void;
  /** Reject/edit → parent re-opens the last question so the user can change an answer. */
  onEditBack: () => void;
}

/**
 * Render the interview confirmation screen.
 */
export function InterviewConfirm({ microInterests, rootsOnlyFallback, onConfirm, onEditBack }: InterviewConfirmProps) {
  const hasInterests = microInterests.length > 0;
  return (
    <section className="flex min-h-full flex-1 flex-col px-8 pt-10 pb-8">
      <span className="font-mono text-[11px] tracking-wide text-text-secondary">HERE&apos;S WHAT WE HEARD</span>
      <h1 className="mt-2 font-sans text-[19px] font-semibold leading-snug text-text-primary">
        {hasInterests ? "Your feed will focus on these." : "We'll start you with the big picture."}
      </h1>

      {hasInterests ? (
        <ul className="mt-6 flex flex-1 flex-col gap-2 overflow-y-auto" aria-label="Extracted interests">
          {microInterests.map((interest) => (
            <li
              key={interest.canonical_slug}
              className="rounded-control border border-white/12 bg-white/5 px-4 py-3 font-sans text-[15px] text-text-primary"
            >
              {interest.display_label}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-6 flex-1 font-sans text-[14px] leading-relaxed text-text-secondary">
          You skipped through, so we&apos;ll build a broad briefing across the main categories. You can always sharpen
          it later — nothing is locked in.
        </p>
      )}

      {rootsOnlyFallback && hasInterests ? (
        <p className="mt-3 font-sans text-[12px] leading-relaxed text-text-secondary">
          We&apos;ll fill the rest of your feed with the big stories across categories.
        </p>
      ) : null}

      <div className="mt-6 flex flex-col gap-3">
        <button
          type="button"
          onClick={onConfirm}
          className="w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-70"
        >
          {hasInterests ? "Looks good" : "Continue"}
        </button>
        <button
          type="button"
          onClick={onEditBack}
          className="font-sans text-[13px] text-text-secondary underline underline-offset-4 transition-opacity active:opacity-60"
        >
          Change an answer
        </button>
      </div>
    </section>
  );
}
