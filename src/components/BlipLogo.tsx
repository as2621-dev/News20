/**
 * blip wordmark — lowercase `blip` (Inter 800) where the "i" tittle is a dot
 * + 3 white horizontal radar waves rippling right.
 *
 * The arc maths is LOAD-BEARING: radii / angle constants are copied VERBATIM
 * from the prototype `app.js` `blipSignal` IIFE (radii [3.2, 6.2, 9.2], 46°
 * opening → k=cos46°=0.695 / s=sin46°=0.719, cy=7, viewBox "0 0 11 14"). Do
 * not "tidy" these — they reproduce the exact wave geometry.
 *
 * @example
 *   <BlipLogo size={20} />            // chrome wordmark
 *   <BlipLogo size={28} glow />       // splash / onboarding pulse
 */

// --- VERBATIM from prototype app.js `blipSignal` (arc geometry is the spec) ---
const SIGNAL_CENTER_Y = 7;
const SIGNAL_ARC_RADII = [3.2, 6.2, 9.2] as const;
const SIGNAL_COS_46_DEG = 0.695; // cos 46°, opens right
const SIGNAL_SIN_46_DEG = 0.719; // sin 46°

interface BlipSignalArc {
  /** Arc path `d` attribute. */
  arc_path_d: string;
  /** Wave index → drives the `bw1`/`bw2`/`bw3` opacity classes. */
  wave_index: number;
}

/**
 * Build the 3 radar-wave arc paths exactly as the prototype `arc(r, i)` does:
 * each arc opens to the right at 46° around (k·r, cy ± s·r).
 */
function buildSignalArcs(): BlipSignalArc[] {
  return SIGNAL_ARC_RADII.map((radius, wave_index) => {
    const x = (SIGNAL_COS_46_DEG * radius).toFixed(2);
    const y_top = (SIGNAL_CENTER_Y - SIGNAL_SIN_46_DEG * radius).toFixed(2);
    const y_bottom = (SIGNAL_CENTER_Y + SIGNAL_SIN_46_DEG * radius).toFixed(2);
    return {
      arc_path_d: `M${x} ${y_top} A${radius} ${radius} 0 0 1 ${x} ${y_bottom}`,
      wave_index,
    };
  });
}

const SIGNAL_ARCS = buildSignalArcs();

export interface BlipLogoProps {
  /** Wordmark font-size in px (em-scales the tittle dot + waves). */
  size?: number;
  /** When true, the waves + dot pulse like a radar ping (honors reduced-motion). */
  glow?: boolean;
}

/**
 * The blip wordmark. Markup mirrors the prototype `blipLogo(px, cls)` builder
 * byte-for-byte so the `.blip` / `.blip-sig` / `.bw` / `.bdot` CSS (ported into
 * globals.css) styles it identically.
 */
export function BlipLogo({ size = 20, glow = false }: BlipLogoProps) {
  return (
    <span className={`blip${glow ? " glow" : ""}`} style={{ fontSize: `${size}px` }}>
      bl
      <span className="bi">
        {/* dotless i (ı, U+0131) — the tittle is drawn by the signal instead */}
        {"ı"}
        <i className="tittle">
          <b className="bdot" />
          <svg className="blip-sig" viewBox="0 0 11 14" fill="none" aria-hidden="true">
            {SIGNAL_ARCS.map(({ arc_path_d, wave_index }) => (
              <path key={wave_index} className={`bw bw${wave_index + 1}`} d={arc_path_d} />
            ))}
          </svg>
        </i>
      </span>
      p
    </span>
  );
}
