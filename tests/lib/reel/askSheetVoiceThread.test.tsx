import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Rolling dual-transcript thread tests for AskSheetVoice (issue #40).
 *
 * Rendering uses React 19's `createRoot` + `act` directly (NO @testing-library),
 * mirroring `askSheetVoiceState.test.tsx`. The Gemini Live hook is mocked with
 * a param-capturing stub so tests can push transcript DELTAS through the real
 * `onTranscript` seam — the same fragments the live socket streams.
 *
 * Rule 9 — WHY these matter:
 *   - The voice sheet must show the WHOLE conversation as it flows (both roles,
 *     accumulated across turns) — the old render showed only the LAST user +
 *     LAST model turn, silently destroying the visible conversation on every
 *     new turn (the exact acceptance gap).
 *   - Deltas must APPEND to the current turn (rolling live partials), not
 *     spawn a bubble per fragment.
 *   - The thread must survive a switch to the typed sheet and back WITHIN the
 *     session (the sheet unmounts on switch), without leaking across stories.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import type { Story } from "@/types/feed";

vi.mock("@/lib/voice/fetchStoryCorpus", () => ({
  fetchStoryCorpus: vi.fn(async () => ""),
}));

/** The latest params AskSheetVoice passed to the mocked hook (onTranscript seam). */
let latestHookParams: {
  onTranscript?: (transcript: { role: "user" | "model"; text: string }) => void;
} = {};

vi.mock("@/lib/voice/useGeminiLive", () => ({
  GEMINI_LIVE_DEFAULT_VOICE: "Charon",
  GEMINI_LIVE_JORDAN_VOICE: "Sadaltager",
  useGeminiLive: (params: typeof latestHookParams) => {
    latestHookParams = params;
    return {
      status: "live",
      isSetupComplete: true,
      inputAmplitude: 0,
      connect: vi.fn(async () => {}),
      prewarmToken: vi.fn(),
      disconnect: vi.fn(),
    };
  },
}));

vi.mock("@/lib/voice/micPermission", () => ({
  getMicPermissionState: vi.fn(async () => "granted"),
  requestMicPermission: vi.fn(async () => ({ mic_permission_state: "granted" })),
}));

const { AskSheetVoice } = await import("@/components/blip/reel/AskSheetVoice");
const { clearAllVoiceTranscriptSessions } = await import("@/lib/voice/voiceTranscriptSession");

function makeStory(digest_id = "story-1"): Story {
  return {
    digest_id,
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
  clearAllVoiceTranscriptSessions();
  latestHookParams = {};
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
  localStorage.setItem("blip-voice-granted", "1");
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

async function renderSheet(story: Story = makeStory()): Promise<void> {
  await act(async () => {
    root.render(<AskSheetVoice story={story} onClose={vi.fn()} onOpenArticle={vi.fn()} />);
  });
}

/** Push one transcript delta through the captured hook seam. */
async function pushTranscript(role: "user" | "model", text: string): Promise<void> {
  expect(latestHookParams.onTranscript).toBeDefined();
  await act(async () => {
    latestHookParams.onTranscript?.({ role, text });
  });
}

describe("AskSheetVoice rolling dual-transcript thread", () => {
  it("accumulates BOTH roles across turns, oldest first (happy path)", async () => {
    await renderSheet();

    await pushTranscript("user", "What led");
    await pushTranscript("user", " to this?");
    await pushTranscript("model", "A chip");
    await pushTranscript("model", " shortage.");
    await pushTranscript("user", "Who is affected?");
    await pushTranscript("model", "Mostly automakers.");

    const userBubbles = [...container.querySelectorAll(".bub-q.voiced span:last-child")].map(
      (node) => node.textContent,
    );
    expect(userBubbles).toEqual(["What led to this?", "Who is affected?"]);
    const modelBubbles = [...container.querySelectorAll(".bub-a p")].map((node) => node.textContent);
    expect(modelBubbles).toEqual(["A chip shortage.", "Mostly automakers."]);

    // Order in the DOM is conversation order: q, a, q, a.
    const bubbleSequence = [...container.querySelectorAll(".bub-q.voiced, .bub-a")].map((node) =>
      node.classList.contains("bub-a") ? "a" : "q",
    );
    expect(bubbleSequence).toEqual(["q", "a", "q", "a"]);
  });

  it("keeps the thread across a switch-away-and-back within the session (edge case)", async () => {
    await renderSheet();
    await pushTranscript("user", "What led to this?");
    await pushTranscript("model", "A chip shortage.");

    // Switch to the typed sheet: the voice sheet unmounts…
    await act(async () => {
      root.unmount();
    });
    root = createRoot(container);
    // …and back: same story remounts with the session thread intact.
    await renderSheet();

    expect(container.querySelector(".bub-q.voiced span:last-child")?.textContent).toBe("What led to this?");
    expect(container.querySelector(".bub-a p")?.textContent).toBe("A chip shortage.");
  });

  it("never leaks a thread across stories (failure/contamination case)", async () => {
    await renderSheet(makeStory("story-1"));
    await pushTranscript("user", "About story one");

    await act(async () => {
      root.unmount();
    });
    root = createRoot(container);
    await renderSheet(makeStory("story-2"));

    expect(container.querySelector(".bub-q.voiced")).toBeNull();
  });

  it("shows the read-full handoff only once a model answer exists", async () => {
    await renderSheet();
    expect(container.querySelector(".read-full")).toBeNull();

    await pushTranscript("user", "What led to this?");
    expect(container.querySelector(".read-full")).toBeNull();

    await pushTranscript("model", "A chip shortage.");
    expect(container.querySelector(".read-full")).not.toBeNull();
  });
});
