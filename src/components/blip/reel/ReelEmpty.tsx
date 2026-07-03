"use client";

/**
 * ReelEmpty — the "your briefing is being prepared" state.
 *
 * Shown when the authed, onboarded user has NO `daily_feeds` rows for the active day
 * yet (the daily assembly has not populated their personalized feed). We deliberately
 * never fall back to the global seeded pool (owner rule 2026-06-30) — a user must only
 * ever see their OWN allocated briefing — so this calm, on-brand empty state stands in
 * until their feed is assembled. A single Refresh re-runs the feed load.
 *
 * Visually mirrors {@link ReelError} (muted glyph, Playfair headline, one CTA) so the
 * two non-content states read as one family.
 */

export interface ReelEmptyProps {
  /** Re-attempt the feed load (reel goes back to `loading`, then `tapstart`/`error`). */
  onRefresh: () => void;
}

/**
 * Render the empty-feed screen. Covers the reel surface; the only control is Refresh.
 */
export function ReelEmpty({ onRefresh }: ReelEmptyProps) {
  return (
    <div
      className="absolute inset-0 z-30 flex flex-col items-center justify-center bg-background px-9 text-center"
      role="status"
    >
      {/* muted "assembling" glyph — a hollow ring with a centre dot, on-brand calm */}
      <span
        className="mb-7 grid h-16 w-16 place-items-center rounded-pill border border-white/[0.18] text-white/60"
        aria-hidden="true"
      >
        <svg
          width={26}
          height={26}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="9" />
          <circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none" />
        </svg>
      </span>

      <h1 className="w-full max-w-[300px] font-serif text-[27px] font-bold leading-tight text-white">
        Your briefing
        <br />
        is being prepared
      </h1>

      <p className="mt-4 w-full max-w-[300px] font-sans text-[14.5px] leading-relaxed text-white/55">
        We&rsquo;re assembling today&rsquo;s 30 stories around the interests you picked. Check back in a moment.
      </p>

      <button
        type="button"
        onClick={onRefresh}
        className="mt-8 w-full max-w-[280px] rounded-control bg-white py-3.5 font-sans text-[15px] font-semibold text-background transition-transform active:scale-95"
      >
        Refresh
      </button>
    </div>
  );
}
