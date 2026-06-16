/**
 * SignalOrb — the brand "signal" element used by the source-swipe curtain + done
 * screen (Phase 5c SP-UI).
 *
 * Now a thin wrapper over the shared {@link SignalMark} (the blip wordmark's radar
 * signal, scaled up) so the curtain, the done screen, and the in-reel voice Q&A all
 * render the SAME brand signal. Kept as a named component so its existing callers
 * (`ProfileCurtain`, `SourceSwipe`) don't change. The old cloud-gradient blob it
 * used to render has been retired in favour of the radar-ping mark.
 */

import { SignalMark } from "@/components/SignalMark";

export interface SignalOrbProps {
  /** Mark diameter in px. */
  size: number;
  /** When true, applies the brighter/faster "thinking" pulse. */
  responding?: boolean;
}

/**
 * Render the brand signal mark (white radar pings on the near-black surface).
 *
 * @param props - {@link SignalOrbProps}.
 */
export function SignalOrb({ size, responding = false }: SignalOrbProps) {
  return <SignalMark size={size} variant="brand" responding={responding} />;
}
