/**
 * SourceSwipeGlyphs — the inline SVG `<symbol>` sprite the source-swipe deck
 * references by `#id` (platform glyphs + action icons).
 *
 * Ported from the Claude Design "blip" handoff — Source Swipe (`Blip Flow.html`
 * symbol library). The deck/card/curtain reference these via `<use href="#id" />`,
 * so this sprite is mounted ONCE at the top of {@link "@/components/sources/SourceSwipe"}.
 * Kept byte-compatible with the prototype symbol set (ids `g-yt`/`g-pod`/`g-x`/
 * `g-people` for platforms, `i-x`/`i-plus`/`i-undo`/`i-check`/`i-arrow` for actions)
 * so the markup ports cleanly and the icons render identically.
 *
 * `currentColor` everywhere → the consuming element's `color` drives the stroke/fill
 * (the design tints glyphs per-context: green checks, accent arrows, white actions).
 */

/**
 * Render the hidden SVG sprite of all source-swipe glyph symbols.
 *
 * @example
 * <SourceSwipeGlyphs />
 * // …elsewhere: <svg><use href="#g-yt" /></svg>
 */
export function SourceSwipeGlyphs() {
  return (
    <svg aria-hidden="true" focusable="false" style={{ position: "absolute", width: 0, height: 0, overflow: "hidden" }}>
      <defs>
        {/* Action icons */}
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

        {/* Platform glyphs */}
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
