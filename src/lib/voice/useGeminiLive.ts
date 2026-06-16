/**
 * `useGeminiLive` — the parameterized raw-WebSocket React hook driving the
 * Gemini Live realtime voice transport (Phase 3 SP3).
 *
 * This is the **one brain** that in-news Voice mode (phase-3b) mounts and
 * configures via `systemInstruction` + `tools`; the hook itself is feature-blind.
 * It ports the seven hard-won gotchas from memory
 * `news20-gemini-live-tts-contract.md` VERBATIM:
 *
 * 1. **Ephemeral token, key off-device.** The hook NEVER sees `GEMINI_API_KEY`;
 *    it `POST`s {@link MINT_TOKEN_PATH} to the worker, which mints an
 *    `auth_tokens/...` token (SP3 `agents/voice/live_token.py`).
 * 2. **Constrained endpoint.** With an `auth_tokens/...` token the WSS MUST be
 *    `...GenerativeService.BidiGenerateContentConstrained`, token passed via
 *    `?access_token=<name>` (the unconstrained endpoint → HTTP 101 then a silent
 *    drop).
 * 3. **`setup` then wait for `setupComplete`.** The client still sends a `setup`
 *    frame (model, AUDIO modality, voice, systemInstruction, tools, in/out
 *    transcription) and must NOT send any audio until `{setupComplete}` arrives.
 *    `speechConfig` lives INSIDE `generationConfig` — at the `setup` top level
 *    the v1alpha constrained endpoint closes with 1007 `Unknown name "speechConfig"`.
 * 4. **Greeting nudge.** Auto-VAD waits for user audio, so we force the model's
 *    first line with a `clientContent` user turn (`turnComplete:true`).
 * 5. **Asymmetric PCM** (handled in `./audio`): input 16 kHz, output 24 kHz.
 * 6. **Frame normalization.** Server frames arrive as `string | Blob |
 *    ArrayBuffer`; normalize to text before `JSON.parse`, then route on the
 *    documented keys.
 * 7. **Single-use token + double-connect guard.** The `uses:1` token means React
 *    19 StrictMode's double-mount must open EXACTLY ONE socket; function
 *    round-trips reply `{toolResponse:{functionResponses:[{id,name,response}]}}`.
 * 8. **Gesture-synchronous AudioContexts (iOS WebKit).** Both AudioContexts (mic
 *    capture + 24 kHz player) and the `getUserMedia` call MUST start
 *    synchronously at the head of `connect()` — i.e. inside the user tap.
 *    Created after an `await` (token mint / setupComplete) they start
 *    `'suspended'` on iOS, the ScriptProcessorNode never fires, ZERO mic chunks
 *    are sent, and the model never answers — while the greeting still plays
 *    (the symptom: one-way audio). `startMicAndGreeting` then consumes the
 *    pre-acquired stream/context after `setupComplete`.
 *
 * Mic capture + downsample + 24 kHz ring-buffer playback live in `./audio`; this
 * file owns only the WS lifecycle, the setup handshake, and frame routing.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { logger } from "@/lib/logger";
import { createMicCapture, createPcmPlayer, type MicCapture, type PcmPlayer } from "@/lib/voice/audio";

/** The default Gemini Live model (native-audio preview — gotcha intro). */
export const GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025";
/** The default prebuilt voice for Live sessions (also the fallback when a preferred voice is rejected). */
export const GEMINI_LIVE_DEFAULT_VOICE = "Charon";
/**
 * Jordan's voice — the SAME prebuilt voice (`Sadaltager`) the pre-rendered
 * story digests bind to JORDAN (agents/voice/gemini_tts.py VOICE_MAP_GEMINI),
 * so the live Q&A host sounds like the Jordan the user just heard.
 */
