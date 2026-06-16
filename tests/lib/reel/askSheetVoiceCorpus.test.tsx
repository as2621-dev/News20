import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Corpus-injection tests for AskSheetVoice (voice-latency-hybrid-grounding SP3).
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `act` directly (NO
 * @testing-library — not a project dependency), mirroring
 * `tests/lib/reel/askSheetTypeThread.test.tsx`.
 *
 * Boundaries mocked (CLAUDE.md mocking strategy — never hit network/LLM/socket):
 *   - `@/lib/voice/fetchStoryCorpus` — returns a known corpus block (the SP2 fetcher).
 *   - `@/lib/voice/useGeminiLive` — a no-op hook that CAPTURES the `systemInstruction`
 *     it is handed, so we can assert the corpus reaches the Live setup frame WITHOUT
 *     opening a WebSocket or any audio context.
 *   - `@/lib/voice/micPermission` — keeps the component on the permission CTA so the
 *     test never drives the connect()/audio machinery (out of scope here).
 *
 * Rule 9 — WHY these matter:
 *   - The whole latency fix hinges on the fetched corpus actually landing inside the
 *     Live `systemInstruction`. If the wiring regresses (e.g. reverts to the
 *     tool-only builder), the model loses the in-context corpus and every question
 *     pays the slow round-trip again — these tests fail the moment that happens.
 *   - An empty corpus (fetch failure) must degrade to the tool-only voice (no STORY
 *     CONTEXT block), proving the graceful "" fallback.
 *   - The hybrid path is gated behind the NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT flag.
 *     With the flag OFF (default) the corpus GET must NEVER fire (no wasted request)
 *     and the legacy tool-forced instruction is used — a true revert to pre-phase
 *     behavior. With the flag ON the corpus is fetched + injected.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import type { Story } from "@/types/feed";

const fetchStoryCorpusMock = vi.fn<(story_id: string) => Promise<string>>();
vi.mock("@/lib/voice/fetchStoryCorpus", () => ({
  fetchStoryCorpus: (...args: [string]) => fetchStoryCorpusMock(...args),
}));

/** Captures the latest props (esp. systemInstruction) handed to the Live hook. */
const useGeminiLiveCapturedSystemInstructions: string[] = [];
vi.mock("@/lib/voice/useGeminiLive", () => ({
  GEMINI_LIVE_DEFAULT_VOICE: "Charon",
  GEMINI_LIVE_JORDAN_VOICE: "Sadaltager",
  useGeminiLive: (options: { systemInstruction: string }) => {
    useGeminiLiveCapturedSystemInstructions.push(options.systemInstruction);
    return {
      status: "idle" as const,
      isSetupComplete: false,
      inputAmplitude: 0,
      connect: vi.fn(async () => {}),
      disconnect: vi.fn(),
    };
  },
}));

// Force the permission CTA (no auto-connect): permission state is NOT granted.
vi.mock("@/lib/voice/micPermission", () => ({
  getMicPermissionState: vi.fn(async () => "prompt"),
  requestMicPermission: vi.fn(async () => ({ mic_permission_state: "prompt" })),
}));

// Import the SUT AFTER the mocks register.
const { AskSheetVoice } = await import("@/components/blip/reel/AskSheetVoice");

const CORPUS_BLOCK = "[p0] About 20% of global oil passes through the Strait of Hormuz.";

/**
 * The labeled corpus-block header the corpus-in-context builder emits. The grounding
 * clause itself mentions "STORY CONTEXT", so this exact header — NOT the bare phrase
 * — is the reliable discriminator between the corpus build and the tool-only build.
 */
const CORPUS_BLOCK_HEADER = "STORY CONTEXT (answer ONLY from this";

