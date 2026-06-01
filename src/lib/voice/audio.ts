/**
 * Web-Audio helpers for the Gemini Live transport (Phase 3 SP3).
 *
 * Gemini Live has an asymmetric, fixed PCM contract (memory
 * `news20-gemini-live-tts-contract.md` gotcha 5):
 *
 * - **Input (mic → Gemini):** 16 kHz mono **PCM16 LE**. The browser
 *   `AudioContext` ignores a requested `sampleRate` on many devices and captures
 *   at 44.1/48 kHz, so we MUST downsample to 16 kHz **in JS** — sending 48 kHz
 *   labelled as 16 kHz makes Gemini hear chipmunk-speed audio and transcribe
 *   garbage.
 * - **Output (Gemini → speaker):** 24 kHz mono PCM16. Playback runs through a
 *   small **ring-buffer scheduler** (≥2 chunks of lead) so streamed chunks play
 *   gap-free instead of stuttering at every chunk boundary.
 *
 * Everything here is split into PURE functions ({@link downsampleTo16kHz},
 * {@link floatToPcm16}, {@link pcm16ToFloat}, {@link base64FromPcm16},
 * {@link pcm16FromBase64}) that are unit-tested without a real `AudioContext`,
 * plus two thin device wrappers ({@link createMicCapture},
 * {@link createPcmPlayer}) that the {@link import('./useGeminiLive')} hook wires
 * to the WebSocket.
 */

import { logger } from "@/lib/logger";

/** The fixed sample rate Gemini Live expects for **input** (mic) audio. */
export const GEMINI_INPUT_SAMPLE_RATE = 16000;
/** The fixed sample rate Gemini Live streams **output** (model) audio at. */
export const GEMINI_OUTPUT_SAMPLE_RATE = 24000;
/** The mime type Gemini Live requires on each input audio chunk. */
export const GEMINI_INPUT_MIME_TYPE = "audio/pcm;rate=16000";

/**
 * Downsample a mono float32 PCM frame to 16 kHz by linear interpolation.
 *
 * PURE — the unit-testable seam for the most error-prone gotcha (5). Browsers
 * capture at their hardware rate (typically 44.1/48 kHz) regardless of the rate
 * we ask for, so we resample to exactly 16 kHz before sending. Linear
 * interpolation is sufficient for speech and avoids pulling in a resampler dep.
 *
 * @param inputSamples - Mono PCM samples in [-1, 1] at `inputSampleRate`.
 * @param inputSampleRate - The source sample rate (e.g. 48000).
 * @param targetSampleRate - Defaults to {@link GEMINI_INPUT_SAMPLE_RATE} (16000).
 * @returns Mono float32 samples resampled to `targetSampleRate`.
 *
 * @example
 * // 48 kHz → 16 kHz drops the length ~3x
 * downsampleTo16kHz(new Float32Array(48000), 48000).length; // 16000
 */
export function downsampleTo16kHz(
  inputSamples: Float32Array,
  inputSampleRate: number,
  targetSampleRate: number = GEMINI_INPUT_SAMPLE_RATE,
): Float32Array {
  if (inputSampleRate === targetSampleRate) {
    return inputSamples;
  }
  if (inputSampleRate < targetSampleRate) {
    // Reason: never upsample — if a device somehow captures below 16 kHz we send
    // it as-is rather than fabricate samples; Gemini tolerates the rate label.
    return inputSamples;
  }
  const ratio = inputSampleRate / targetSampleRate;
  const outputLength = Math.floor(inputSamples.length / ratio);
  const outputSamples = new Float32Array(outputLength);
  for (let outputIndex = 0; outputIndex < outputLength; outputIndex += 1) {
    const sourcePosition = outputIndex * ratio;
    const lowerIndex = Math.floor(sourcePosition);
    const upperIndex = Math.min(lowerIndex + 1, inputSamples.length - 1);
    const interpolationFraction = sourcePosition - lowerIndex;
    // Reason: linear interpolation between the two nearest source samples.
    outputSamples[outputIndex] =
      inputSamples[lowerIndex] * (1 - interpolationFraction) + inputSamples[upperIndex] * interpolationFraction;
  }
  return outputSamples;
}

/**
 * Convert float32 PCM ([-1, 1]) to 16-bit signed little-endian PCM.
 *
 * PURE. Clamps out-of-range samples so a hot mic can't wrap around into noise.
 *
 * @param floatSamples - Mono float32 samples in [-1, 1].
 * @returns An `Int16Array` of PCM16 samples (LE on every platform JS runs on).
 */
export function floatToPcm16(floatSamples: Float32Array): Int16Array {
  const pcm16 = new Int16Array(floatSamples.length);
  for (let index = 0; index < floatSamples.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, floatSamples[index]));
    // Reason: -1..1 → full Int16 range; negative scale is larger so -1 maps to
    // -32768 and +1 maps to +32767 without overflow.
    pcm16[index] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  return pcm16;
}