export const GEMINI_LIVE_JORDAN_VOICE = "Sadaltager";
/** Worker route that mints the ephemeral token (SP3 `agents/worker/main.py`). */
export const MINT_TOKEN_PATH = "/api/voice/live-token";
/**
 * The CONSTRAINED Bidi WSS base (gotcha 2). The unconstrained
 * `BidiGenerateContent` endpoint silently drops `auth_tokens/...` tokens.
 */
export const GEMINI_LIVE_WSS_BASE =
  "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained";

/** A function declaration the model may call (gotcha 7 round-trip). */
export interface GeminiToolDeclaration {
  /** The function name the model emits in `toolCall.functionCalls[].name`. */
  name: string;
  /** Human/model-readable description of when to call it. */
  description: string;
  /** JSON-schema-shaped parameter spec (Gemini `parameters`). */
  parameters?: Record<string, unknown>;
}

/** A model→client function call to fulfil (gotcha 7). */
export interface GeminiToolCall {
  /** The call id to echo back in the `functionResponses[]` entry. */
  id: string;
  /** The function name the model invoked. */
  name: string;
  /** The arguments the model supplied. */
  args: Record<string, unknown>;
}

/** Connection lifecycle states the consuming UI (orb) reflects. */
export type GeminiLiveStatus = "idle" | "connecting" | "live" | "closed" | "error";

/** Inputs to {@link useGeminiLive} — the parameterization SP3b/SP3 configure. */
export interface UseGeminiLiveParams {
  /** The system prompt scoping the session (e.g. the active story's grounding). */
  systemInstruction: string;
  /** Function declarations the model may call (e.g. the grounded-answer tool). */
  tools?: GeminiToolDeclaration[];
  /** Receives input (user) and output (model) transcription text as it streams. */
  onTranscript?: (transcript: { role: "user" | "model"; text: string }) => void;
  /**
   * Fulfils a model function call. Return the `response` object; the hook posts
   * it back as `{toolResponse:{functionResponses:[{id,name,response}]}}`.
   */
  onToolCall?: (toolCall: GeminiToolCall) => Promise<Record<string, unknown>> | Record<string, unknown>;
  /** The prebuilt voice name. Defaults to {@link GEMINI_LIVE_DEFAULT_VOICE}. */
  voiceName?: string;
  /** The Live model id. Defaults to {@link GEMINI_LIVE_MODEL}. */
  model?: string;
  /**
   * The greeting nudge text (gotcha 4) — a `clientContent` user turn that forces
   * the model to speak first. Defaults to a generic opener.
   */
  greetingNudge?: string;
  /**
   * Called when mic capture could not start AFTER the session connected (the
   * greeting may already be audible). The hook also disconnects and sets status
   * `"error"` so the orb never fakes LISTENING on a deaf session.
   */
  onMicError?: (errorMessage: string) => void;
}

/** What {@link useGeminiLive} hands back to a Voice-mode component. */
export interface GeminiLiveController {
  /** Current connection lifecycle status (drives the orb states). */
  status: GeminiLiveStatus;
  /** True once `{setupComplete}` has arrived and audio may flow. */
  isSetupComplete: boolean;
  /** Latest RMS amplitude (0..1) of mic input — drives the waveform UI. */
  inputAmplitude: number;
  /** Open the token mint → WSS → setup handshake (call inside a user gesture). */
  connect: () => Promise<void>;
  /** Tear down the socket, mic, and playback. Idempotent. */
  disconnect: () => void;
}

/**
 * Build the well-formed `setup` frame (gotcha 3).
 *
 * PURE and exported so a test can assert the EXACT shape the contract requires
 * without standing up a socket: AUDIO modality, the prebuilt voice, the caller's
 * systemInstruction + tools, and BOTH input/output transcription enabled.
 *
 * @param params - The model/voice/instruction/tools to encode.
 * @returns The `{ setup: {...} }` object to JSON-send as the first WS frame.
 *
 * The session also pins a low `temperature` so the corpus-in-context voice path
 * answers faithfully from the injected STORY CONTEXT (mirrors the server's
 * `ANSWER_TEMPERATURE = 0.2` on the text Q&A path).
 *
 * @example
 * buildSetupFrame({ model: "m", voiceName: "Charon", systemInstruction: "hi", tools: [] }).setup.generationConfig.responseModalities;
 * // ["AUDIO"]
 */
