import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Tests for phase-3b SP2 — the VoiceMode lateral layer + live wiring.
 *
 * Rule 9 — these encode WHY the behaviour matters, not just WHAT happens:
 *   1. **The socket must not open before grant, and must open EXACTLY once on
 *      grant, correctly configured.** VoiceMode mounts the SP1 permission gate;
 *      `useGeminiLive.connect()` is the socket-open. A test asserts: while the gate
 *      is in `prompt`, the hook is NOT connected (no conversation surface mounted);
 *      after a real grant, `useGeminiLive` is configured with the AUDIO/Charon/
 *      story-scoped systemInstruction + a story greeting nudge, and `connect()` is
 *      called exactly once. A regression that opened the WSS pre-grant — or twice,
 *      or with the wrong (unscoped) instruction — FAILS here. This is the trust +
 *      cost boundary, not mere wiring.
 *   2. **The open/close COMMIT logic is correct at the offset/velocity boundaries.**
 *      The pure `shouldCommit{Right,Left}wardDrag` deciders (extracted like the
 *      Detail one) are unit-tested at the threshold edges — a drift in the commit
 *      rule would silently make the layer open on a stray graze or refuse a real
 *      swipe.
 *   3. **Closing tears the socket down; the reel base layer stays mounted.** A test
 *      asserts `disconnect()` fires when the layer closes (`isOpen` false) and on
 *      unmount, and that LayerStack renders the reel children as the persistent base
 *      (never unmounted by a Voice open) so reel `<audio>` position is preserved.
 *
 * `useGeminiLive` is MOCKED at the boundary (CLAUDE.md mocking rule): no real
 * socket, token mint, or mic is touched. The mic gate's permission APIs are mocked
 * via a navigator stub (matching `voicePermissionGate.test.tsx`). Rendering uses
 * React 19's `react-dom/client` + `act` directly.
 */

// ---- useGeminiLive mock (boundary) -----------------------------------------

/** Captured params of the most recent `useGeminiLive` call (for config asserts). */
let lastGeminiLiveParams: Record<string, unknown> | null = null;
const connectMock = vi.fn(async () => {});
const disconnectMock = vi.fn();

vi.mock("@/lib/voice/useGeminiLive", async (importOriginal) => {
  // Reason: keep the real constants/types (GEMINI_LIVE_DEFAULT_VOICE etc.) so the
  // component imports them unchanged; only the hook itself is replaced with a spy.
  const actual = await importOriginal<typeof import("@/lib/voice/useGeminiLive")>();
  return {
    ...actual,
    useGeminiLive: (params: Record<string, unknown>) => {
      lastGeminiLiveParams = params;
      return {
        status: "live" as const,
        isSetupComplete: true,
        inputAmplitude: 0,
        connect: connectMock,
        disconnect: disconnectMock,
      };
    },
  };
});

// Imported AFTER the mock so the components pick up the spied hook.
import { LayerStack, shouldCommitLeftwardDrag, shouldCommitRightwardDrag } from "@/components/shell/LayerStack";
import { useLayerStack } from "@/components/shell/LayerStackContext";
import {
  buildGreetingNudge,
  buildInNewsSystemInstruction,
  orbStateForStatus,
} from "@/components/voice/VoiceConversation";
import { VoiceMode } from "@/components/voice/VoiceMode";
import { ASK_ABOUT_STORY_TOOL_NAME, STORY_QA_TOOL_GROUNDING_CLAUSE } from "@/lib/voice/storyQaTool";
import { GEMINI_LIVE_DEFAULT_VOICE, type GeminiToolDeclaration } from "@/lib/voice/useGeminiLive";
import type { Story } from "@/types/feed";

// ---- fixtures + navigator shims --------------------------------------------

const ACTIVE_STORY: Story = {
  digest_id: "s1",
  headline: "Ceasefire talks stall as both sides trade blame",
  segment_key: "geopolitics",
  segment_label: "Geopolitics",
  segment_accent_hex: "#EF4444",
  anchors: ["ALEX", "JORDAN"],
  digest_audio_url: "/fixtures/s1.mp3",
  audio_duration_ms: 13000,
  speech_end_ms: 12000,
  poster_url: "/fixtures/s1.png",
  caption_sentences: [],
};

const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");

function stubNavigator(value: unknown): void {
  Object.defineProperty(globalThis, "navigator", { value, configurable: true, writable: true });
}

function restoreNavigator(): void {
  if (originalNavigatorDescriptor) {
    Object.defineProperty(globalThis, "navigator", originalNavigatorDescriptor);
  }
}

function fakeGrantedStream(): MediaStream {
  return { getTracks: () => [{ stop: () => {} }] } as unknown as MediaStream;
}

