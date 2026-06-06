/**
 * SignalOrb — the brand "signal" orb used by the source-swipe curtain + done screen
 * (Phase 5c SP-UI).
 *
 * Ported from the Claude Design "blip" handoff — Source Swipe (`blip-sources.js`
 * `orb(size)` markup + `blip-flow.css` `.orb`/`.orb.brand`). Renders the layered
 * cloud/core gradient discs the CSS animates (breathe + cloud-drift). `responding`
 * adds the `.responding` modifier (faster, brighter pulse) the curtain toggles
 * while "thinking". Size is set inline so one component serves the 100px curtain
 * orb and the 84px done-screen orb.
 *
 * The visual styling lives in `globals.css` under `.sw-screen .orb` (screen-scoped
 * so it does NOT clobber the existing in-news Voice `.orb`).
 */

export interface SignalOrbProps {
  /** Disc diameter in px. */
  size: number;
  /** When true, applies the brighter/faster `.responding` pulse. */
  responding?: boolean;
}

/**
 * Render the brand signal orb.
 *
 * @param props - {@link SignalOrbProps}.
 */
export function SignalOrb({ size, responding = false }: SignalOrbProps) {
  return (
    <div
      className={`orb brand${responding ? " responding" : ""}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <i className="c1" />
      <i className="c2" />
      <i className="d1" />
      <i className="d2" />
      <i className="core" />
    </div>
  );
}
