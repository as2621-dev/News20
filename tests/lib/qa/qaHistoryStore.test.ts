/**
 * Tests for the per-story Q&A thread persistence (Bug 5).
 *
 * WHY: the ask sheet unmounts on close, so the thread MUST round-trip through
 * localStorage — per story, bounded (turn cap + story eviction), and immune to
 * corrupt/blocked storage (a broken store degrades to "no history", never an
 * error the sheet can crash on).
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearQaThreadForStory,
  loadQaThreadForStory,
  MAX_PERSISTED_STORIES,
  MAX_PERSISTED_TURNS_PER_STORY,
  QA_HISTORY_STORAGE_KEY,
  saveQaThreadForStory,
} from "@/lib/qa/qaHistoryStore";
import type { CompletedQaTurn } from "@/types/qa";

function buildTurn(question_text: string): CompletedQaTurn {
  return {
    question_text,
    answer: { answer_text: `answer to ${question_text}`, answer_citations: [], answer_is_grounded: true },
  };
}

/** A minimal in-memory localStorage stub (the signals.test.ts pattern). */
function installLocalStorageStub(): { store: Map<string, string> } {
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
  return { store };
}

/** A localStorage stub whose every access throws (private-mode simulation). */
function installThrowingLocalStorageStub(): void {
  const throwingStub = {
    getItem: () => {
      throw new Error("private mode");
    },
    setItem: () => {
      throw new Error("private mode");
    },
    removeItem: () => {
      throw new Error("private mode");
    },
    clear: () => {
      throw new Error("private mode");
    },
  };
  Object.defineProperty(globalThis, "localStorage", { value: throwingStub, configurable: true, writable: true });
}

beforeEach(() => {
  installLocalStorageStub();
});

describe("qaHistoryStore", () => {
  it("round-trips a thread per story (happy path)", () => {
    saveQaThreadForStory("s1", { completed_turns: [buildTurn("q1")], draft_question_text: "draft text" });

    const thread = loadQaThreadForStory("s1");
    expect(thread?.completed_turns).toHaveLength(1);
    expect(thread?.completed_turns[0].question_text).toBe("q1");
    expect(thread?.draft_question_text).toBe("draft text");
  });

  it("isolates threads between stories", () => {
    saveQaThreadForStory("s1", { completed_turns: [buildTurn("about s1")], draft_question_text: "" });
    saveQaThreadForStory("s2", { completed_turns: [buildTurn("about s2")], draft_question_text: "" });

    expect(loadQaThreadForStory("s1")?.completed_turns[0].question_text).toBe("about s1");
    expect(loadQaThreadForStory("s2")?.completed_turns[0].question_text).toBe("about s2");
    expect(loadQaThreadForStory("s3")).toBeNull();
  });

  it("caps persisted turns per story (edge case)", () => {
    const manyTurns = Array.from({ length: MAX_PERSISTED_TURNS_PER_STORY + 5 }, (_unused, index) =>
      buildTurn(`q${index}`),
    );
    saveQaThreadForStory("s1", { completed_turns: manyTurns, draft_question_text: "" });

    const thread = loadQaThreadForStory("s1");
    expect(thread?.completed_turns).toHaveLength(MAX_PERSISTED_TURNS_PER_STORY);
    // The OLDEST turns were dropped; the newest survive.
    expect(thread?.completed_turns[0].question_text).toBe("q5");
  });

  it("evicts the least-recently-updated story beyond the story cap", () => {
    vi.useFakeTimers();
    try {
      for (let index = 0; index <= MAX_PERSISTED_STORIES; index += 1) {
        vi.setSystemTime(1_000_000 + index * 1000);
        saveQaThreadForStory(`story-${index}`, { completed_turns: [buildTurn("q")], draft_question_text: "" });
      }
      // story-0 (oldest updated_at_ms) was evicted; the newest survives.
      expect(loadQaThreadForStory("story-0")).toBeNull();
      expect(loadQaThreadForStory(`story-${MAX_PERSISTED_STORIES}`)).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns null (no throw) on malformed stored JSON (failure case)", () => {
    localStorage.setItem(QA_HISTORY_STORAGE_KEY, "{not json");
    expect(loadQaThreadForStory("s1")).toBeNull();
    // The next save overwrites the corrupt blob.
    saveQaThreadForStory("s1", { completed_turns: [buildTurn("q1")], draft_question_text: "" });
    expect(loadQaThreadForStory("s1")?.completed_turns).toHaveLength(1);
  });

  it("swallows a throwing localStorage (failure case)", () => {
    installThrowingLocalStorageStub();
    expect(loadQaThreadForStory("s1")).toBeNull();
    expect(() => saveQaThreadForStory("s1", { completed_turns: [], draft_question_text: "" })).not.toThrow();
  });

  it("clearQaThreadForStory removes only that story", () => {
    saveQaThreadForStory("s1", { completed_turns: [buildTurn("q1")], draft_question_text: "" });
    saveQaThreadForStory("s2", { completed_turns: [buildTurn("q2")], draft_question_text: "" });

    clearQaThreadForStory("s1");

    expect(loadQaThreadForStory("s1")).toBeNull();
    expect(loadQaThreadForStory("s2")).not.toBeNull();
  });
});