function makeStory(digest_id = "s1"): Story {
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

/** The grounding-clause marker for the legacy (flag-OFF) tool-forced instruction. */
const LEGACY_CLAUSE_MARKER = "You MUST NOT answer any factual question";

let container: HTMLDivElement;
let root: Root;
let previousFlagValue: string | undefined;

beforeEach(() => {
  vi.clearAllMocks();
  useGeminiLiveCapturedSystemInstructions.length = 0;
  // Default: flag ON for the original corpus-injection tests. Flag-OFF tests override.
  previousFlagValue = process.env.NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT;
  process.env.NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT = "true";
  // Default localStorage stub so readVoiceGrantedFlag() is "" (not granted).
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
  if (previousFlagValue === undefined) {
    delete process.env.NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT;
  } else {
    process.env.NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT = previousFlagValue;
  }
});

async function renderSheet(story: Story = makeStory()): Promise<void> {
  await act(async () => {
    root.render(<AskSheetVoice story={story} onClose={vi.fn()} onOpenArticle={vi.fn()} />);
  });
  // Flush the mount corpus-fetch microtask + the resulting re-render.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("AskSheetVoice — corpus injection into the Live system instruction", () => {
  it("fetches the corpus for the story and feeds it into the system instruction", async () => {
    fetchStoryCorpusMock.mockResolvedValue(CORPUS_BLOCK);

    await renderSheet(makeStory("s1"));

    // The corpus is fetched for THIS story.
    expect(fetchStoryCorpusMock).toHaveBeenCalledWith("s1");

    // After the fetch resolves, the LATEST systemInstruction handed to useGeminiLive
    // embeds the corpus STORY CONTEXT block — the core of the latency fix.
    const latestInstruction =
      useGeminiLiveCapturedSystemInstructions[useGeminiLiveCapturedSystemInstructions.length - 1];
    expect(latestInstruction).toContain(CORPUS_BLOCK_HEADER);
    expect(latestInstruction).toContain(CORPUS_BLOCK);
  });

  it("does NOT serialize behind the corpus: the first render uses the empty-corpus (tool-only) instruction", async () => {
    // WHY: connect() must be able to fire on the user's gesture WITHOUT waiting for
    // the corpus. The very first systemInstruction (before the async fetch resolves)
    // must be the tool-only build (no STORY CONTEXT) so the session never blocks.
    fetchStoryCorpusMock.mockResolvedValue(CORPUS_BLOCK);

    await renderSheet();

    expect(useGeminiLiveCapturedSystemInstructions.length).toBeGreaterThan(0);
    const firstInstruction = useGeminiLiveCapturedSystemInstructions[0];
    expect(firstInstruction).not.toContain(CORPUS_BLOCK_HEADER);
  });

  it("degrades to the tool-only instruction when the corpus fetch yields '' (graceful fallback)", async () => {
    fetchStoryCorpusMock.mockResolvedValue("");

    await renderSheet();

    const latestInstruction =
      useGeminiLiveCapturedSystemInstructions[useGeminiLiveCapturedSystemInstructions.length - 1];
    // No corpus → tool-only voice (no labeled STORY CONTEXT block), but still scoped to the story.
    expect(latestInstruction).not.toContain(CORPUS_BLOCK_HEADER);
    expect(latestInstruction).toContain("Why does Hormuz matter?");
  });
});

describe("AskSheetVoice — NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT flag OFF (legacy path)", () => {
  beforeEach(() => {
    // Flag OFF overrides the suite default — pre-phase tool-forced behavior.
    process.env.NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT = "false";
  });

  it("does NOT fetch the corpus when the flag is off (no wasted GET)", async () => {
    // WHY (Rule 9): the OFF path must be a TRUE revert — no corpus GET at all. If this
    // regresses (corpus fetched regardless of the flag), the A/B is meaningless and every
    // OFF session pays a wasted network request before the legacy tool-only voice.
    fetchStoryCorpusMock.mockResolvedValue(CORPUS_BLOCK);

    await renderSheet();

    expect(fetchStoryCorpusMock).not.toHaveBeenCalled();
  });

  it("uses the legacy tool-forced instruction (no STORY CONTEXT) when the flag is off", async () => {
    fetchStoryCorpusMock.mockResolvedValue(CORPUS_BLOCK);

    await renderSheet();

    const latestInstruction =
      useGeminiLiveCapturedSystemInstructions[useGeminiLiveCapturedSystemInstructions.length - 1];
    // Legacy = tool-FORCED clause, no injected corpus block.
    expect(latestInstruction).not.toContain(CORPUS_BLOCK_HEADER);
    expect(latestInstruction).toContain(LEGACY_CLAUSE_MARKER);
    expect(latestInstruction).toContain("Why does Hormuz matter?");
  });

  it("treats an unset flag as off (default legacy behavior)", async () => {
    delete process.env.NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT;
    fetchStoryCorpusMock.mockResolvedValue(CORPUS_BLOCK);

    await renderSheet();

    expect(fetchStoryCorpusMock).not.toHaveBeenCalled();
    const latestInstruction =
      useGeminiLiveCapturedSystemInstructions[useGeminiLiveCapturedSystemInstructions.length - 1];
    expect(latestInstruction).toContain(LEGACY_CLAUSE_MARKER);
  });
});
