"use client";

/**
 * VoiceOrb — the shared listening/responding orb (Phase 3 SP4).
 *
 * The single visual template mounted twice (in-news Voice mode, port-map §2 row 7
 * / §5): the prototype `.orb` / `.orb-brand` / `.orb.listening` / `.orb.responding`
 * contract ported verbatim so phase-3b styles it through `globals.css` and the
 * `--accent` per-story cascade (port-map §4) without re-deriving anything.
 *
 * **The mic is folded INTO the orb — there is NO separate mic button** (port-map
 * §5.1). Tapping the orb toggles pause/resume: an *animating* orb (`listening` /
 * `responding`) means the conversation is live; a *still* orb (`idle` / `paused`)
 * means it is paused. The tap fires {@link VoiceOrbProps.onPauseToggle}.
 *
 * **State → prototype class contract:**
 * | `orb_state`  | classes emitted        | animation                       |
 * | ------------ | ---------------------- | ------------------------------- |
 * | `idle`       | `orb`                  | none (still)                    |
 * | `listening`  | `orb listening`        | `.orb-ring` pulse-ring          |
 * | `responding` | `orb responding`       | orb-throb                       |
 * | `paused`     | `orb`                  | none (still — tapped to resume) |
 *
 * **Reduced motion** (port-map §3.3): when `prefers_reduced_motion` is true the
 * animating `listening` / `responding` classes are NOT emitted at all (the orb
 * stays on the static `orb` class), so a headless render emits zero animation
 * classes — matching the prototype's media-query suppression but assertable in JS.
 *
 * Presentational only: no Gemini / WebSocket logic lives here (that is SP3's
 * `src/lib/voice/*`). Props in, one callback out.
 *
 * @example
 * <VoiceOrb orb_state="listening" onPauseToggle={() => toggleConversation()} />
 *
 * @example
 * // Brand (white) variant, reduced motion — renders static `orb orb-brand`.
 * <VoiceOrb orb_state="responding" orb_variant="brand" prefers_reduced_motion />
 */

import { Waveform } from "@/components/voice/Waveform";

/** The four orb states (prototype `.orb` / `.orb.listening` / `.orb.responding`). */
export type OrbState = "idle" | "listening" | "responding" | "paused";

/** The orb colour variant: per-story accent (`orb`) or brand white (`orb-brand`). */
export type OrbVariant = "accent" | "brand";

export interface VoiceOrbProps {
  /**
   * The conversation state driving the orb's classes + animation. `listening` /
   * `responding` animate; `idle` / `paused` are still.
   */
  orb_state: OrbState;
  /**
   * Colour variant. `"accent"` (default) uses the per-story `--accent` cascade
   * (prototype `.orb`); `"brand"` is the white variant (prototype `.orb-brand`).
   */
  orb_variant?: OrbVariant;
  /**
   * Live mic/output amplitude (0–1) forwarded to the inner {@link Waveform} so the
   * five bars react to the voice signal. Optional — omit to render flat bars.
   */
  amplitude_level?: number;
  /**
   * When true (`prefers-reduced-motion`), the animating `listening` / `responding`
   * classes are suppressed so the orb never pulses or throbs. The waveform is also
   * told to stay flat.
   */
  prefers_reduced_motion?: boolean;
  /**
   * Fired when the user taps the orb (mic-in-orb pause/resume, port-map §5.1).
   * The parent flips the conversation between live and paused.
   */
  onPauseToggle?: () => void;
  /**
   * Accessible label for the tap target. Defaults to a pause/resume label derived
   * from `orb_state` so the control announces its action to screen readers.
   */
  tap_aria_label?: string;
}

/**
 * Map an orb state to the prototype animation class, honouring reduced motion.
 *
 * Pure + exported so the reduced-motion DoD (`emits NO animation classes`) is
 * assertable without rendering: `idle` / `paused` are always still; `listening` /
 * `responding` add their animation class ONLY when motion is allowed.
 *
 * @param orb_state - The current conversation state.
 * @param prefers_reduced_motion - Whether the OS/user prefers reduced motion.
 * @returns The animation class to append (`"listening"`, `"responding"`, or `""`).
 *
 * @example
 * orbAnimationClass("listening", false) // → "listening"
 * orbAnimationClass("listening", true)  // → "" (suppressed)
 * orbAnimationClass("paused", false)    // → "" (still by design)
 */