/**
 * Convert 16-bit signed PCM back to float32 ([-1, 1]) for Web-Audio playback.
 *
 * PURE — the inverse of {@link floatToPcm16}, used to feed Gemini's 24 kHz
 * output into an `AudioBuffer`.
 *
 * @param pcm16 - PCM16 samples.
 * @returns Mono float32 samples in [-1, 1].
 */
export function pcm16ToFloat(pcm16: Int16Array): Float32Array {
  const floatSamples = new Float32Array(pcm16.length);
  for (let index = 0; index < pcm16.length; index += 1) {
    floatSamples[index] = pcm16[index] / 0x8000;
  }
  return floatSamples;
}

/**
 * Base64-encode PCM16 samples for the `realtimeInput.audio.data` WS field.
 *
 * PURE. Uses `btoa` over the raw bytes (browser) — no `Buffer` dependency.
 *
 * @param pcm16 - PCM16 samples to encode.
 * @returns A base64 string of the little-endian byte stream.
 */
export function base64FromPcm16(pcm16: Int16Array): string {
  const bytes = new Uint8Array(pcm16.buffer, pcm16.byteOffset, pcm16.byteLength);
  let binary = "";
  // Reason: chunk the String.fromCharCode spread so a long buffer doesn't blow
  // the argument-count limit (apply caps around ~65k args).
  const CHUNK_SIZE = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += CHUNK_SIZE) {
    const chunk = bytes.subarray(offset, offset + CHUNK_SIZE);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}

/**
 * Decode a base64 PCM16 chunk from a Gemini `inlineData.data` audio frame.
 *
 * PURE — the inverse of {@link base64FromPcm16}, used on the output path.
 *
 * @param base64 - Base64 of a little-endian PCM16 byte stream.
 * @returns The decoded PCM16 samples.
 */
export function pcm16FromBase64(base64: string): Int16Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  // Reason: reinterpret the byte stream as little-endian 16-bit samples.
  return new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
}

/** A 16 kHz PCM16 mic chunk ready to send, plus its mime type. */
export interface MicAudioChunk {
  /** Base64 of 16 kHz mono PCM16 LE samples. */
  base64Data: string;
  /** Always {@link GEMINI_INPUT_MIME_TYPE}. */
  mimeType: string;
}

/** A live microphone capture session feeding 16 kHz PCM16 chunks to a callback. */
export interface MicCapture {
  /** Stop capture, disconnect nodes, and release the mic track. Idempotent. */
  stop: () => void;
}

/** Inputs to {@link createMicCapture}. */
export interface CreateMicCaptureParams {
  /** A live mic `MediaStream` (already granted via getUserMedia / Capacitor). */
  mediaStream: MediaStream;
  /** Receives each downsampled 16 kHz PCM16 chunk for `realtimeInput`. */
  onAudioChunk: (chunk: MicAudioChunk) => void;
  /** Optional amplitude (0..1, RMS) per frame — drives the waveform UI (SP4). */
  onAmplitude?: (amplitude: number) => void;
}

/**
 * Capture the mic, downsample each frame to 16 kHz PCM16, and emit base64 chunks.
 *
 * Wraps a `ScriptProcessorNode` (broadest WebView support — `AudioWorklet` needs
 * a separate module file that the static export can't easily ship) reading the
 * `AudioContext`'s real `sampleRate`, then {@link downsampleTo16kHz} +
 * {@link floatToPcm16} + {@link base64FromPcm16} per frame. The hook calls
 * `onAudioChunk` straight into the WS `realtimeInput` send.
 *
 * @param params - The mic stream + chunk/amplitude callbacks.
 * @returns A {@link MicCapture} whose `stop()` tears everything down.
 *
 * @example
 * const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
 * const capture = createMicCapture({ mediaStream: stream, onAudioChunk: send });
 * // later: capture.stop();
 */
