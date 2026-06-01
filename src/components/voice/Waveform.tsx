"use client";

/**
 * Waveform — the five-bar voice signal indicator (Phase 3 SP4).
 *
 * Ports the prototype's `#wave` row of five `.wave-bar` elements (port-map §5.1;
 * prototype `voiceConversation`): five vertical pills centred in the orb whose
 * heights track the live audio amplitude. In the prototype the heights were driven
 * by `Math.random()` on a timer; here they are a pure function of the
 * {@link WaveformProps.amplitude_level} prop so the bars react to the REAL mic /
 * output signal phase-3b feeds in (and so the render is deterministic + testable).
 *
 * **Reactivity contract.** Each bar's height interpolates between a resting
 * minimum and a maximum by `amplitude_level` (0–1), with a fixed per-bar profile
 * (the centre bar tallest) so silence reads as a flat low row and a loud signal
 * fans the bars up — exactly the prototype's `14 + (i%3)*10` shape, now amplitude-
 * scaled. The math lives in the pure {@link waveformBarHeights} so it is unit-
 * testable in isolation.
 *
 * **Reduced motion / inactive.** When `prefers_reduced_motion` is true OR the orb
 * is not actively listening, the bars render at their resting heights (no live
 * height changes) — matching the prototype, whose `animWave()` early-returns under
 * `reduced`.
 *
 * Presentational only — props in, no callbacks, no audio access.
 *
 * @example
 * <Waveform amplitude_level={0.8} is_active />          // bars fanned up
 *
 * @example
 * <Waveform amplitude_level={0.8} is_active={false} />  // resting (flat) bars
 */

import type { OrbVariant } from "@/components/voice/VoiceOrb";

/** Number of bars in the waveform (prototype renders exactly five). */
export const WAVEFORM_BAR_COUNT = 5;

/** Resting (silence / inactive) bar height in px. */
const RESTING_BAR_HEIGHT_PX = 10;
/** Maximum bar height in px at full amplitude (prototype peak ≈ 44px). */
const MAX_BAR_HEIGHT_PX = 44;

/**
 * Per-bar amplitude weight: the centre bar reacts most, the edges least, so a
 * signal fans the row up symmetrically (prototype's `(i % 3) * 10` shape,
 * re-expressed as 0–1 weights for a 5-bar row).
 */
const BAR_AMPLITUDE_WEIGHTS = [0.55, 0.8, 1, 0.8, 0.55] as const;

/**
 * Compute the five bar heights (px) for a given amplitude.
 *
 * Pure + exported so the reactivity contract is testable without rendering: each
 * bar = `resting + clamp(amplitude) * weight * (max - resting)`. At amplitude 0
 * every bar is the resting height (flat row); at amplitude 1 each bar reaches its
 * weighted maximum (centre tallest). Amplitude is clamped to `[0, 1]` so a noisy
 * out-of-range signal can never produce a negative or runaway height.
 *
 * @param amplitude_level - Audio amplitude in `[0, 1]` (clamped if outside).
 * @returns Five bar heights in px, edge → centre → edge order.
 *
 * @example
 * waveformBarHeights(0)   // → [10, 10, 10, 10, 10] (resting / flat)
 *
 * @example
 * waveformBarHeights(1)   // → [28.7, 37.2, 44, 37.2, 28.7] (centre tallest)
 */
export function waveformBarHeights(amplitude_level: number): number[] {
  // Reason: clamp first — a mic feed can momentarily spike >1 or report a tiny
  // negative; either would warp the bars. Heights stay within [resting, max].
  const clampedAmplitude = Math.min(1, Math.max(0, amplitude_level));
  const heightSpan = MAX_BAR_HEIGHT_PX - RESTING_BAR_HEIGHT_PX;
  return BAR_AMPLITUDE_WEIGHTS.map((barWeight) => RESTING_BAR_HEIGHT_PX + clampedAmplitude * barWeight * heightSpan);
}

export interface WaveformProps {
  /**
   * Live audio amplitude in `[0, 1]`. Drives every bar's height (clamped). When
   * omitted or while inactive the bars render at their resting heights.
   */
  amplitude_level?: number;
  /**
   * Whether the orb is actively listening. When false (paused / responding / idle)
   * the bars rest at their minimum regardless of `amplitude_level`, matching the
   * prototype (the waveform only animates while listening).
   */
  is_active?: boolean;
  /**
   * When true (`prefers-reduced-motion`), the bars never react to amplitude — they
   * render flat at the resting height (prototype `animWave` early-returns under
   * reduced motion).
   */
  prefers_reduced_motion?: boolean;
  /**
   * Colour variant, mirrored from the parent {@link VoiceOrb}: `"accent"` fills the
   * bars with the per-story `--accent` (prototype `.wave-bar`); `"brand"` fills
   * them white (prototype `.orb-brand .wave-bar`).
   */
  wave_variant?: OrbVariant;
}

/**
 * Render the five-bar waveform.
 *
 * The bars rest flat unless the orb is actively listening with motion allowed, in
 * which case their heights come from {@link waveformBarHeights}. Each bar keeps the
 * prototype `.wave-bar` class so phase-3b / globals.css can style it.
 */
export function Waveform({
  amplitude_level = 0,
  is_active = false,
  prefers_reduced_motion = false,
  wave_variant = "accent",
}: WaveformProps) {
  // Bars only react when actively listening AND motion is allowed; otherwise rest.
  const effectiveAmplitude = is_active && !prefers_reduced_motion ? amplitude_level : 0;
  const barHeights = waveformBarHeights(effectiveAmplitude);
  const barColor = wave_variant === "brand" ? "#fff" : "var(--accent, #E8B7BC)";

  return (
    <span data-waveform="" aria-hidden="true" className="flex items-center justify-center gap-[3px]">
      {barHeights.map((barHeightPx, barIndex) => (
        <span
          // Reason: fixed five-bar row — index keys are stable here (no reorder).
          // biome-ignore lint/suspicious/noArrayIndexKey: fixed-length static bar row.
          key={barIndex}
          data-wave-bar={barIndex}
          className="wave-bar block w-[4px] rounded-pill transition-[height] duration-100 ease-out"
          style={{ height: `${barHeightPx}px`, background: barColor }}
        />
      ))}
    </span>
  );
}
