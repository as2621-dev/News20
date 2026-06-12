import { act, StrictMode, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildSetupFrame,
  GEMINI_LIVE_WSS_BASE,
  type GeminiLiveController,
  type GeminiToolCall,
  normalizeFrameToText,
  useGeminiLive,
} from "@/lib/voice/useGeminiLive";

/**
 * Tests for the Gemini Live transport hook (Phase 3 SP3).
 *
 * Rule 9 — these encode the FOUR contract invariants the SP3 Definition of Done
 * names, against a fake WebSocket + mocked token mint (no network, no real
 * audio device):
 *   1. the client sends a well-formed `setup` frame and does NOT send audio /
 *      the greeting before `{setupComplete}` arrives (gotcha 3);
 *   2. it replies to a `toolCall` with the EXACT
 *      `{toolResponse:{functionResponses:[{id,name,response}]}}` shape (gotcha 7);
 *   3. it targets the CONSTRAINED endpoint with the token as ?access_token= (g2);
 *   4. mounting twice (React 19 StrictMode) opens EXACTLY ONE socket (gotcha 7) —
 *      the single-use token would 401 on a second connect.
 */

// ---------------------------------------------------------------------------
// Fake WebSocket the tests drive deterministically.
// ---------------------------------------------------------------------------

interface SentFrame {
  raw: string;
  parsed: Record<string, unknown>;
}

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState = FakeWebSocket.CONNECTING;
  sentFrames: SentFrame[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(raw: string): void {
    this.sentFrames.push({ raw, parsed: JSON.parse(raw) });
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  // Test driver: simulate the server side.
  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  deliver(frameObject: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frameObject) });
  }

  /** Frames the client sent, by their top-level key (setup / clientContent / ...). */
  framesWithKey(topLevelKey: string): SentFrame[] {
    return this.sentFrames.filter((frame) => topLevelKey in frame.parsed);
  }
}

// ---------------------------------------------------------------------------
// Fake AudioContext — complete enough for BOTH the mic-capture path and the
// 24 kHz player path (gotcha 8: the hook now constructs both in-gesture).
// ---------------------------------------------------------------------------

class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  state = "suspended";
  sampleRate = 48000;
  currentTime = 0;
  destination = {};
  resume = vi.fn(async (): Promise<void> => {
    this.state = "running";
  });
  constructor() {
    FakeAudioContext.instances.push(this);
  }
  createBuffer() {
    return { getChannelData: () => new Float32Array(0), duration: 0 };
  }
  createBufferSource() {
    return { connect() {}, start() {}, stop() {}, onended: null };
  }
  createMediaStreamSource() {
    return { connect() {}, disconnect() {} };
  }
  lastScriptProcessor: {
    connect: () => void;
    disconnect: () => void;
    onaudioprocess: ((event: unknown) => void) | null;
  } | null = null;
  createScriptProcessor() {
    this.lastScriptProcessor = { connect() {}, disconnect() {}, onaudioprocess: null };
    return this.lastScriptProcessor;
  }
  createGain() {
    return { gain: { value: 0 }, connect() {}, disconnect() {} };
  }
  close() {
    this.state = "closed";
    return Promise.resolve();
  }
}

function fakeMediaStream(): { getTracks: () => { stop: () => void }[] } {
  return { getTracks: () => [{ stop: vi.fn() }] };
}

let getUserMediaMock: ReturnType<typeof vi.fn>;

// A controller-capturing harness component (codebase convention: createRoot+act).
let capturedController: GeminiLiveController | null = null;