export function createMicCapture({ mediaStream, onAudioChunk, onAmplitude }: CreateMicCaptureParams): MicCapture {
  const audioContext = new AudioContext();
  const sourceNode = audioContext.createMediaStreamSource(mediaStream);
  // Reason: 4096-frame buffer balances latency vs. callback overhead; mono in/out.
  const processorNode = audioContext.createScriptProcessor(4096, 1, 1);

  processorNode.onaudioprocess = (event: AudioProcessingEvent): void => {
    const inputFrame = event.inputBuffer.getChannelData(0);
    const downsampled = downsampleTo16kHz(inputFrame, audioContext.sampleRate);
    const pcm16 = floatToPcm16(downsampled);
    onAudioChunk({ base64Data: base64FromPcm16(pcm16), mimeType: GEMINI_INPUT_MIME_TYPE });

    if (onAmplitude) {
      let sumOfSquares = 0;
      for (let index = 0; index < inputFrame.length; index += 1) {
        sumOfSquares += inputFrame[index] * inputFrame[index];
      }
      onAmplitude(Math.sqrt(sumOfSquares / inputFrame.length));
    }
  };

  sourceNode.connect(processorNode);
  // Reason: a ScriptProcessorNode only fires onaudioprocess while connected to a
  // destination; the gain stays at 0 so the mic is never echoed to the speaker.
  const silentGain = audioContext.createGain();
  silentGain.gain.value = 0;
  processorNode.connect(silentGain);
  silentGain.connect(audioContext.destination);

  let isStopped = false;
  return {
    stop(): void {
      if (isStopped) {
        return;
      }
      isStopped = true;
      processorNode.onaudioprocess = null;
      try {
        sourceNode.disconnect();
        processorNode.disconnect();
        silentGain.disconnect();
        for (const track of mediaStream.getTracks()) {
          track.stop();
        }
        void audioContext.close();
      } catch (stopError) {
        logger.warn("voice_mic_capture_stop_error", {
          error_message: stopError instanceof Error ? stopError.message : "unknown",
          fix_suggestion: "Mic teardown raced a context close; safe to ignore once.",
        });
      }
    },
  };
}

/** Streams Gemini's 24 kHz PCM16 output chunks to the speaker, gap-free. */
export interface PcmPlayer {
  /** Enqueue a base64 24 kHz PCM16 chunk; scheduled after the previous chunk. */
  enqueueBase64Chunk: (base64Data: string) => void;
  /** Drop any not-yet-played chunks (barge-in / turn interrupted). */
  clear: () => void;
  /** Release the playback `AudioContext`. Idempotent. */
  close: () => void;
}

/**
 * Build a ring-buffer scheduler that plays streamed 24 kHz PCM16 chunks gap-free.
 *
 * Each chunk is decoded to an `AudioBuffer` and scheduled at the running
 * `nextStartTime` cursor, which advances by each buffer's duration — so chunks
 * butt up against each other with no audible gap even though they arrive
 * asynchronously over the WS (gotcha 5: keep ≥2 chunks of lead). A small lead
 * offset is added on the first chunk so the scheduler always stays ahead of the
 * clock.
 *
 * @param outputSampleRate - Defaults to {@link GEMINI_OUTPUT_SAMPLE_RATE} (24000).
 * @returns A {@link PcmPlayer}.
 *
 * @example
 * const player = createPcmPlayer();
 * player.enqueueBase64Chunk(chunk1);
 * player.enqueueBase64Chunk(chunk2); // plays right after chunk1
 */
export function createPcmPlayer(outputSampleRate: number = GEMINI_OUTPUT_SAMPLE_RATE): PcmPlayer {
  // Reason: Gemini streams 24 kHz; create the context AT that rate so the buffers
  // play at true pitch without an extra resample.
  const audioContext = new AudioContext({ sampleRate: outputSampleRate });
  let nextStartTime = 0;
  const scheduledSources = new Set<AudioBufferSourceNode>();
  // Reason: ≥2 chunks of lead so the scheduler never falls behind the clock and
  // produces a click; ~120ms is comfortably above the WS chunk cadence.
  const SCHEDULE_LEAD_SECONDS = 0.12;

  return {
    enqueueBase64Chunk(base64Data: string): void {
      const pcm16 = pcm16FromBase64(base64Data);
      if (pcm16.length === 0) {
        return;
      }
      const floatSamples = pcm16ToFloat(pcm16);
      const audioBuffer = audioContext.createBuffer(1, floatSamples.length, outputSampleRate);
      audioBuffer.getChannelData(0).set(floatSamples);

      const sourceNode = audioContext.createBufferSource();
      sourceNode.buffer = audioBuffer;
      sourceNode.connect(audioContext.destination);

      const now = audioContext.currentTime;
      // Reason: if the queue drained (cursor fell behind), restart the cursor a
      // lead-offset ahead of now; otherwise chain after the previous chunk.
      const startAt = Math.max(nextStartTime, now + SCHEDULE_LEAD_SECONDS);
      sourceNode.start(startAt);
      nextStartTime = startAt + audioBuffer.duration;

      scheduledSources.add(sourceNode);
      sourceNode.onended = (): void => {
        scheduledSources.delete(sourceNode);
      };
    },
    clear(): void {
      for (const sourceNode of scheduledSources) {
        try {
          sourceNode.stop();
        } catch {
          // Reason: stopping a source that never started throws; ignore.
        }
      }
      scheduledSources.clear();
      nextStartTime = 0;
    },
    close(): void {
      this.clear();
      void audioContext.close();
    },
  };
}
