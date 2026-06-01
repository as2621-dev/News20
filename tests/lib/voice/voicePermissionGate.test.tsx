import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoicePermissionGate } from "@/components/voice/VoicePermissionGate";
import {
  getMicPermissionState,
  isMicCaptureSupported,
  type MicPermissionResult,
  requestMicPermission,
} from "@/lib/voice/micPermission";

/**
 * Tests for phase-3b SP1 — the mic-permission helper + the VoicePermissionGate.
 *
 * Rule 9 — these encode WHY the behaviour matters, not just WHAT happens:
 *   - The gate's whole job is the TRUST/SAFETY boundary: it must NEVER reveal the
 *     Voice-mode children (the socket-opening surface) before a real grant, and it
 *     must fire `onGranted` (the SP2 socket-open seam) ONLY on grant. A test asserts
 *     the children + onGranted are absent before grant and present after — a
 *     regression that opened the conversation early would FAIL here.
 *   - A denial is a PRODUCT promise ("read & ask by text instead"), not a dead end:
 *     a test asserts the denied state renders the text-fallback CTA and that tapping
 *     it invokes the deep-link callback, with the granted children / onGranted never
 *     reached. If the fallback broke, a denied user would be stranded.
 *   - SSR / unsupported runtimes must DEGRADE, not crash: the helper must resolve a
 *     typed state (never throw) when `navigator.mediaDevices` is missing, and the
 *     gate must show the same text fallback. A throw here would white-screen Voice
 *     mode on an old WebView.
 *
 * All mic/permission APIs are MOCKED at the boundary (CLAUDE.md mocking rule): no
 * real microphone, socket, or DOM permission is touched. Rendering uses React 19's
 * `react-dom/client` + `react`'s `act` directly (matches `voiceOrb.test.tsx`).
 */

// ---- navigator shims (saved + restored per test) ---------------------------

const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");

/** Replace `globalThis.navigator` with a stub for one test. */
function stubNavigator(value: unknown): void {
  Object.defineProperty(globalThis, "navigator", {
    value,
    configurable: true,
    writable: true,
  });
}

/** Restore the jsdom navigator after a test that stubbed it. */
function restoreNavigator(): void {
  if (originalNavigatorDescriptor) {
    Object.defineProperty(globalThis, "navigator", originalNavigatorDescriptor);
  }
}

/** A fake granted MediaStream whose tracks record that they were stopped. */
function fakeGrantedStream(stoppedTracks: number[]): MediaStream {
  const track = {
    stop: () => {
      stoppedTracks.push(1);
    },
  };
  return { getTracks: () => [track] } as unknown as MediaStream;
}

// ---- micPermission helper (unit, no React) ---------------------------------

