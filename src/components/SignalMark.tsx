/**
 * SignalMark — the brand "signal" element: a glowing core dot emitting concentric
 * radar pings. This is the blip wordmark's signal motif (the dot + radar waves on
 * the "i" tittle, see {@link BlipLogo}) scaled up into a hero element, so every
 * place the app shows "listening / thinking / building" reads as the SAME brand
 * signal rather than a generic AI orb.
 *
 * Replaces the old cloud-gradient `.orb` blob used by the source curtain, the
 * source done-screen, and the in-reel voice Q&A. One component, two variants:
 *
 * - `brand` — white signal on the near-black surface (onboarding / profile build).
 * - `story` — tints to the active story's `--accent` (in-reel voice Q&A), falling
 *   back to the story-red accent when no `--accent` is in scope.
 *
 * `responding` speeds up + brightens the emission for the "thinking / answering"
 * state. Honors `prefers-reduced-motion` (pings freeze into static concentric
 * rings, no pulsing). Visual styling lives in `src/styles/blip-flow.css` under
 * `.signal-mark` (loaded app-wide).
 *
 * @example
 *   <SignalMark size={100} />                          // brand, idle
 *   <SignalMark size={100} responding />               // brand, building profile
 *   <SignalMark size={100} variant="story" responding /> // in-reel voice answering
 */

export interface SignalMarkProps {
  /** Mark diameter in px (the core dot + ping radius scale from this). */
  size: number;
  /** Color register: brand white, or per-story accent. Defaults to `brand`. */
  variant?: "brand" | "story";
  /** When true, pings emit faster + brighter (the "thinking / answering" state). */
  responding?: boolean;
}

/**
 * Render the brand signal mark.
 *
 * @param props - {@link SignalMarkProps}.
 */
export function SignalMark({ size, variant = "brand", responding = false }: SignalMarkProps) {
  return (
    <div
      className={`signal-mark ${variant}${responding ? " responding" : ""}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {/* Three staggered radar pings emanating from the core (continuous emission). */}
      <span className="sm-ping" />
      <span className="sm-ping" />
      <span className="sm-ping" />
      {/* The glowing core dot — same DNA as the wordmark's "i" tittle blip. */}
      <span className="sm-core" />
    </div>
  );
}
