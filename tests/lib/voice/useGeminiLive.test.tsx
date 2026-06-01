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

// A controller-capturing harness component (codebase convention: createRoot+act).
let capturedController: GeminiLiveController | null = null;

function HookHarness(props: {
  systemInstruction: string;
  onToolCall?: (call: GeminiToolCall) => Promise<Record<string, unknown>> | Record<string, unknown>;
  autoConnect?: boolean;
}): null {
  const controller = useGeminiLive({
    systemInstruction: props.systemInstruction,
    tools: [{ name: "record_interest", description: "record a detected interest" }],
    onToolCall: props.onToolCall,
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
  // Stub the playback AudioContext (createPcmPlayer) — no real audio device.
  vi.stubGlobal(
    "AudioContext",
    class {
      currentTime = 0;
      destination = {};
      createBuffer() {
        return { getChannelData: () => new Float32Array(0), duration: 0 };
      }
      createBufferSource() {
        return { connect() {}, start() {}, stop() {}, onended: null };
      }
      close() {
        return Promise.resolve();
      }
    },
  );
  // Stub the mic so startMicAndGreeting's getUserMedia resolves to a fake stream.
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [] })) },
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
    expect(frame.setup.speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName).toBe("Charon");
    expect(frame.setup.systemInstruction.parts[0].text).toBe("ground in the story");
    expect(frame.setup.tools[0].functionDeclarations[0].name).toBe("t");
    expect(frame.setup.inputAudioTranscription).toEqual({});
    expect(frame.setup.outputAudioTranscription).toEqual({});
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
