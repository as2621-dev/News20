import { act, useSyncExternalStore } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Honest-state-machine tests for AskSheetVoice (issue #37 — PRD stories #7/#8).
 *
 * Rendering uses React 19's `createRoot` + `act` directly (NO @testing-library),
 * mirroring `askSheetVoiceCorpus.test.tsx`. The Gemini Live hook is mocked with
 * a tiny external store so tests can drive the REAL status transitions the
 * component must render honestly: connecting → live → error/closed.
 *
 * Rule 9 — WHY these matter:
 *   - The orb previously showed LISTENING the moment the sheet opened, while the
 *     session was still minting a token / handshaking — the user spoke into a
 *     deaf session. LISTENING may appear ONLY once status === "live"
 *     (setupComplete); before that the state must read CONNECTING.
 *   - Pre-warm: the session must start at sheet-open (mount) when permission is
 *     already granted, and the token mint must fire at sheet-open even on the
 *     permission CTA — not wait for the mic tap.
 *   - A mint/WSS failure must land on a visible error WITH a retry, never an
 *     infinite CONNECTING; an unexpected mid-session close must not fake live.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import type { Story } from "@/types/feed";

vi.mock("@/lib/voice/fetchStoryCorpus", () => ({
  fetchStoryCorpus: vi.fn(async () => ""),
}));

/** External store driving the mocked hook's status, like the real socket would. */
const liveStatusStore = {
  status: "idle" as "idle" | "connecting" | "live" | "closed" | "error",
  listeners: new Set<() => void>(),
};

function setLiveStatus(next_status: typeof liveStatusStore.status): void {
  liveStatusStore.status = next_status;
  for (const notify of liveStatusStore.listeners) {
    notify();
  }
}

const connectMock = vi.fn(async (): Promise<void> => {
  setLiveStatus("connecting");
});
const prewarmTokenMock = vi.fn();
const disconnectMock = vi.fn();

vi.mock("@/lib/voice/useGeminiLive", () => ({
  GEMINI_LIVE_DEFAULT_VOICE: "Charon",
  GEMINI_LIVE_JORDAN_VOICE: "Sadaltager",
  useGeminiLive: () => {
    const status = useSyncExternalStore(
      (notify: () => void) => {
        liveStatusStore.listeners.add(notify);
        return () => liveStatusStore.listeners.delete(notify);
      },
      () => liveStatusStore.status,
    );
    return {
      status,
      isSetupComplete: status === "live",
      inputAmplitude: 0,
      connect: connectMock,
      prewarmToken: prewarmTokenMock,
      disconnect: disconnectMock,
    };
  },
}));

const getMicPermissionStateMock = vi.fn(async () => "granted" as const);
vi.mock("@/lib/voice/micPermission", () => ({
  getMicPermissionState: () => getMicPermissionStateMock(),
  requestMicPermission: vi.fn(async () => ({ mic_permission_state: "granted" })),
}));

const { AskSheetVoice } = await import("@/components/blip/reel/AskSheetVoice");

function makeStory(): Story {
  return {
    digest_id: "story-1",
    headline: "Why does Hormuz matter?",
    segment_key: "world",
    segment_label: "World",
    segment_accent_hex: "#22C55E",
    anchors: ["ALEX", "JORDAN"],
    digest_audio_url: "",
    audio_duration_ms: 1000,
    speech_end_ms: 1000,
    poster_url: "",
    caption_sentences: [],
  } as unknown as Story;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  liveStatusStore.status = "idle";
  liveStatusStore.listeners.clear();
  getMicPermissionStateMock.mockResolvedValue("granted");
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
      removeItem: (key: string) => store.delete(key),
      clear: () => store.clear(),
    },
    configurable: true,
    writable: true,
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

async function renderSheetAlreadyGranted(): Promise<void> {
  localStorage.setItem("blip-voice-granted", "1");
  await act(async () => {
    root.render(<AskSheetVoice story={makeStory()} onClose={vi.fn()} onOpenArticle={vi.fn()} />);
  });
  // Flush the mount permission check + auto-connect microtasks.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function stateLabelText(): string | null {
  return container.querySelector(".vs-state")?.textContent ?? null;
}

describe("AskSheetVoice — honest connecting state (issue #37)", () => {
  it("pre-warms at sheet-open (connect on mount) and shows CONNECTING, not LISTENING", async () => {
    await renderSheetAlreadyGranted();

    // Pre-warm: the session starts at sheet-open, not at a tap.
    expect(connectMock).toHaveBeenCalledTimes(1);
    // Honest state: setup has NOT completed — the orb must not claim LISTENING.
    expect(stateLabelText()).toBe("CONNECTING");
  });

  it("shows LISTENING only after the session reaches live (setupComplete)", async () => {
    await renderSheetAlreadyGranted();
    expect(stateLabelText()).toBe("CONNECTING");

    await act(async () => {
      setLiveStatus("live");
    });
    expect(stateLabelText()).toBe("LISTENING");
  });

  it("fires the token pre-warm at sheet-open even on the permission CTA (mint overlaps the tap)", async () => {
    getMicPermissionStateMock.mockResolvedValue("prompt" as never);
    await act(async () => {
      root.render(<AskSheetVoice story={makeStory()} onClose={vi.fn()} onOpenArticle={vi.fn()} />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    // Still on the permission CTA — no connect, but the mint is already warming.
    expect(container.textContent).toContain("Enable microphone");
    expect(connectMock).not.toHaveBeenCalled();
    expect(prewarmTokenMock).toHaveBeenCalledTimes(1);
  });

  it("lands on a visible error WITH retry when setup fails, and retry reconnects", async () => {
    await renderSheetAlreadyGranted();

    // First error is consumed by the one-shot Jordan→default voice fallback,
    // which auto-reconnects; a second failure must surface the error view.
    await act(async () => {
      setLiveStatus("error");
    });
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      setLiveStatus("error");
    });

    const retryButton = container.querySelector<HTMLButtonElement>("[data-testid='voice-retry']");
    expect(container.textContent).toContain("Voice isn't available right now.");
    expect(retryButton).not.toBeNull();

    const connectCallsBeforeRetry = connectMock.mock.calls.length;
    await act(async () => {
      retryButton?.click();
      await Promise.resolve();
    });
    expect(connectMock.mock.calls.length).toBe(connectCallsBeforeRetry + 1);
    expect(stateLabelText()).toBe("CONNECTING");
  });

  it("treats an unexpected mid-session close as an ended session, never a fake LISTENING", async () => {
    await renderSheetAlreadyGranted();
    await act(async () => {
      setLiveStatus("live");
    });
    expect(stateLabelText()).toBe("LISTENING");

    await act(async () => {
      setLiveStatus("closed");
    });
    expect(stateLabelText()).toBeNull();
    expect(container.querySelector("[data-testid='voice-retry']")).not.toBeNull();
  });
});
