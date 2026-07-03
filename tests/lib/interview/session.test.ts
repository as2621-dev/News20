import { beforeEach, describe, expect, it } from "vitest";

/**
 * Unit tests for the interview resume store (FSR slice #4).
 *
 * jsdom provides a real `localStorage`, so these exercise the actual read/write path.
 *
 * Rule 9 — WHY: the resume store is the ONLY thing standing between a mid-interview
 * abandon and a clean restart-or-resume. A corrupt/oversized/version-mismatched cache
 * must degrade to "no resume" (a fresh start), never to a thrown error that traps the
 * user, and an empty transcript must clear the cache (nothing to resume).
 */

import { clearInterviewSession, loadInterviewSession, saveInterviewSession } from "@/lib/interview/session";
import type { InterviewExchange } from "@/types/interview";

const SAMPLE: InterviewExchange[] = [
  {
    question_text: "What are you into?",
    bubbles_offered: ["Sport", "Tech"],
    bubbles_tapped: ["Sport"],
    free_text_entered: null,
  },
];

/**
 * A minimal in-memory localStorage stub (mirrors tests/lib/onboardingProfile.test.ts):
 * the jsdom/node build's native localStorage lacks a usable `.clear()`.
 */
function installLocalStorageStub(): void {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
      clear: () => store.clear(),
    },
    configurable: true,
    writable: true,
  });
}

beforeEach(() => {
  installLocalStorageStub();
});

describe("interview session store", () => {
  it("round-trips a saved transcript", () => {
    saveInterviewSession(SAMPLE);
    expect(loadInterviewSession()).toEqual(SAMPLE);
  });

  it("returns null when nothing is stored", () => {
    expect(loadInterviewSession()).toBeNull();
  });

  it("clears the cache when saving an empty transcript (nothing to resume)", () => {
    saveInterviewSession(SAMPLE);
    saveInterviewSession([]);
    expect(loadInterviewSession()).toBeNull();
  });

  it("clearInterviewSession removes a stored transcript", () => {
    saveInterviewSession(SAMPLE);
    clearInterviewSession();
    expect(loadInterviewSession()).toBeNull();
  });

  it("returns null on a corrupt (non-JSON) cache instead of throwing", () => {
    window.localStorage.setItem("n20-interview-session", "{not json");
    expect(loadInterviewSession()).toBeNull();
  });

  it("returns null on a version mismatch (schema change invalidates cleanly)", () => {
    window.localStorage.setItem(
      "n20-interview-session",
      JSON.stringify({ version: 99, conversation_state: SAMPLE, saved_at_ms: Date.now() }),
    );
    expect(loadInterviewSession()).toBeNull();
  });
});