export function buildSetupFrame(params: {
  model: string;
  voiceName: string;
  systemInstruction: string;
  tools: GeminiToolDeclaration[];
}): {
  setup: {
    model: string;
    generationConfig: {
      responseModalities: string[];
      temperature: number;
      speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: string } } };
    };
    systemInstruction: { parts: { text: string }[] };
    tools: { functionDeclarations: GeminiToolDeclaration[] }[];
    inputAudioTranscription: Record<string, never>;
    outputAudioTranscription: Record<string, never>;
  };
} {
  return {
    setup: {
      // Reason: the model id MUST be prefixed with `models/` in the setup frame.
      model: params.model.startsWith("models/") ? params.model : `models/${params.model}`,
      generationConfig: {
        responseModalities: ["AUDIO"],
        // Reason: pin a low temperature so the corpus-in-context voice path answers
        // faithfully from the injected STORY CONTEXT (mirrors the server's
        // ANSWER_TEMPERATURE = 0.2 on the text Q&A path). The v1alpha constrained
        // endpoint accepts temperature inside generationConfig alongside speechConfig;
        // if it ever 1007-rejects it, move/remove it (verified by the manual voice eval).
        temperature: 0.2,
        // Reason: speechConfig MUST sit INSIDE generationConfig on the v1alpha
        // constrained endpoint — at the `setup` top level the server closes the
        // socket with 1007 `Unknown name "speechConfig" at 'setup'`.
        speechConfig: {
          voiceConfig: { prebuiltVoiceConfig: { voiceName: params.voiceName } },
        },
      },
      systemInstruction: { parts: [{ text: params.systemInstruction }] },
      // Reason: even an empty tool set is sent as a single declarations group so
      // the frame shape is stable; the model just never emits a toolCall.
      tools: [{ functionDeclarations: params.tools }],
      // Reason: enabling both transcriptions (empty config objects) is what makes
      // Gemini stream `inputTranscription`/`outputTranscription` text frames.
      inputAudioTranscription: {},
      outputAudioTranscription: {},
    },
  };
}

/** Shape of the relevant server frame fields after normalization (gotcha 6). */
interface GeminiServerFrame {
  setupComplete?: unknown;
  serverContent?: {
    modelTurn?: { parts?: { inlineData?: { data?: string } }[] };
    inputTranscription?: { text?: string };
    outputTranscription?: { text?: string };
    turnComplete?: boolean;
  };
  toolCall?: { functionCalls?: { id?: string; name?: string; args?: Record<string, unknown> }[] };
  goAway?: unknown;
  error?: unknown;
}

/**
 * Normalize a raw WS message (`string | Blob | ArrayBuffer`) to text (gotcha 6).
 *
 * Gemini sends JSON over the binary channel on some platforms, so frames may
 * arrive as a `Blob` or `ArrayBuffer`; both decode as UTF-8 JSON. Returns the
 * text, or `null` if it can't be decoded (caller skips the frame).
 *
 * @param raw - The `MessageEvent.data` payload.
 * @returns The JSON text, or `null`.
 */
export async function normalizeFrameToText(raw: unknown): Promise<string | null> {
  if (typeof raw === "string") {
    return raw;
  }
  // Reason: `instanceof ArrayBuffer` is unreliable across realms (jsdom, worker,
  // some WebView builds), so duck-type on the tag + an ArrayBuffer view instead.
  if (raw instanceof ArrayBuffer || Object.prototype.toString.call(raw) === "[object ArrayBuffer]") {
    return new TextDecoder().decode(raw as ArrayBuffer);
  }
  if (ArrayBuffer.isView(raw)) {
    return new TextDecoder().decode(raw as ArrayBufferView);
  }
  // Reason: Blob is async; `instanceof Blob` is unavailable in some test envs, so
  // duck-type on the `.text()` method.
  if (raw && typeof (raw as { text?: unknown }).text === "function") {
    return (raw as Blob).text();
  }
  return null;
}

