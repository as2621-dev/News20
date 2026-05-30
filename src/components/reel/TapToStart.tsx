"use client";

/**
 * TapToStart — the first-run audio-unlock overlay (port-map §6; ports the
 * prototype `#tap-start` / `firstStart()`).
 *
 * **Why it exists (iOS muted-autoplay reality).** iOS WebView allows muted
 * autoplay but blocks audio playback until a real user gesture. This full-screen
 * tappable overlay IS that gate: nothing plays until the user taps it. On tap it
 * calls {@link TapToStartProps.onStart}, which the reel wires to
 * `audio.play()` **inside this gesture** (the only place iOS will honour it),
 * unlock the audio element, and move the machine to `playing`. The overlay is the
 * brand-forward first frame: the `blip` wordmark, a play ring, and the
 * "tap to start your briefing" affordance.
 *
 * The whole surface is the button (the prototype tapped anywhere on the reel to
 * start), so the gesture is impossible to miss.
 */
import { BlipLogo } from "@/components/BlipLogo";

export interface TapToStartProps {
  /**
   * Fired on the first tap. The reel handler MUST start audio playback inside
   * this call (synchronously, in the gesture) to unlock the iOS audio element.
   */
  onStart: () => void;
}

/**
 * Render the tap-to-start gate. A single full-screen `<button>` so the audio
 * unlock fires from a genuine user gesture anywhere on the surface.
 */
export function TapToStart({ onStart }: TapToStartProps) {
  return (
    <button
      type="button"
      onClick={onStart}
      aria-label="Tap to start your briefing"
      className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-5 bg-background/55 px-10 text-center backdrop-blur-[2px]"
    >
      <span className="mb-1">
        <BlipLogo size={30} glow />
      </span>

      {/* play ring affordance (ported from #play-ring) */}
      <span className="grid h-20 w-20 place-items-center rounded-pill border border-white/30 bg-white/5">
        <svg
          width={30}
          height={30}
          viewBox="0 0 24 24"
          fill="currentColor"
          aria-hidden="true"
          className="translate-x-[2px] text-white"
        >
          <path d="M8 5v14l11-7z" />
        </svg>
      </span>

      <span>
        <span className="block font-sans text-[15px] font-semibold text-white">Tap to start your briefing</span>
        <span className="mt-1.5 block font-mono text-[10px] tracking-wide text-white/50">
          AUDIO ON · WORD-BY-WORD CAPTIONS
        </span>
      </span>
    </button>
  );
}