// ---- pure deciders (no React) ----------------------------------------------

describe("drag-commit deciders — offset/velocity boundaries (Rule 9)", () => {
  // WHY: these are the single source of truth for "did this swipe open/close a
  // lateral layer?". A boundary drift would either open on a stray graze or refuse
  // a deliberate swipe — both break the core gesture. The Detail (rightward) and
  // Voice (leftward) directions are exact mirrors.
  it("rightward: commits past the offset threshold OR a fast rightward flick", () => {
    expect(shouldCommitRightwardDrag(80, 0, 64)).toBe(true); // offset past 64
    expect(shouldCommitRightwardDrag(20, 600, 64)).toBe(true); // fast flick (>480)
    expect(shouldCommitRightwardDrag(64, 0, 64)).toBe(false); // exactly at edge = no
    expect(shouldCommitRightwardDrag(20, 480, 64)).toBe(false); // exactly at vel edge = no
    expect(shouldCommitRightwardDrag(-80, -600, 64)).toBe(false); // leftward never commits Detail
  });

  it("leftward: commits past the (negative) offset threshold OR a fast leftward flick", () => {
    expect(shouldCommitLeftwardDrag(-80, 0, 64)).toBe(true); // offset past -64
    expect(shouldCommitLeftwardDrag(-20, -600, 64)).toBe(true); // fast leftward flick
    expect(shouldCommitLeftwardDrag(-64, 0, 64)).toBe(false); // exactly at edge = no
    expect(shouldCommitLeftwardDrag(-20, -480, 64)).toBe(false); // exactly at vel edge = no
    expect(shouldCommitLeftwardDrag(80, 600, 64)).toBe(false); // rightward never commits Voice
  });
});

// ---- system-instruction / greeting / orb mapping (pure) --------------------

