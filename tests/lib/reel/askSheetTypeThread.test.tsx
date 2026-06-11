import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Thread-model tests for AskSheetType (Bugs 3 + 5).
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `act` directly
 * (NO @testing-library — not a project dependency), mirroring
 * `tests/lib/sources/sourceSwipe.test.tsx`.
 *
 * `askQuestion` is mocked at the module boundary (CLAUDE.md mocking strategy);
 * `qaHistoryStore` runs REAL against an in-memory localStorage stub so the
 * persistence contract (hydrate-on-mount) is tested end-to-end.
 *
 * Rule 9 — WHY each behaviour matters:
 *   - The thread must ACCUMULATE turns: replacing the previous Q&A pair (the
 *     old single-pair state machine) silently destroyed the conversation.
 *   - A follow-up must SHIP the prior turns: without them the worker answers
 *     each question in isolation and follow-ups like "what about it?" break.
 *   - A refusal renders INSIDE the thread (not a whole-sheet takeover) so the
 *     conversation survives one off-topic question.
 *   - A previously saved thread must REHYDRATE on mount — the sheet unmounts
 *     on close, so without hydration history is lost (Bug 5's exact symptom).
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import type { Story } from "@/types/feed";
import type { QuestionAnswer } from "@/types/qa";

const askQuestionMock = vi.fn();
vi.mock("@/lib/qa/askQuestion", async () => {
  const actual = await vi.importActual<typeof import("@/lib/qa/askQuestion")>("@/lib/qa/askQuestion");
  return { ...actual, askQuestion: (...args: unknown[]) => askQuestionMock(...args) };
});

// Import the SUT (and the real store) AFTER the mock registers.
const { AskSheetType } = await import("@/components/blip/reel/AskSheetType");
const { saveQaThreadForStory } = await import("@/lib/qa/qaHistoryStore");

/** A minimal in-memory localStorage stub (the signals.test.ts pattern). */
function installLocalStorageStub(): void {
  const store = new Map<string, string>();
  const stub = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
  };
  Object.defineProperty(globalThis, "localStorage", { value: stub, configurable: true, writable: true });
}

/** A minimal Story fixture — only the fields AskSheetType touches matter. */
function makeStory(digest_id = "s1"): Story {
  return {
    digest_id,
    headline: "Intel vs TSMC",
    segment_key: "markets",
    segment_label: "Markets",
    segment_accent_hex: "#22C55E",
    anchors: ["ALEX", "JORDAN"],
    digest_audio_url: "",
    audio_duration_ms: 1000,
    speech_end_ms: 1000,
    poster_url: "",
    caption_sentences: [],
  } as unknown as Story;
}

function groundedAnswer(answer_text: string): QuestionAnswer {
  return { answer_text, answer_citations: [], answer_is_grounded: true };
}

function refusalAnswer(): QuestionAnswer {
  return {
    answer_text: "I can't answer that from this story's sources.",
    answer_citations: [],
    answer_is_grounded: false,
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  installLocalStorageStub();
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
    root.render(<AskSheetType story={story} onClose={vi.fn()} onOpenArticle={vi.fn()} />);
  });
}

/** Click the first suggested question ("What led to this?") and flush the ask. */
async function askFirstSuggested(): Promise<void> {
  const suggestedButton = container.querySelector<HTMLButtonElement>("button.sq");
  expect(suggestedButton).not.toBeNull();
  await act(async () => {
    suggestedButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

/** Type into the follow-up input and submit its form. */
async function askFollowup(question_text: string): Promise<void> {
  const followupInput = container.querySelector<HTMLInputElement>("form.followup input");
  expect(followupInput).not.toBeNull();
  if (followupInput) {
    followupInput.value = question_text;
  }
  const followupForm = container.querySelector<HTMLFormElement>("form.followup");
  await act(async () => {
    followupForm?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

describe("AskSheetType thread model", () => {
  it("accumulates every turn as bubbles instead of replacing the last pair", async () => {
    askQuestionMock
      .mockResolvedValueOnce(groundedAnswer("Because of the chip race."))
      .mockResolvedValueOnce(groundedAnswer("Margins are around 53%."));
    await renderSheet();

    await askFirstSuggested();
    await askFollowup("What about its margins?");

    const questionBubbles = [...container.querySelectorAll(".bub-q")].map((node) => node.textContent);
    expect(questionBubbles).toEqual(["What led to this?", "What about its margins?"]);
    const answerBubbles = [...container.querySelectorAll(".bub-a")].map((node) => node.textContent);
    expect(answerBubbles).toEqual(["Because of the chip race.", "Margins are around 53%."]);
  });

  it("ships the accumulated prior turns with a follow-up", async () => {
    askQuestionMock
      .mockResolvedValueOnce(groundedAnswer("Because of the chip race."))
      .mockResolvedValueOnce(groundedAnswer("Margins are around 53%."));
    await renderSheet();

    await askFirstSuggested();
    await askFollowup("What about its margins?");

    expect(askQuestionMock).toHaveBeenCalledTimes(2);
    // First ask: no prior turns.
    expect(askQuestionMock.mock.calls[0][2]).toEqual([]);
    // Follow-up: the full first exchange, user then model.
    expect(askQuestionMock.mock.calls[1][2]).toEqual([
      { role: "user", text: "What led to this?" },
      { role: "model", text: "Because of the chip race." },
    ]);
  });

  it("renders a refusal card inline without destroying the thread", async () => {
    askQuestionMock
      .mockResolvedValueOnce(groundedAnswer("Because of the chip race."))
      .mockResolvedValueOnce(refusalAnswer());
    await renderSheet();

    await askFirstSuggested();
    await askFollowup("Who wins the World Cup?");

    expect(container.querySelectorAll(".bub-q")).toHaveLength(2);
    expect(container.querySelectorAll(".bub-a")).toHaveLength(1);
    expect(container.querySelector(".refusal")).not.toBeNull();
  });

  it("rehydrates a previously saved thread on mount (Bug 5)", async () => {
    saveQaThreadForStory("s1", {
      completed_turns: [{ question_text: "Saved question?", answer: groundedAnswer("Saved answer.") }],
      draft_question_text: "",
    });

    await renderSheet(makeStory("s1"));

    expect(container.querySelector(".bub-q")?.textContent).toBe("Saved question?");
    expect(container.querySelector(".bub-a")?.textContent).toBe("Saved answer.");
    // The thread view (not the empty composer state) is showing.
    expect(container.querySelector(".sq-list")).toBeNull();
  });
});
