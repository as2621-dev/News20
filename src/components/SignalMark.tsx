/**
 * SignalMark — the brand "signal" element: the blip three-wave signal (a glowing
 * dot emitting three concentric arc "waves"). This is the blip wordmark's signal
 * motif (the dot + radar waves on the "i" tittle, see {@link BlipLogo}) scaled up
 * into a hero element, so every place the app shows "listening / thinking /
 * answering" reads as the SAME brand signal rather than a generic AI orb.
 *
 * Replaces the old radar-ping orb used by the source curtain, the source
 * done-screen, and the in-reel voice Q&A. Two states, driven by `responding`:
 *
 * - LISTENING (`responding={false}`) — gray waves; the signal flips to open LEFT
 *   ("your voice arrives at the dot"); ripple cascades inward (outer wave first).
 * - RESPONDING (`responding={true}`) — brand-yellow waves with a yellow glow; the
 *   signal opens RIGHT ("blip speaks outward"); ripple emanates outward (inner first).
 *
 * Color is now STATE-driven (gray → yellow), not variant-driven. The `variant`
 * prop is retained for call-site compatibility but no longer tints the mark.
 * The whole mark breathes; the dot pulses; each wave pings. Honors
 * `prefers-reduced-motion` (animations freeze, waves stay visible). Visual styling
 * lives in `src/styles/blip-flow.css` under `.vmark` (loaded app-wide).
 *
 * @example
 *   <SignalMark size={120} />            // listening (gray, opens left)
 *   <SignalMark size={120} responding /> // responding (yellow, opens right)
 */

export interface SignalMarkProps {
  /** Mark diameter in px (the SVG signal scales proportionally within it). */
  size: number;
  /**
   * Retained for call-site compatibility; no longer tints the mark (color is now
   * state-driven: gray when listening, brand-yellow when responding).
   */
  variant?: "brand" | "story";
  /** When true, the RESPONDING state: yellow waves opening right. Else LISTENING. */
  responding?: boolean;
}

/**
 * Render the brand signal mark (the blip three-wave signal).
 *
 * @param props - {@link SignalMarkProps}.
 */
export function SignalMark({ size, responding = false }: SignalMarkProps) {
  return (
    <div
      className={`vmark ${responding ? "responding" : "listening"}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {/* The blip three-wave signal: dot = blip; waves open right (responding) /
          flip to open left (listening). Outer→inner waves are w3→w1. */}
      <svg className="vm-sig" viewBox="0 0 30 44" fill="none" aria-hidden="true">
        <path className="vm-wave w3" d="M23.68 4.74 A24 24 0 0 1 23.68 39.26" />
        <path className="vm-wave w2" d="M18.82 9.78 A17 17 0 0 1 18.82 34.22" />
        <path className="vm-wave w1" d="M13.95 14.81 A10 10 0 0 1 13.95 29.19" />
        <circle className="vm-dot" cx="7" cy="22" r="3.1" />
      </svg>
    </div>
  );
}
