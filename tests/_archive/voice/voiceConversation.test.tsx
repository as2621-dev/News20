import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Phase 3b SP4 — VoiceConversation open boundary: signal + quota gate.
 *
 * Rule 9 — these encode WHY the behaviour matters, not just WHAT happens:
 *   1. **Opening Voice fires EXACTLY ONE `player_signals` voice signal for the
 *      active story.** The signal is the deep-engagement input to tomorrow's
 *      ranking; a missing one under-weights it, a duplicate over-weights it. The
 *      DoD is "exactly one row per open" — a re-render must NOT re-fire it.
 *   2. **Over the daily Live-session quota, opening BLOCKS the socket and shows a
 *      calm cap message — it never silently fails (Rule 12).** This test FAILS if
 *      the quota gate is bypassed (connect called) or the calm message is missing.
 *      Under quota, the session proceeds (connect called) — the inverse guard.
 *
 * Mocks `useGeminiLive` (socket/mic boundary) and `@/lib/signals` (the Supabase +
 * localStorage boundary) so no real socket, DB, or storage is touched.
 */

// ---- useGeminiLive mock (socket boundary) ----------------------------------

const connectMock = vi.fn(async () => {});
const disconnectMock = vi.fn();

vi.mock("@/lib/voice/useGeminiLive", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/voice/useGeminiLive")>();
  return {
    ...actual,
    useGeminiLive: () => ({
      status: "live" as const,
      isSetupComplete: true,
      inputAmplitude: 0,
      connect: connectMock,
      disconnect: disconnectMock,
    }),
  };
});

// ---- signals mock (Supabase + localStorage boundary) -----------------------

const recordVoiceSignalMock = vi.fn(async (_storyId: string) => {});
const getVoiceQuotaStateMock = vi.fn(() => ({ seconds_used_today: 0, is_over_quota: false }));
const startVoiceQuotaHeartbeatMock = vi.fn(() => vi.fn());

vi.mock("@/lib/signals", () => ({
  recordVoiceSignal: (storyId: string) => recordVoiceSignalMock(storyId),
  getVoiceQuotaState: () => getVoiceQuotaStateMock(),
  startVoiceQuotaHeartbeat: () => startVoiceQuotaHeartbeatMock(),
}));

// Imported AFTER the mocks so the component picks them up.
import { VoiceConversation } from "@/components/voice/VoiceConversation";
import type { Story } from "@/types/feed";

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

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  connectMock.mockClear();
  disconnectMock.mockClear();
  recordVoiceSignalMock.mockClear();
  startVoiceQuotaHeartbeatMock.mockClear();
  getVoiceQuotaStateMock.mockReturnValue({ seconds_used_today: 0, is_over_quota: false });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.restoreAllMocks();
});

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("VoiceConversation — voice signal on open (DoD, Rule 9)", () => {
  it("records exactly one voice signal for the active story on open", async () => {
    await act(async () => {
      root.render(<VoiceConversation story={ACTIVE_STORY} isOpen prefers_reduced_motion={false} />);
    });
    await flush();

    expect(recordVoiceSignalMock).toHaveBeenCalledTimes(1);
    expect(recordVoiceSignalMock).toHaveBeenCalledWith(ACTIVE_STORY.digest_id);
  });

  it("does NOT re-fire the signal on a re-render (exactly one row per open)", async () => {
    // WHY: the DoD is "exactly one row per open". The open effect is keyed on
    // [isOpen, digest_id]; a benign prop change (e.g. prefers_reduced_motion) must
    // NOT re-run it. Fails if the effect deps over-fire.
    await act(async () => {
      root.render(<VoiceConversation story={ACTIVE_STORY} isOpen prefers_reduced_motion={false} />);
    });
    await flush();
    expect(recordVoiceSignalMock).toHaveBeenCalledTimes(1);

    // Re-render with an unrelated prop change — same open, same story.
    await act(async () => {
      root.render(<VoiceConversation story={ACTIVE_STORY} isOpen prefers_reduced_motion />);
    });
    await flush();

    expect(recordVoiceSignalMock).toHaveBeenCalledTimes(1);
  });
});

describe("VoiceConversation — daily quota gate (DoD, Rule 12)", () => {
  it("BLOCKS the socket and shows the calm cap message when over quota", async () => {
    // WHY: the paid Live WSS is the cost ceiling. Over the daily cap, opening MUST
    // NOT connect and MUST surface a calm message — never silently fail. Fails if
    // connect() is called or the cap message is missing.
    getVoiceQuotaStateMock.mockReturnValue({ seconds_used_today: 600, is_over_quota: true });

    await act(async () => {
      root.render(<VoiceConversation story={ACTIVE_STORY} isOpen prefers_reduced_motion={false} />);
    });
    await flush();

    expect(connectMock).not.toHaveBeenCalled();
    expect(startVoiceQuotaHeartbeatMock).not.toHaveBeenCalled();
    const block = container.querySelector("[data-voice-quota-blocked]");
    expect(block).not.toBeNull();
    expect(block?.textContent?.toLowerCase()).toContain("voice limit");
    // The signal still fires (the open intent happened) even when blocked.
    expect(recordVoiceSignalMock).toHaveBeenCalledTimes(1);
  });

  it("proceeds (connects + starts heartbeat) when under quota", async () => {
    // WHY: the inverse guard — under the cap the session MUST open. Together with
    // the block test, this fails if the quota gate is inverted or bypassed.
    getVoiceQuotaStateMock.mockReturnValue({ seconds_used_today: 10, is_over_quota: false });

    await act(async () => {
      root.render(<VoiceConversation story={ACTIVE_STORY} isOpen prefers_reduced_motion={false} />);
    });
    await flush();

    expect(connectMock).toHaveBeenCalledTimes(1);
    expect(startVoiceQuotaHeartbeatMock).toHaveBeenCalledTimes(1);
    expect(container.querySelector("[data-voice-quota-blocked]")).toBeNull();
  });
});