describe("micPermission — getUserMedia is wrapped to a typed state (Rule 9)", () => {
  afterEach(() => {
    restoreNavigator();
    vi.restoreAllMocks();
  });

  it("returns granted and releases the mic on a successful getUserMedia (happy path)", async () => {
    // WHY: a grant must resolve "granted" AND stop the tracks — the helper only
    // confirms the grant; holding the mic open would double-acquire it vs SP3.
    stubNavigator({ mediaDevices: { getUserMedia: vi.fn() } });
    const stoppedTracks: number[] = [];
    const getUserMediaImpl = vi.fn().mockResolvedValue(fakeGrantedStream(stoppedTracks));

    const result = await requestMicPermission(getUserMediaImpl);

    expect(result.mic_permission_state).toBe("granted");
    expect(getUserMediaImpl).toHaveBeenCalledWith({ audio: true });
    // The obtained stream's tracks were stopped (mic released, not held open).
    expect(stoppedTracks).toHaveLength(1);
  });

  it("returns denied (never throws) when getUserMedia rejects (failure path)", async () => {
    // WHY: a NotAllowedError must degrade to a typed "denied" so the gate has a
    // state to render — an uncaught throw would crash Voice mode.
    stubNavigator({ mediaDevices: { getUserMedia: vi.fn() } });
    const getUserMediaImpl = vi.fn().mockRejectedValue(new DOMException("blocked", "NotAllowedError"));

    const result = await requestMicPermission(getUserMediaImpl);

    expect(result.mic_permission_state).toBe("denied");
  });

  it("treats a missing mediaDevices API as unsupported, not a throw (edge/SSR)", async () => {
    // WHY: under SSR / old WebView there is no capture API; the helper must resolve
    // "unsupported" rather than throw on `navigator.mediaDevices.getUserMedia`.
    stubNavigator({}); // navigator with no mediaDevices
    expect(isMicCaptureSupported()).toBe(false);

    const result = await requestMicPermission();
    expect(result.mic_permission_state).toBe("unsupported");
  });

  it("reads granted from the Permissions API without prompting", async () => {
    // WHY: an already-granted user must skip the CTA — getMicPermissionState reads
    // state without ever calling getUserMedia (no prompt).
    const getUserMedia = vi.fn();
    stubNavigator({
      mediaDevices: { getUserMedia },
      permissions: { query: vi.fn().mockResolvedValue({ state: "granted" }) },
    });

    const state = await getMicPermissionState();
    expect(state).toBe("granted");
    expect(getUserMedia).not.toHaveBeenCalled(); // never prompted
  });

  it("falls back to prompt when the Permissions API is absent but capture is supported", async () => {
    // WHY: some Safari/WebView builds lack navigator.permissions; the gate must
    // still offer its CTA (the only way to learn the state there).
    stubNavigator({ mediaDevices: { getUserMedia: vi.fn() } }); // no .permissions
    const state = await getMicPermissionState();
    expect(state).toBe("prompt");
  });
});

// ---- VoicePermissionGate (component) ---------------------------------------

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  restoreNavigator();
  vi.restoreAllMocks();
});

/** Render + flush effects, then flush the mount-read microtask + a re-render. */
async function renderGate(node: React.ReactElement): Promise<void> {
  await act(async () => {
    root.render(node);
  });
  // Let getMicPermissionState()'s promise resolve + the state update flush.
  await act(async () => {
    await Promise.resolve();
  });
}

const GRANTED_CHILD_TESTID = "voice-conversation-stub";

/** A stand-in for the Voice-mode children — its presence == the socket surface. */
function ConversationStub(): React.ReactElement {
  return <div data-testid={GRANTED_CHILD_TESTID}>live conversation</div>;
}