function HookHarness(props: {
  systemInstruction: string;
  onToolCall?: (call: GeminiToolCall) => Promise<Record<string, unknown>> | Record<string, unknown>;
  onMicError?: (errorMessage: string) => void;
  autoConnect?: boolean;
}): null {
  const controller = useGeminiLive({
    systemInstruction: props.systemInstruction,
    tools: [{ name: "record_interest", description: "record a detected interest" }],
    onToolCall: props.onToolCall,
    onMicError: props.onMicError,
  });
  capturedController = controller;
  useEffect(() => {
    if (props.autoConnect) {
      void controller.connect();
    }
    // Reason: connect once per mount; StrictMode double-mounts to prove the guard.
  }, [props.autoConnect, controller]);
  return null;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // Reason: tells React this is an act() environment so effect cleanups (and the
  // StrictMode double-invoke) flush synchronously inside act(...).
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  FakeWebSocket.instances = [];
  capturedController = null;
  vi.stubGlobal("WebSocket", FakeWebSocket);
  // Token mint → return a valid auth_tokens/ name.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ephemeral_token_name: "auth_tokens/test-token" }),
    })),
  );
  // Stub BOTH AudioContexts (mic capture + createPcmPlayer) — no real audio
  // device. The stub is COMPLETE for the mic path (createMediaStreamSource /
  // createScriptProcessor / createGain / state / resume): a partial stub would
  // make mic startup throw, which the gotcha-8 design now surfaces as status
  // "error" instead of swallowing.
  FakeAudioContext.instances = [];
  vi.stubGlobal("AudioContext", FakeAudioContext);
  // Stub the mic so connect()'s in-gesture getUserMedia resolves to a fake stream.
  getUserMediaMock = vi.fn(async () => fakeMediaStream());
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: getUserMediaMock },
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("buildSetupFrame — the setup-frame contract (gotcha 3, pure)", () => {
  it("emits AUDIO modality, the voice, both transcriptions, and a models/-prefixed model", () => {
    // WHY: a malformed setup frame makes the WS drop without setupComplete. Each
    // of these fields is required by the contract, so assert the full shape.
    const frame = buildSetupFrame({
      model: "gemini-x",
      voiceName: "Charon",
      systemInstruction: "ground in the story",
      tools: [{ name: "t", description: "d" }],
    });
    expect(frame.setup.model).toBe("models/gemini-x");
    expect(frame.setup.generationConfig.responseModalities).toEqual(["AUDIO"]);
    // speechConfig MUST live INSIDE generationConfig — top-level → WS close 1007.
    expect(frame.setup.generationConfig.speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName).toBe("Charon");
    expect(frame.setup.systemInstruction.parts[0].text).toBe("ground in the story");
    expect(frame.setup.tools[0].functionDeclarations[0].name).toBe("t");
    expect(frame.setup.inputAudioTranscription).toEqual({});
    expect(frame.setup.outputAudioTranscription).toEqual({});
  });

  it("exports Jordan's live voice as Sadaltager (matches the pre-rendered TTS host)", async () => {
    // WHY: the live Q&A host must sound like the JORDAN the user just heard in
    // the story digest — agents/voice/gemini_tts.py binds JORDAN → Sadaltager,
    // and this constant is the frontend half of that cross-stack contract.
    const { GEMINI_LIVE_JORDAN_VOICE } = await import("@/lib/voice/useGeminiLive");
    expect(GEMINI_LIVE_JORDAN_VOICE).toBe("Sadaltager");
  });
});

describe("normalizeFrameToText — frame normalization (gotcha 6)", () => {
  it("returns a string frame unchanged", async () => {
    expect(await normalizeFrameToText('{"a":1}')).toBe('{"a":1}');
  });
  it("decodes an ArrayBuffer frame to JSON text", async () => {
    const buffer = new TextEncoder().encode('{"a":1}').buffer;
    expect(await normalizeFrameToText(buffer)).toBe('{"a":1}');
  });
  it("decodes a Blob-like frame via its async text() method", async () => {
    const blobLike = { text: async () => '{"b":2}' };
    expect(await normalizeFrameToText(blobLike)).toBe('{"b":2}');
  });
});

