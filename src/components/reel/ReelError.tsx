"use client";

/**
 * ReelError — the offline / failed-load state (port-map §2 row 11; ports
 * `showErrorScreen`).
 *
 * Calm and on-brand, never alarming: a muted glyph, a Playfair "can't reach
 * today's briefing" headline, a reassuring line, and a single Retry CTA that
 * re-runs the feed load (the reel returns to its loading state while it retries).
 *
 * **Scope (M1).** The prototype's secondary "continue with N downloaded" action
 * is OMITTED — it implies a partial offline cache that does not exist against the
 * bundled fixtures (the fixture feed either fully resolves or fully rejects).
 * Retry is the whole recovery path here.
 */

export interface ReelErrorProps {
  /** Re-attempt the feed load (reel goes back to `loading`, then `tapstart`/`error`). */
  onRetry: () => void;
}

/**
 * Render the connection-error screen. Covers the reel surface; the only control
 * is Retry.
 */
export function ReelError({ onRetry }: ReelErrorProps) {
  return (
    <div
      className="absolute inset-0 z-30 flex flex-col items-center justify-center bg-background px-9 text-center"
      role="alert"
    >
      {/* muted "blindspot" glyph — a hollow ring, on-brand calm */}
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
          <path d="M12 8v4" strokeLinecap="round" />
          <path d="M12 16h.01" strokeLinecap="round" />
        </svg>
      </span>

      <h1 className="w-full max-w-[300px] font-serif text-[27px] font-bold leading-tight text-white">
        Can&rsquo;t reach
        <br />
        today&rsquo;s briefing
      </h1>

      <p className="mt-4 w-full max-w-[300px] font-sans text-[14.5px] leading-relaxed text-white/55">
        You appear to be offline. We&rsquo;ll refresh today&rsquo;s briefing as soon as you&rsquo;re back.
      </p>

      <button
        type="button"
        onClick={onRetry}
        className="mt-8 w-full max-w-[280px] rounded-control bg-white py-3.5 font-sans text-[15px] font-semibold text-background transition-transform active:scale-95"
      >
        Retry
      </button>
    </div>
  );
}