describe("VoicePermissionGate — never reveals the conversation before grant (DoD, Rule 9)", () => {
  it("shows the enable-mic CTA (not the children, not onGranted) when state is prompt", async () => {
    // WHY: pre-grant, the socket-opening surface must be hidden and onGranted must
    // NOT fire — this is the "never opens the socket before grant" guarantee.
    stubNavigator({ mediaDevices: { getUserMedia: vi.fn() } }); // no permissions → prompt
    const onGranted = vi.fn();

    await renderGate(
      <VoicePermissionGate story_id="s1" onGranted={onGranted}>
        <ConversationStub />
      </VoicePermissionGate>,
    );

    expect(container.querySelector('[data-voice-permission-cta="enable-mic"]')).not.toBeNull();
    expect(container.querySelector(`[data-testid="${GRANTED_CHILD_TESTID}"]`)).toBeNull();
    expect(onGranted).not.toHaveBeenCalled();
  });

  it("resolves to children + fires onGranted ONCE after a successful request (happy path)", async () => {
    // WHY: granting must reveal the conversation AND fire the single SP2 socket-open
    // seam — exactly once (StrictMode-safe).
    const getUserMedia = vi.fn().mockResolvedValue(fakeGrantedStream([]));
    stubNavigator({ mediaDevices: { getUserMedia } }); // prompt initially
    const onGranted = vi.fn();

    await renderGate(
      <VoicePermissionGate story_id="s1" onGranted={onGranted}>
        <ConversationStub />
      </VoicePermissionGate>,
    );

    // Tap the enable-mic CTA (the user gesture) and flush the async request.
    const cta = container.querySelector<HTMLButtonElement>('[data-voice-permission-cta="enable-mic"]');
    await act(async () => {
      cta?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(container.querySelector(`[data-testid="${GRANTED_CHILD_TESTID}"]`)).not.toBeNull();
    expect(onGranted).toHaveBeenCalledTimes(1);
  });
});

describe("VoicePermissionGate — denial routes to the text fallback (DoD, Rule 9)", () => {
  it("renders the text-fallback CTA and deep-links on tap, never opening the conversation", async () => {
    // WHY: a denied user is promised "read & ask by text" — the fallback CTA must
    // render and invoke the deep-link, while the conversation children / onGranted
    // are NEVER reached (the socket never opens on denial).
    const getUserMedia = vi.fn().mockRejectedValue(new DOMException("blocked", "NotAllowedError"));
    stubNavigator({ mediaDevices: { getUserMedia } });
    const onGranted = vi.fn();
    const onOpenTextFallback = vi.fn();

    await renderGate(
      <VoicePermissionGate story_id="s1" onGranted={onGranted} onOpenTextFallback={onOpenTextFallback}>
        <ConversationStub />
      </VoicePermissionGate>,
    );

    // Tap enable-mic → getUserMedia rejects → gate flips to the denied fallback.
    const enableCta = container.querySelector<HTMLButtonElement>('[data-voice-permission-cta="enable-mic"]');
    await act(async () => {
      enableCta?.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    const fallbackCta = container.querySelector<HTMLButtonElement>('[data-voice-permission-cta="text-fallback"]');
    expect(fallbackCta).not.toBeNull();
    // The conversation surface never mounted; the socket seam never fired.
    expect(container.querySelector(`[data-testid="${GRANTED_CHILD_TESTID}"]`)).toBeNull();
    expect(onGranted).not.toHaveBeenCalled();

    // Tapping the fallback deep-links to Detail Q&A via the controlled callback.
    await act(async () => {
      fallbackCta?.click();
    });
    expect(onOpenTextFallback).toHaveBeenCalledTimes(1);
  });

  it("shows the same text fallback (no throw) when the mic API is unsupported (edge/SSR)", async () => {
    // WHY: an unsupported runtime must degrade to the text path, not crash — the
    // gate renders the denied/unsupported fallback and never the conversation.
    stubNavigator({}); // navigator with no mediaDevices → unsupported
    const onGranted = vi.fn();
    const onOpenTextFallback = vi.fn();

    await renderGate(
      <VoicePermissionGate story_id="s1" onGranted={onGranted} onOpenTextFallback={onOpenTextFallback}>
        <ConversationStub />
      </VoicePermissionGate>,
    );

    const fallbackCta = container.querySelector<HTMLButtonElement>('[data-voice-permission-cta="text-fallback"]');
    expect(fallbackCta).not.toBeNull();
    expect(container.querySelector(`[data-testid="${GRANTED_CHILD_TESTID}"]`)).toBeNull();
    expect(onGranted).not.toHaveBeenCalled();
  });
});

describe("VoicePermissionGate — already-granted skips the CTA (no needless prompt)", () => {
  it("renders the children straight away when the Permissions API already reports granted", async () => {
    // WHY: a returning, already-granted user must NOT be re-prompted — the gate
    // reads granted on mount (without getUserMedia) and resolves immediately.
    const getUserMedia = vi.fn();
    stubNavigator({
      mediaDevices: { getUserMedia },
      permissions: { query: vi.fn().mockResolvedValue({ state: "granted" }) },
    });
    const onGranted = vi.fn();

    await renderGate(
      <VoicePermissionGate story_id="s1" onGranted={onGranted}>
        <ConversationStub />
      </VoicePermissionGate>,
    );

    expect(container.querySelector(`[data-testid="${GRANTED_CHILD_TESTID}"]`)).not.toBeNull();
    expect(onGranted).toHaveBeenCalledTimes(1);
    expect(getUserMedia).not.toHaveBeenCalled(); // resolved without ever prompting
  });
});

// Keep an explicit reference so the typed result import is exercised (the
// helper's public contract surface): a granted result has the union literal.
const _typecheckResult: MicPermissionResult = { mic_permission_state: "granted" };
void _typecheckResult;