/**
 * Drive a Gemini Live realtime voice session over a raw WebSocket.
 *
 * Parameterized by `systemInstruction` + `tools` so phase-3b configures the brain
 * without forking the transport. The whole hard contract (gotchas 1–7) lives
 * here; `connect()` runs the token mint → constrained WSS → `setup`/`setupComplete`
 * → greeting nudge → mic stream, and routes every server frame to the caller's
 * callbacks. The `connectGuardRef` ensures React 19 StrictMode's double-mount
 * opens EXACTLY ONE socket (gotcha 7 — the `uses:1` token would 401 on a second).
 *
 * @param params - {@link UseGeminiLiveParams}.
 * @returns A {@link GeminiLiveController}.
 *
 * @example
 * const live = useGeminiLive({ systemInstruction: storyPrompt, tools: [answerTool], onToolCall });
 * // <button onClick={() => live.connect()}>Talk</button>
 */
export function useGeminiLive(params: UseGeminiLiveParams): GeminiLiveController {
  const {
    systemInstruction,
    tools = [],
    onTranscript,
    onToolCall,
    voiceName = GEMINI_LIVE_DEFAULT_VOICE,
    model = GEMINI_LIVE_MODEL,
    greetingNudge = "Say a brief, friendly hello and ask what I'd like to know.",
    onMicError,
  } = params;

  const [status, setStatus] = useState<GeminiLiveStatus>("idle");
  const [isSetupComplete, setIsSetupComplete] = useState<boolean>(false);
  const [inputAmplitude, setInputAmplitude] = useState<number>(0);

  const socketRef = useRef<WebSocket | null>(null);
  const micCaptureRef = useRef<MicCapture | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  // Reason (gotcha 8): the mic AudioContext + getUserMedia promise are acquired
  // synchronously inside connect()'s user gesture and consumed later (after
  // setupComplete) by startMicAndGreeting.
  const micAudioContextRef = useRef<AudioContext | null>(null);
  const pendingMicStreamRef = useRef<Promise<MediaStream> | null>(null);
  // Reason (gotcha 7): the single-use token means StrictMode's double-invoke must
  // open EXACTLY ONE socket. Two guards cover both StrictMode behaviours:
  //   - `connectGuardRef` short-circuits a second `connect()` within one mount;
  //   - `connectEpochRef` is bumped on every teardown (disconnect/unmount). Each
  //     `connect()` captures the epoch BEFORE its async token mint and, after the
  //     await, bails if the epoch changed — so the StrictMode FIRST mount (whose
  //     cleanup bumped the epoch) never constructs a socket, while the remounted
  //     instance opens the single allowed one. Refs are shared across StrictMode's
  //     simulated remount, so an epoch (not a boolean) is required to tell the two
  //     in-flight connects apart.
  const connectGuardRef = useRef<boolean>(false);
  const connectEpochRef = useRef<number>(0);
  // Keep the latest callbacks in refs so the frame router never goes stale and
  // the connect callback identity stays stable.
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;
  const onToolCallRef = useRef(onToolCall);
  onToolCallRef.current = onToolCall;
  const onMicErrorRef = useRef(onMicError);
  onMicErrorRef.current = onMicError;
  // Reason: setupComplete is read inside the message closure; a ref avoids a
  // stale-state race between the gate check and the React state update, and lets
  // the gate fire exactly once per session.
  const isSetupCompleteRef = useRef<boolean>(false);

  // Release the gesture-pre-acquired audio resources (gotcha 8): the mic
  // AudioContext (when not yet owned by a MicCapture — its stop() closes it),
  // the PCM player, and the pending getUserMedia stream's tracks. Idempotent;
  // double-close is guarded/caught.
  const releasePreacquiredAudio = useCallback((): void => {
    const micAudioContext = micAudioContextRef.current;
    micAudioContextRef.current = null;
    if (micAudioContext && micAudioContext.state !== "closed") {
      void micAudioContext.close().catch(() => {
        // Reason: a context already closed by MicCapture.stop() rejects; benign.
      });
    }
    playerRef.current?.close();
    playerRef.current = null;
    const pendingMicStream = pendingMicStreamRef.current;
    pendingMicStreamRef.current = null;
    if (pendingMicStream) {
      pendingMicStream
        .then((stream) => {
          for (const track of stream.getTracks()) {
            track.stop();
          }
        })
        .catch(() => {
          // Reason: a rejected getUserMedia has no tracks to stop.
        });
    }
  }, []);

  const disconnect = useCallback((): void => {
    connectGuardRef.current = false;
    // Reason (gotcha 7): bump the epoch so any connect still awaiting its token
    // mint sees a changed epoch after the await and aborts before opening a socket.
    // INVARIANT (gotcha 8): every epoch bump releases the pre-acquired audio —
    // the stale-epoch bail in connect() relies on this and must NOT release
    // (the shared refs may already hold a NEWER connect's resources).
    connectEpochRef.current += 1;
    micCaptureRef.current?.stop();
    micCaptureRef.current = null;
    releasePreacquiredAudio();
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket && socket.readyState <= WebSocket.OPEN) {
      socket.close();
    }
    isSetupCompleteRef.current = false;
    setIsSetupComplete(false);
    setStatus("closed");
  }, [releasePreacquiredAudio]);

  // Start mic capture + send the greeting nudge — ONLY after setupComplete (so
  // audio never precedes the handshake, gotcha 3). Consumes the stream/context
  // pre-acquired inside connect()'s user gesture (gotcha 8). Defined before
  // `connect` so it is a stable dependency of it.
  const startMicAndGreeting = useCallback(
    (socket: WebSocket): void => {
      // Reason (gotcha 4): auto-VAD waits for user audio, so force the model's
      // first line with a clientContent user turn (turnComplete:true).
      socket.send(
        JSON.stringify({
          clientContent: {
            turns: [{ role: "user", parts: [{ text: greetingNudge }] }],
            turnComplete: true,
          },
        }),
      );

      void (async (): Promise<void> => {
        try {
          // Reason (gotcha 8): consume the gesture-pre-acquired stream; the
          // direct getUserMedia is a non-iOS/legacy-caller fallback only.
          const mediaStream = await (pendingMicStreamRef.current ??
            navigator.mediaDevices.getUserMedia({ audio: true }));
          const micAudioContext = micAudioContextRef.current ?? undefined;
          if (micAudioContext && micAudioContext.state !== "running") {
            // Belt-and-suspenders: the gesture's resume() should have landed.
            await micAudioContext.resume().catch(() => {});
          }
          logger.info("voice_live_mic_starting", {
            mic_context_state: micAudioContext?.state ?? "none_preacquired",
            socket_ready_state: socket.readyState,
          });
          let hasSentFirstChunk = false;
          let hasWarnedDroppedChunk = false;
          micCaptureRef.current = createMicCapture({
            mediaStream,
            audioContext: micAudioContext,
            onAmplitude: setInputAmplitude,
            onAudioChunk: (chunk) => {
              // Reason (gotcha 5): input is 16 kHz mono PCM16, base64, as realtimeInput.
              const liveSocket = socketRef.current;
              if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN) {
                // Reason (Rule 12): WebSocket.send() on a non-OPEN socket drops the
                // frame SILENTLY — warn once per session instead of vanishing audio.
                if (!hasWarnedDroppedChunk) {
                  hasWarnedDroppedChunk = true;
                  logger.warn("voice_live_send_dropped_socket_not_open", {
                    frame_kind: "realtime_audio",
                    ready_state: liveSocket?.readyState ?? -1,
                    fix_suggestion: "Socket closed under an active mic; expect a reconnect or teardown.",
                  });
                }
                return;
              }
              if (!hasSentFirstChunk) {
                hasSentFirstChunk = true;
                logger.info("voice_live_first_audio_chunk_sent", { socket_ready_state: liveSocket.readyState });
              }
              liveSocket.send(
                JSON.stringify({
                  realtimeInput: { audio: { mimeType: chunk.mimeType, data: chunk.base64Data } },
                }),
              );
            },
          });
        } catch (micError) {
          const errorMessage = micError instanceof Error ? micError.message : "unknown";
          logger.error("voice_live_mic_failed", {
            error_message: errorMessage,
            fix_suggestion: "Mic permission denied or unavailable; show the mic-denied fallback.",
          });
          // Reason (Rule 12): a deaf session must not pose as LISTENING — surface
          // the failure, tear down, and land on the error state.
          onMicErrorRef.current?.(errorMessage);
          disconnect();
          setStatus("error");
        }
      })();
    },
    [greetingNudge, disconnect],
  );

  const handleServerFrame = useCallback(
    async (raw: unknown): Promise<void> => {
      const text = await normalizeFrameToText(raw);
      if (text === null) {
        return;
      }
      let frame: GeminiServerFrame;
      try {
        frame = JSON.parse(text) as GeminiServerFrame;
      } catch {
        // Reason: a non-JSON keepalive or partial frame — skip, never crash.
        return;
      }

      if (frame.error) {
        logger.error("voice_live_server_error", {
          error_message: JSON.stringify(frame.error).slice(0, 200),
          fix_suggestion: "Inspect the Gemini Live error frame; often a stale token or bad setup.",
        });
        setStatus("error");
        return;
      }
      if (frame.goAway) {
        logger.warn("voice_live_go_away", {
          fix_suggestion: "Server asked the session to end; reconnect with a fresh token.",
        });
        disconnect();
        return;
      }

      const serverContent = frame.serverContent;
      if (serverContent?.inputTranscription?.text) {
        onTranscriptRef.current?.({ role: "user", text: serverContent.inputTranscription.text });
      }
      if (serverContent?.outputTranscription?.text) {
        onTranscriptRef.current?.({ role: "model", text: serverContent.outputTranscription.text });
      }
      for (const part of serverContent?.modelTurn?.parts ?? []) {
        const audioData = part.inlineData?.data;
        if (audioData) {
          playerRef.current?.enqueueBase64Chunk(audioData);
        }
      }

      // Function round-trip (gotcha 7): fulfil each call and reply with the EXACT
      // {toolResponse:{functionResponses:[{id,name,response}]}} shape.
      for (const call of frame.toolCall?.functionCalls ?? []) {
        const handler = onToolCallRef.current;
        if (!handler) {
          continue;
        }
        const response = await handler({
          id: call.id ?? "",
          name: call.name ?? "",
          args: call.args ?? {},
        });
        const liveSocket = socketRef.current;
        if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN) {
          // Reason (Rule 12): send() on a non-OPEN socket drops the frame
          // silently — the model would wait forever for the tool response.
          logger.warn("voice_live_send_dropped_socket_not_open", {
            frame_kind: "tool_response",
            ready_state: liveSocket?.readyState ?? -1,
            fix_suggestion: "Socket closed mid tool round-trip; the turn is lost — reconnect.",
          });
          continue;
        }
        liveSocket.send(
          JSON.stringify({
            toolResponse: {
              functionResponses: [{ id: call.id, name: call.name, response }],
            },
          }),
        );
      }
    },
    [disconnect],
  );

  const connect = useCallback(async (): Promise<void> => {
    // Reason (gotcha 7): the single-use token means a StrictMode double-invoke
    // must open only ONE socket — short-circuit the second call.
    if (connectGuardRef.current) {
      // Reason (gotcha 7): a second connect WITHIN this mount must not open a
      // second socket for the uses:1 token.
      logger.info("voice_live_connect_skipped_double_invoke", {});
      return;
    }
    connectGuardRef.current = true;
    // Reason (gotcha 7): snapshot the epoch so we can detect a teardown that
    // happens while the async token mint is in flight (the StrictMode case).
    const connectEpoch = connectEpochRef.current;
    setStatus("connecting");

    // Reason (gotcha 8): acquire ALL audio resources SYNCHRONOUSLY here, inside
    // the user tap — an AudioContext constructed after an await starts
    // 'suspended' on iOS WebKit and the mic processor never fires (the one-way
    // -audio bug). getUserMedia is also started now so the iOS permission flow
    // binds to the gesture; its promise is consumed after setupComplete.
    try {
      const micAudioContext = new AudioContext();
      if (micAudioContext.state !== "running") {
        void micAudioContext.resume();
      }
      micAudioContextRef.current = micAudioContext;
      playerRef.current = createPcmPlayer();
      const pendingMicStream = navigator.mediaDevices.getUserMedia({ audio: true });
      pendingMicStreamRef.current = pendingMicStream;
      // Reason: defuse the unhandled rejection; startMicAndGreeting consumes
      // (and surfaces) the real failure after setupComplete.
      pendingMicStream.catch(() => {});
      logger.info("voice_live_audio_acquired_in_gesture", {
        mic_context_state: micAudioContext.state,
        mic_context_sample_rate: micAudioContext.sampleRate,
      });
    } catch (gestureAudioError) {
      logger.error("voice_live_gesture_audio_failed", {
        error_message: gestureAudioError instanceof Error ? gestureAudioError.message : "unknown",
        fix_suggestion: "AudioContext/getUserMedia unavailable in this WebView; voice mode cannot run.",
      });
      releasePreacquiredAudio();
      connectGuardRef.current = false;
      setStatus("error");
      return;
    }

    let ephemeralTokenName: string;
    try {
      // Reason (gotcha 1): the worker mints the token; the API key never reaches
      // the client. We only ever hold the opaque `auth_tokens/...` name.
      // Reason (4b-SP3): prepend the deployed worker origin (same env Q&A uses) —
      // the static Capacitor/export build has no same-origin server, so a bare
      // relative path would 404. Empty env → same-origin (dev proxy) as before.
      const tokenBaseUrl = (process.env.NEXT_PUBLIC_QA_API_BASE_URL ?? "").replace(/\/+$/, "");
      const tokenResponse = await fetch(`${tokenBaseUrl}${MINT_TOKEN_PATH}`, { method: "POST" });
      if (!tokenResponse.ok) {
        throw new Error(`token mint returned HTTP ${tokenResponse.status}`);
      }
      const tokenBody = (await tokenResponse.json()) as { ephemeral_token_name?: string };
      if (!tokenBody.ephemeral_token_name) {
        throw new Error("token mint response missing ephemeral_token_name");
      }
      ephemeralTokenName = tokenBody.ephemeral_token_name;
    } catch (mintError) {
      logger.error("voice_live_token_mint_failed", {
        error_message: mintError instanceof Error ? mintError.message : "unknown",
        fix_suggestion: "Confirm the worker /api/voice/live-token route + GEMINI_API_KEY are configured.",
      });
      // Reason (gotcha 8): release the gesture-pre-acquired audio — but ONLY if
      // no teardown bumped the epoch meanwhile (a teardown's disconnect already
      // released, and the refs may hold a newer connect's resources).
      if (connectEpoch === connectEpochRef.current) {
        releasePreacquiredAudio();
      }
      connectGuardRef.current = false;
      setStatus("error");
      return;
    }

    // Reason (gotcha 7): if a teardown bumped the epoch while the token mint was
    // awaiting, THIS connect belongs to a torn-down attempt (the StrictMode first
    // mount) — bail BEFORE constructing a socket. The surviving connect (matching
    // epoch) opens the single allowed WSS. This collapses the double-mount to one.
    // NO release here (gotcha 8 invariant): the disconnect that bumped the epoch
    // already released THIS connect's audio, and the shared refs may now hold the
    // SURVIVING connect's resources — releasing would kill the live session.
    if (connectEpoch !== connectEpochRef.current) {
      logger.info("voice_live_connect_aborted_stale_epoch", {});
      return;
    }

    // Reason (gotcha 2): pass the token via ?access_token= on the CONSTRAINED
    // endpoint; the unconstrained one silently drops auth_tokens/... tokens.
    // (The 24 kHz player was already constructed in-gesture — gotcha 8.)
    const socket = new WebSocket(`${GEMINI_LIVE_WSS_BASE}?access_token=${encodeURIComponent(ephemeralTokenName)}`);
    socketRef.current = socket;

    socket.onopen = (): void => {
      // Reason (gotcha 3): send the setup frame and WAIT for setupComplete before
      // any audio. We do NOT start the mic here — only after setupComplete.
      socket.send(JSON.stringify(buildSetupFrame({ model, voiceName, systemInstruction, tools })));
      logger.info("voice_live_setup_sent", { model });
    };

    socket.onmessage = (event: MessageEvent): void => {
      const frameData = event.data;
      void (async (): Promise<void> => {
        const text = await normalizeFrameToText(frameData);
        // Reason (gotcha 3): gate the mic + greeting on setupComplete. Detect it
        // here (before full routing) so audio NEVER precedes the handshake.
        // (text is string | null — `null?.includes` is undefined → falsy.)
        if (text?.includes("setupComplete")) {
          let isComplete = false;
          try {
            isComplete = Boolean((JSON.parse(text) as GeminiServerFrame).setupComplete);
          } catch {
            isComplete = false;
          }
          if (isComplete && !isSetupCompleteRef.current) {
            isSetupCompleteRef.current = true;
            setIsSetupComplete(true);
            setStatus("live");
            startMicAndGreeting(socket);
            return;
          }
        }
        await handleServerFrame(frameData);
      })();
    };

    socket.onerror = (): void => {
      logger.error("voice_live_socket_error", {
        fix_suggestion: "WSS error — check the constrained endpoint URL + token freshness.",
      });
      setStatus("error");
    };
    socket.onclose = (closeEvent: CloseEvent): void => {
      // Reason: allow a future reconnect after the socket closes.
      connectGuardRef.current = false;
      // Reason (Rule 12): a close BEFORE setupComplete on the still-active socket
      // means the server rejected the `setup` frame (e.g. 1007 on a malformed
      // field) — surface an error state instead of leaving the orb on LISTENING
      // forever. Intentional teardowns skip this: disconnect() nulls socketRef
      // BEFORE calling close(), so `socketRef.current === socket` is false there.
      if (socketRef.current === socket && !isSetupCompleteRef.current) {
        logger.error("voice_live_setup_rejected", {
          close_code: closeEvent?.code,
          close_reason: closeEvent?.reason,
          fix_suggestion:
            "Server closed the WS before setupComplete — the setup frame was rejected. " +
            "Check the close reason; e.g. speechConfig must sit inside generationConfig.",
        });
        setStatus("error");
        return;
      }
      setStatus("closed");
    };
    // Reason: hint the parameterized values are intentionally captured here.
  }, [handleServerFrame, startMicAndGreeting, releasePreacquiredAudio, model, systemInstruction, tools, voiceName]);

  // Tear everything down on unmount (also covers the StrictMode unmount). The
  // disconnect() bumps the connect epoch, which is what an in-flight connect()
  // checks after its async token mint so a torn-down (StrictMode-first) mount
  // never opens a socket.
  useEffect(() => {
    return () => {
      disconnect();
      isSetupCompleteRef.current = false;
    };
  }, [disconnect]);

  return { status, isSetupComplete, inputAmplitude, connect, disconnect };
}
