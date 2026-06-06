"use client";

/**
 * BlipIconDefs — the shared inline SVG `<symbol>` library for the Blip Flow scenes.
 *
 * The flow's scenes reference icons by `#id` via `<use href="#i-...">` (e.g. the
 * topic tree's check uses `#i-check`). This component renders the off-screen
 * `<svg width="0" height="0">` `<defs>` block ONCE near the top of the flow so every
 * scene's `#id` references resolve. Ported VERBATIM from `Blip Flow.html`'s icon
 * library (symbol viewBoxes + paths unchanged; only HTML attribute names are
 * JSX-cased — `stroke-width` → `strokeWidth`, etc.). Stage 1 strictly needs only
 * `i-check`, but the full block is included so later stages get their icons for free.
 *
 * Static-export safe: pure presentational client component, no `window` access.
 *
 * @example
 * // Render once at the top of the flow, then reference symbols anywhere below:
 * <BlipIconDefs />
 * <svg className="cbox"><use href="#i-check" /></svg>
 */
export function BlipIconDefs() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        <symbol id="i-save" viewBox="0 0 24 24">
          <path
            d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-share" viewBox="0 0 24 24">
          <path
            d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7M12 3v13M7 8l5-5 5 5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-following" viewBox="0 0 24 24">
          <path
            d="M5 12.5l4.5 4.5L19 7"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-voice" viewBox="0 0 24 24">
          <path
            d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3ZM6 11a6 6 0 0 0 12 0M12 17v4M8 21h8"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-profile" viewBox="0 0 24 24">
          <circle cx="12" cy="8" r="4" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path
            d="M4 21c1.5-4 5-6 8-6s6.5 2 8 6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </symbol>
        <symbol id="i-send" viewBox="0 0 24 24">
          <path
            d="M4 12 20 4l-6 16-3-7-7-1Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-search" viewBox="0 0 24 24">
          <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </symbol>
        <symbol id="i-keyboard" viewBox="0 0 24 24">
          <rect x="3" y="6" width="18" height="12" rx="2.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path
            d="M7 10h.01M11 10h.01M15 10h.01M8 14h8"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </symbol>
        <symbol id="i-close" viewBox="0 0 24 24">
          <path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </symbol>
        <symbol id="i-back" viewBox="0 0 24 24">
          <path
            d="M15 5l-7 7 7 7"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-spark" viewBox="0 0 24 24">
          <path
            d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2 2-6Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-doc" viewBox="0 0 24 24">
          <path
            d="M7 3h7l4 4v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinejoin="round"
          />
          <path d="M13 3v5h5M9 13h6M9 17h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </symbol>
        <symbol id="i-arrow" viewBox="0 0 24 24">
          <path
            d="M5 12h14M13 6l6 6-6 6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-plus" viewBox="0 0 24 24">
          <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
        </symbol>
        <symbol id="i-x" viewBox="0 0 24 24">
          <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
        </symbol>
        <symbol id="i-undo" viewBox="0 0 24 24">
          <path
            d="M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-check" viewBox="0 0 24 24">
          <path
            d="M4 12l5 5L20 6"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </symbol>
        <symbol id="i-play" viewBox="0 0 24 24">
          <path
            d="M8 5.5v13a1 1 0 0 0 1.5.87l10.5-6.5a1 1 0 0 0 0-1.74L9.5 4.63A1 1 0 0 0 8 5.5Z"
            fill="currentColor"
          />
        </symbol>
        <symbol id="i-pause" viewBox="0 0 24 24">
          <rect x="6.5" y="5" width="4" height="14" rx="1.3" fill="currentColor" />
          <rect x="13.5" y="5" width="4" height="14" rx="1.3" fill="currentColor" />
        </symbol>
        <symbol id="g-yt" viewBox="0 0 24 24">
          <rect x="2" y="5" width="20" height="14" rx="4" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path d="M10 9l5 3-5 3V9Z" fill="currentColor" />
        </symbol>
        <symbol id="g-pod" viewBox="0 0 24 24">
          <rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path
            d="M6 11a6 6 0 0 0 12 0M12 17v4M9 21h6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </symbol>
        <symbol id="g-x" viewBox="0 0 24 24">
          <path d="M4 4l16 16M20 4 4 20" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </symbol>
        <symbol id="g-people" viewBox="0 0 24 24">
          <circle cx="9" cy="8" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path
            d="M3.4 19a5.6 5.6 0 0 1 11.2 0"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
          <path
            d="M16 5.3a3.1 3.1 0 0 1 0 5.9M17.6 13.7a5.6 5.6 0 0 1 3 4.8"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </symbol>
      </defs>
    </svg>
  );
}
