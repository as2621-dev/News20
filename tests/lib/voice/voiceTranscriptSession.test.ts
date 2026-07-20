import { beforeEach, describe, expect, it } from "vitest";

/**
 * In-session voice transcript store tests (issue #40).
 *
 * Rule 9 — WHY these matter:
 *   - The voice sheet UNMOUNTS whenever the user switches to the typed sheet or
 *     closes the overlay; without an in-session store the rolling transcript is
 *     wiped and reopening voice mid-conversation loses everything said so far.
 *   - Transcripts are keyed PER STORY — a thread leaking across stories would
 *     show another story's conversation, which is worse than showing nothing.
 *   - The store is memory-only (module state): cross-session persistence is
 *     explicitly OUT of scope for this slice, so nothing may touch localStorage.
 */

import {
  clearAllVoiceTranscriptSessions,
  loadVoiceTranscriptForStory,
  MAX_SESSION_TURNS_PER_STORY,
  saveVoiceTranscriptForStory,
} from "@/lib/voice/voiceTranscriptSession";

beforeEach(() => {
  clearAllVoiceTranscriptSessions();
});

describe("voiceTranscriptSession", () => {
  it("returns the saved turns for the same story (happy path)", () => {
    saveVoiceTranscriptForStory("story-1", [
      { role: "user", text: "What led to this?" },
      { role: "model", text: "A chip shortage." },
    ]);
    expect(loadVoiceTranscriptForStory("story-1")).toEqual([
      { role: "user", text: "What led to this?" },
      { role: "model", text: "A chip shortage." },
    ]);
  });

  it("returns an empty list for a story with no session (failure/empty case)", () => {
    expect(loadVoiceTranscriptForStory("never-seen")).toEqual([]);
  });

  it("keeps stories isolated — no cross-story leak", () => {
    saveVoiceTranscriptForStory("story-1", [{ role: "user", text: "About story one" }]);
    saveVoiceTranscriptForStory("story-2", [{ role: "user", text: "About story two" }]);
    expect(loadVoiceTranscriptForStory("story-1")).toEqual([{ role: "user", text: "About story one" }]);
    expect(loadVoiceTranscriptForStory("story-2")).toEqual([{ role: "user", text: "About story two" }]);
  });

  it("caps a story's turns, keeping the NEWEST (edge case)", () => {
    const overflowingTurns = Array.from({ length: MAX_SESSION_TURNS_PER_STORY + 5 }, (_, index) => ({
      role: "user" as const,
      text: `turn ${index}`,
    }));
    saveVoiceTranscriptForStory("story-1", overflowingTurns);
    const storedTurns = loadVoiceTranscriptForStory("story-1");
    expect(storedTurns).toHaveLength(MAX_SESSION_TURNS_PER_STORY);
    expect(storedTurns[storedTurns.length - 1].text).toBe(`turn ${MAX_SESSION_TURNS_PER_STORY + 4}`);
  });

  it("returns a defensive copy — mutating the loaded array never corrupts the store", () => {
    saveVoiceTranscriptForStory("story-1", [{ role: "user", text: "original" }]);
    const loadedTurns = loadVoiceTranscriptForStory("story-1");
    loadedTurns.push({ role: "model", text: "mutated" });
    expect(loadVoiceTranscriptForStory("story-1")).toHaveLength(1);
  });
});