describe("in-news Voice config — AUDIO/Charon/story-scoped contract (Rule 9)", () => {
  it("scopes the system instruction to the active story and forbids ungrounded answers", () => {
    // WHY: the instruction is the trust contract — it MUST name this one story and
    // forbid answering from outside its sources, so even the SP2 (tool-less) state
    // can't invent. A regression that dropped the scope/refusal would fail here.
    const instruction = buildInNewsSystemInstruction(ACTIVE_STORY.headline, ACTIVE_STORY.digest_id);
    expect(instruction).toContain(ACTIVE_STORY.headline);
    expect(instruction).toContain(ACTIVE_STORY.digest_id);
    expect(instruction.toLowerCase()).toContain("only");
    expect(instruction.toLowerCase()).toMatch(/can't answer|never guess|never invent/);
  });

  it("appends the SP3 tool-grounding clause when provided (extension point)", () => {
    const clause = "You MUST call ask_about_story before answering.";
    const instruction = buildInNewsSystemInstruction("H", "s9", clause);
    expect(instruction).toContain(clause);
  });

  it("greets about THIS story without summarizing it", () => {
    const nudge = buildGreetingNudge(ACTIVE_STORY.headline);
    expect(nudge).toContain(ACTIVE_STORY.headline);
    expect(nudge.toLowerCase()).toContain("ask");
  });

  it("maps live+paused → still 'paused' orb; live+active → 'listening'; else 'idle'", () => {
    // WHY: the mic-in-orb contract — animating = live, still = paused. A miswire
    // would lie about session state to a hands-free user.
    expect(orbStateForStatus("live", false)).toBe("listening");
    expect(orbStateForStatus("live", true)).toBe("paused");
    expect(orbStateForStatus("connecting", false)).toBe("idle");
    expect(orbStateForStatus("error", false)).toBe("idle");
  });
});

// ---- VoiceMode (component) — gate → connect handoff ------------------------

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  lastGeminiLiveParams = null;
  connectMock.mockClear();
  disconnectMock.mockClear();
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

/** A tiny LayerStack provider host so VoiceMode's `useLayerStack()` resolves. */
function HostedVoiceMode({ isOpen }: { isOpen: boolean }): React.ReactElement {
  return (
    <LayerStack>
      <VoiceModeContextBridge isOpen={isOpen} />
    </LayerStack>
  );
}

/** Renders VoiceMode inside the LayerStack tree (so context is available). */
function VoiceModeContextBridge({ isOpen }: { isOpen: boolean }): React.ReactElement {
  // Touch the context so a missing provider would throw (fail-loud, Rule 12).
  useLayerStack();
  return <VoiceMode story={ACTIVE_STORY} isOpen={isOpen} prefers_reduced_motion={false} />;
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("VoiceMode — socket opens ONLY on grant, configured + once (DoD, Rule 9)", () => {
  it("does NOT connect while the gate is in prompt (no grant yet)", async () => {
    // WHY: pre-grant there must be no conversation surface and no WSS — the SP1
    // structural guarantee carried through SP2. connect() must be untouched.
    stubNavigator({ mediaDevices: { getUserMedia: vi.fn() } }); // no permissions → prompt
    await act(async () => {
      root.render(<HostedVoiceMode isOpen />);
    });
    await flush();

    expect(container.querySelector('[data-voice-permission-cta="enable-mic"]')).not.toBeNull();
    expect(container.querySelector("[data-voice-conversation]")).toBeNull();
    expect(connectMock).not.toHaveBeenCalled();
  });

  it("on grant: configures useGeminiLive (AUDIO/Charon/story-scoped + greeting) and connects ONCE", async () => {
    // WHY: this is the open boundary — granting must reveal the conversation, wire
    // the hook with the story-scoped Charon instruction + greeting, and open the
    // socket exactly once. A regression (wrong voice, unscoped prompt, double
    // connect) fails here.
    const getUserMedia = vi.fn().mockResolvedValue(fakeGrantedStream());
    stubNavigator({ mediaDevices: { getUserMedia } }); // prompt → grant on tap
    await act(async () => {
      root.render(<HostedVoiceMode isOpen />);
    });
    await flush();

    const cta = container.querySelector<HTMLButtonElement>('[data-voice-permission-cta="enable-mic"]');
    await act(async () => {
      cta?.click();
    });
    await flush();

    // Conversation surface mounted + socket opened exactly once.
    expect(container.querySelector("[data-voice-conversation]")).not.toBeNull();
    expect(connectMock).toHaveBeenCalledTimes(1);

    // Hook configured per the contract.
    expect(lastGeminiLiveParams).not.toBeNull();
    expect(lastGeminiLiveParams?.voiceName).toBe(GEMINI_LIVE_DEFAULT_VOICE);
    expect(lastGeminiLiveParams?.voiceName).toBe("Charon");
    expect(String(lastGeminiLiveParams?.systemInstruction)).toContain(ACTIVE_STORY.headline);
    expect(String(lastGeminiLiveParams?.greetingNudge)).toContain(ACTIVE_STORY.headline);
    // SP3 fills the seam: the ask_about_story tool + the tool-forcing instruction
    // clause are now wired through VoiceMode into the hook (was empty in SP2).
    const wiredTools = lastGeminiLiveParams?.tools as GeminiToolDeclaration[] | undefined;
    expect(wiredTools).toHaveLength(1);
    expect(wiredTools?.[0]?.name).toBe(ASK_ABOUT_STORY_TOOL_NAME);
    expect(typeof lastGeminiLiveParams?.onToolCall).toBe("function");
    expect(String(lastGeminiLiveParams?.systemInstruction)).toContain(STORY_QA_TOOL_GROUNDING_CLAUSE);
  });
});

describe("VoiceMode — closing disconnects; reel base stays mounted (DoD, Rule 9)", () => {
  it("disconnects when the layer closes (isOpen → false), reel children still present", async () => {
    // WHY: closing must tear the socket down (cost/privacy) WITHOUT unmounting the
    // reel — the reel is the persistent base so its <audio> position survives.
    const getUserMedia = vi.fn().mockResolvedValue(fakeGrantedStream());
    stubNavigator({ mediaDevices: { getUserMedia } });

    // A marker child stands in for the reel base layer.
    function ReelBaseMarker(): React.ReactElement {
      useLayerStack();
      return <div data-reel-base="">reel</div>;
    }

    // Render LayerStack with BOTH the reel base marker and a granted VoiceMode.
    function Host({ isOpen }: { isOpen: boolean }): React.ReactElement {
      return (
        <LayerStack>
          <ReelBaseMarker />
          <VoiceMode story={ACTIVE_STORY} isOpen={isOpen} prefers_reduced_motion={false} />
        </LayerStack>
      );
    }

    await act(async () => {
      root.render(<Host isOpen />);
    });
    await flush();
    const cta = container.querySelector<HTMLButtonElement>('[data-voice-permission-cta="enable-mic"]');
    await act(async () => {
      cta?.click();
    });
    await flush();
    expect(connectMock).toHaveBeenCalledTimes(1);
    disconnectMock.mockClear();

    // Close the layer: VoiceConversation must disconnect; the reel base remains.
    await act(async () => {
      root.render(<Host isOpen={false} />);
    });
    await flush();

    expect(disconnectMock).toHaveBeenCalled();
    expect(container.querySelector("[data-reel-base]")).not.toBeNull();
  });
});