export function orbAnimationClass(orb_state: OrbState, prefers_reduced_motion: boolean): string {
  if (prefers_reduced_motion) {
    // Reason: reduced motion must emit ZERO animation classes (DoD) — the orb
    // falls back to the static `orb` base, mirroring the prototype media query.
    return "";
  }
  if (orb_state === "listening") {
    return "listening";
  }
  if (orb_state === "responding") {
    return "responding";
  }
  // idle + paused are intentionally still (animating = live, still = paused).
  return "";
}

/** Default accessible label for the tap target, derived from the orb state. */
function defaultTapLabel(orb_state: OrbState): string {
  const isLive = orb_state === "listening" || orb_state === "responding";
  return isLive ? "Pause voice conversation" : "Resume voice conversation";
}

/**
 * Render the tappable voice orb.
 *
 * Renders the prototype `.orb` (+ `.orb-brand` / `.listening` / `.responding`)
 * class contract plus the absolutely-positioned `.orb-ring` and an inner
 * {@link Waveform}. The whole orb is the tap target (mic-in-orb): a `≥44px`
 * accessible `button` firing {@link VoiceOrbProps.onPauseToggle}.
 */
export function VoiceOrb({
  orb_state,
  orb_variant = "accent",
  amplitude_level,
  prefers_reduced_motion = false,
  onPauseToggle,
  tap_aria_label,
}: VoiceOrbProps) {
  const animationClass = orbAnimationClass(orb_state, prefers_reduced_motion);
  const variantClass = orb_variant === "brand" ? "orb-brand" : "";
  // Prototype contract: `orb` base + optional `orb-brand` + the animation class.
  const orbClassName = ["orb", variantClass, animationClass].filter(Boolean).join(" ");
  const isListening = orb_state === "listening";
  // The orb is a toggle: "pressed" reads as the conversation being LIVE
  // (listening/responding), so a screen reader announces tap → pause/resume.
  const isConversationLive = orb_state === "listening" || orb_state === "responding";

  return (
    <button
      type="button"
      data-voice-orb={orb_state}
      data-orb-variant={orb_variant}
      aria-label={tap_aria_label ?? defaultTapLabel(orb_state)}
      aria-pressed={isConversationLive}
      onClick={onPauseToggle}
      // 168px circle (prototype `.orb`), accent radial gradient via --accent.
      // The whole disc is the hit target (≥44px); the inner ring + waveform are
      // pointer-transparent so taps always land on the orb.
      className={`${orbClassName} relative grid h-[168px] w-[168px] place-items-center rounded-pill outline-none transition-transform focus-visible:ring-2 focus-visible:ring-white/60 active:scale-[0.97]`}
      style={
        orb_variant === "brand"
          ? {
              background: "radial-gradient(circle at 50% 40%, rgba(255,255,255,0.20) 0%, #06070d 72%)",
              boxShadow: "0 0 80px -14px rgba(255,255,255,0.55), inset 0 0 50px rgba(0,0,0,0.6)",
            }
          : {
              background:
                "radial-gradient(circle at 50% 40%, color-mix(in oklab, var(--accent, #E8B7BC) 55%, #000) 0%, #05060c 72%)",
              boxShadow: "0 0 80px -10px var(--accent, #E8B7BC), inset 0 0 50px rgba(0,0,0,0.6)",
            }
      }
    >
      {/* The pulse-ring (prototype `.orb-ring`): styled by globals.css /
          phase-3b. Pointer-transparent so taps fall through to the orb. */}
      <span
        data-orb-ring=""
        aria-hidden="true"
        className="orb-ring pointer-events-none absolute -inset-[18px] rounded-pill border"
        style={{
          borderColor:
            orb_variant === "brand"
              ? "rgba(255,255,255,0.45)"
              : "color-mix(in oklab, var(--accent, #E8B7BC) 40%, transparent)",
        }}
      />
      {/* Five-bar waveform centred in the orb (prototype `#wave`). Only animates
          while listening; otherwise flat. Pointer-transparent. */}
      <span className="pointer-events-none absolute inset-0 grid place-items-center">
        <Waveform
          amplitude_level={amplitude_level}
          is_active={isListening}
          prefers_reduced_motion={prefers_reduced_motion}
          wave_variant={orb_variant}
        />
      </span>
    </button>
  );
}