describe("useGeminiLive — the live transport contract", () => {
  it("opens the CONSTRAINED endpoint and sends setup BEFORE any audio/greeting (gotcha 2/3)", async () => {
    await act(async () => {
      root.render(<HookHarness systemInstruction="story-prompt" autoConnect />);
    });
    // The token mint + WS construction are async (await fetch); flush microtasks.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const socket = FakeWebSocket.instances[0];
    expect(socket).toBeDefined();
    // gotcha 2: constrained endpoint + token as ?access_token=.
    expect(socket.url.startsWith(GEMINI_LIVE_WSS_BASE)).toBe(true);
    expect(socket.url).toContain("access_token=auth_tokens%2Ftest-token");

    // Before onopen, nothing is sent.
    expect(socket.sentFrames.length).toBe(0);

    // onopen → exactly the setup frame, and NO audio / greeting yet (gotcha 3).
    await act(async () => {
      socket.open();
    });
    expect(socket.framesWithKey("setup").length).toBe(1);
    expect(socket.framesWithKey("realtimeInput").length).toBe(0);
    expect(socket.framesWithKey("clientContent").length).toBe(0);

    // Only AFTER setupComplete does the greeting nudge (clientContent) go out.
    await act(async () => {
      socket.deliver({ setupComplete: {} });
      await Promise.resolve();
    });
    expect(socket.framesWithKey("clientContent").length).toBe(1);
    const greeting = socket.framesWithKey("clientContent")[0].parsed.clientContent as {
      turnComplete: boolean;
    };
    expect(greeting.turnComplete).toBe(true);
  });

  it("replies to a toolCall with the exact {toolResponse:{functionResponses:[{id,name,response}]}} shape (gotcha 7)", async () => {
    const onToolCall = vi.fn(async (_call: GeminiToolCall) => ({ result: "ok" }));
    await act(async () => {
      root.render(<HookHarness systemInstruction="p" autoConnect onToolCall={onToolCall} />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const socket = FakeWebSocket.instances[0];
    await act(async () => {
      socket.open();
      socket.deliver({ setupComplete: {} });
      await Promise.resolve();
    });

    await act(async () => {
      socket.deliver({
        toolCall: { functionCalls: [{ id: "call-1", name: "record_interest", args: { x: 1 } }] },
      });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onToolCall).toHaveBeenCalledWith({ id: "call-1", name: "record_interest", args: { x: 1 } });
    const toolResponses = socket.framesWithKey("toolResponse");
    expect(toolResponses.length).toBe(1);
    const payload = toolResponses[0].parsed.toolResponse as {
      functionResponses: { id: string; name: string; response: unknown }[];
    };
    expect(payload.functionResponses).toEqual([{ id: "call-1", name: "record_interest", response: { result: "ok" } }]);
  });

  it("opens EXACTLY ONE socket under React 19 StrictMode double-mount (gotcha 7)", async () => {
    // WHY: the uses:1 token would 401 on a second connect; the guard must collapse
    // StrictMode's intentional double-invoke down to a single WebSocket.
    await act(async () => {
      root.render(
        <StrictMode>
          <HookHarness systemInstruction="p" autoConnect />
        </StrictMode>,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(FakeWebSocket.instances.length).toBe(1);
  });

  it("does not connect (no socket) until connect() is called", async () => {
    // WHY: connect() must run inside a user gesture (mic + audio unlock), never on
    // mount — proving the hook is inert until explicitly told to connect.
    await act(async () => {
      root.render(<HookHarness systemInstruction="p" />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(FakeWebSocket.instances.length).toBe(0);
    expect(capturedController?.status).toBe("idle");
  });
});

describe("useGeminiLive — gesture-synchronous audio (gotcha 8)", () => {
  it("constructs the AudioContexts and starts getUserMedia BEFORE the token mint resolves", async () => {
    // WHY: an AudioContext created after an await starts 'suspended' on iOS
    // WebKit → the mic processor never fires → one-way audio (the exact bug).
    // Gate the mint on a manual promise to prove acquisition happens first.
    let releaseMint: (() => void) | undefined;
    const mintGate = new Promise<void>((resolve) => {
      releaseMint = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        await mintGate;
        return {
          ok: true,
          status: 200,
          json: async () => ({ ephemeral_token_name: "auth_tokens/test-token" }),
        };
      }),
    );

    await act(async () => {
      root.render(<HookHarness systemInstruction="p" autoConnect />);
    });

    // The mint has NOT resolved yet — both contexts + getUserMedia already exist.
    expect(FakeAudioContext.instances.length).toBe(2);
    expect(getUserMediaMock).toHaveBeenCalledTimes(1);
    // resume() was kicked on the (suspended) mic context inside the gesture.
    expect(FakeAudioContext.instances[0].resume).toHaveBeenCalled();
    expect(FakeWebSocket.instances.length).toBe(0);

    releaseMint?.();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(FakeWebSocket.instances.length).toBe(1);
  });

  it("surfaces a mic failure as status error + onMicError (never a deaf LISTENING)", async () => {
    // WHY: the old code logged the failure and let the orb stay LISTENING while
    // the socket heard nothing — the user spoke to a deaf session.
    getUserMediaMock.mockRejectedValue(new Error("NotAllowedError"));
    const onMicError = vi.fn();
    await act(async () => {
      root.render(<HookHarness systemInstruction="p" onMicError={onMicError} />);
    });
    // Reason: call connect() ONCE manually — the autoConnect harness effect
    // would re-connect after the error path resets the double-connect guard.
    await act(async () => {
      void capturedController?.connect();
      await Promise.resolve();
      await Promise.resolve();
    });
    const socket = FakeWebSocket.instances[0];
    await act(async () => {
      socket.open();
      socket.deliver({ setupComplete: {} });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onMicError).toHaveBeenCalledWith("NotAllowedError");
    expect(capturedController?.status).toBe("error");
  });

  it("drops mic frames (no throw, no send) when the socket is not OPEN", async () => {
    // WHY: WebSocket.send() on a closing socket drops frames SILENTLY — the
    // guard must skip the send and warn instead of letting audio vanish quietly.
    await act(async () => {
      root.render(<HookHarness systemInstruction="p" autoConnect />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const socket = FakeWebSocket.instances[0];
    await act(async () => {
      socket.open();
      socket.deliver({ setupComplete: {} });
      await Promise.resolve();
      await Promise.resolve();
    });

    // The mic context (first instance) now owns a ScriptProcessor.
    const micContext = FakeAudioContext.instances[0];
    expect(micContext.lastScriptProcessor).not.toBeNull();
    const audioEvent = { inputBuffer: { getChannelData: () => new Float32Array(8) } };

    // While OPEN, a frame goes out.
    act(() => {
      micContext.lastScriptProcessor?.onaudioprocess?.(audioEvent);
    });
    expect(socket.framesWithKey("realtimeInput").length).toBe(1);

    // Not OPEN → the chunk is dropped, not thrown, not sent.
    socket.readyState = FakeWebSocket.CLOSING;
    act(() => {
      micContext.lastScriptProcessor?.onaudioprocess?.(audioEvent);
    });
    expect(socket.framesWithKey("realtimeInput").length).toBe(1);
  });

  it("StrictMode: the torn-down first mount's pre-acquired contexts are closed", async () => {
    // WHY: connect() now holds live audio resources BEFORE any await — the
    // StrictMode first mount must release them (mic indicator off, no leak)
    // while the surviving mount keeps its own.
    await act(async () => {
      root.render(
        <StrictMode>
          <HookHarness systemInstruction="p" autoConnect />
        </StrictMode>,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(FakeWebSocket.instances.length).toBe(1);
    // Mount 1 acquired contexts [0,1] (mic+player) — released by its cleanup;
    // mount 2's contexts [2,3] survive for the live session.
    expect(FakeAudioContext.instances.length).toBe(4);
    expect(FakeAudioContext.instances[0].state).toBe("closed");
    expect(FakeAudioContext.instances[1].state).toBe("closed");
    expect(FakeAudioContext.instances[2].state).not.toBe("closed");
    expect(FakeAudioContext.instances[3].state).not.toBe("closed");
  });
});
