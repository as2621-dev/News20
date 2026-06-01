import { describe, expect, it } from "vitest";
import {
  base64FromPcm16,
  downsampleTo16kHz,
  floatToPcm16,
  GEMINI_INPUT_SAMPLE_RATE,
  pcm16FromBase64,
  pcm16ToFloat,
} from "@/lib/voice/audio";

/**
 * Unit tests for the Gemini Live PCM audio helpers (Phase 3 SP3).
 *
 * Rule 9 — these encode WHY the transforms matter, not just their arithmetic:
 *   - Gemini's input contract is FIXED at 16 kHz; the browser captures at its
 *     hardware rate (44.1/48 kHz). If we mislabel 48 kHz audio as 16 kHz, Gemini
 *     hears chipmunk speech and transcribes garbage — so downsampling to EXACTLY
 *     16 kHz (the SP3 DoD item) is the load-bearing behaviour, tested directly.
 *   - PCM16 ↔ float and base64 round-trips must be lossless enough that audio
 *     survives the wire; a broken encoder is silent corruption, not a crash.
 */
describe("downsampleTo16kHz — the mic-input contract (must reach exactly 16 kHz)", () => {
  it("downsamples a 48 kHz frame to 16 kHz (one-third the samples)", () => {
    // WHY: 48 kHz is the most common WebView capture rate. 1s of 48 kHz audio
    // must become 1s of 16 kHz audio — i.e. a third of the sample count — or
    // Gemini will play/transcribe it at the wrong speed.
    const oneSecondAt48k = new Float32Array(48000);
    const result = downsampleTo16kHz(oneSecondAt48k, 48000);
    expect(result.length).toBe(16000);
  });

  it("downsamples a 44.1 kHz frame toward 16 kHz", () => {
    // WHY: the other common hardware rate; floor(44100/ (44100/16000)) == 16000.
    const oneSecondAt44k = new Float32Array(44100);
    const result = downsampleTo16kHz(oneSecondAt44k, 44100);
    expect(result.length).toBe(16000);
  });

  it("returns the input unchanged when already at the target rate (no work)", () => {
    // WHY: a device that already captures at 16 kHz must not be resampled — that
    // would needlessly interpolate and degrade the signal.
    const already16k = new Float32Array([0.1, 0.2, 0.3]);
    const result = downsampleTo16kHz(already16k, GEMINI_INPUT_SAMPLE_RATE);
    expect(result).toBe(already16k);
  });

  it("never upsamples a below-16 kHz source (sends it as-is, no fabricated samples)", () => {
    // WHY: edge case — if a device somehow captures at 8 kHz, inventing samples to
    // hit 16 kHz adds no information and risks artifacts; we pass it through.
    const eightK = new Float32Array(8000);
    const result = downsampleTo16kHz(eightK, 8000);
    expect(result.length).toBe(8000);
  });

  it("preserves a constant DC signal through interpolation", () => {
    // WHY: linear interpolation of a constant must stay that constant — a sanity
    // check that the resampler doesn't distort steady tones.
    const constant = new Float32Array(48000).fill(0.5);
    const result = downsampleTo16kHz(constant, 48000);
    expect(result.every((sample) => Math.abs(sample - 0.5) < 1e-6)).toBe(true);
  });
});

describe("floatToPcm16 / pcm16ToFloat — lossless-enough round-trip + clamping", () => {
  it("maps full-scale floats to the Int16 extremes without overflow", () => {
    // WHY: +1.0 must not wrap to a negative value (the classic off-by-one PCM bug).
    const pcm = floatToPcm16(new Float32Array([1.0, -1.0, 0]));
    expect(pcm[0]).toBe(32767);
    expect(pcm[1]).toBe(-32768);
    expect(pcm[2]).toBe(0);
  });

  it("clamps out-of-range input instead of wrapping it into noise", () => {
    // WHY: a hot mic can momentarily exceed [-1,1]; clamping keeps it as loud
    // audio rather than wrapping to the opposite polarity (audible as a click).
    const pcm = floatToPcm16(new Float32Array([2.0, -2.0]));
    expect(pcm[0]).toBe(32767);
    expect(pcm[1]).toBe(-32768);
  });

  it("round-trips a signal back to approximately the same floats", () => {
    const original = new Float32Array([0.25, -0.5, 0.75, 0]);
    const restored = pcm16ToFloat(floatToPcm16(original));
    for (let index = 0; index < original.length; index += 1) {
      // 16-bit quantization error is ~1/32768.
      expect(Math.abs(restored[index] - original[index])).toBeLessThan(1e-3);
    }
  });
});

describe("base64FromPcm16 / pcm16FromBase64 — wire round-trip (output decode path)", () => {
  it("round-trips PCM16 samples through base64 unchanged", () => {
    // WHY: the input path base64-encodes for realtimeInput; the output path
    // decodes Gemini's inlineData. A broken codec is silent audio corruption.
    const samples = new Int16Array([0, 1, -1, 32767, -32768, 1234, -4321]);
    const restored = pcm16FromBase64(base64FromPcm16(samples));
    expect(Array.from(restored)).toEqual(Array.from(samples));
  });

  it("decodes an empty chunk to zero samples (no crash on a keepalive)", () => {
    const restored = pcm16FromBase64(base64FromPcm16(new Int16Array(0)));
    expect(restored.length).toBe(0);
  });
});
