/**
 * Tests for the corpus-in-context Voice system instruction builder
 * (voice-latency-hybrid-grounding SP2).
 *
 * WHY (Rule 9): the live model is grounded by the instruction string alone — there
 * is no server verification on the corpus path. So the contract that MUST hold is:
 *   1. Empty corpus ⇒ byte-identical to the tool-only instruction (the single
 *      graceful-degradation seam — a drift here would silently change the fallback
 *      voice or, worse, ship an empty STORY CONTEXT block to the model).
 *   2. Non-empty corpus ⇒ the instruction carries the corpus block, the SAME persona
 *      scope as the tool-only path (no voice drift), and the appended tool clause.
 * These are PURE assertions — no socket, no fetch (Rule 9).
 */

import { describe, expect, it } from "vitest";
import { buildInNewsSystemInstruction, buildInNewsSystemInstructionWithCorpus } from "@/lib/voice/storyVoicePrompts";

const HEADLINE = "Ceasefire talks stall";
const STORY_ID = "s1";
const CORPUS = "[detail_chunk:0] About 20% of global oil passes through Hormuz.\n[detail_chunk:1] Talks stalled.";
const TOOL_CLAUSE = "Call ask_about_story ONLY when the answer is not in the STORY CONTEXT.";

describe("buildInNewsSystemInstructionWithCorpus — empty corpus falls back to tool-only", () => {
  it("equals buildInNewsSystemInstruction when the corpus is empty", () => {
    // WHY: the single graceful-degradation seam — an empty corpus from
    // fetchStoryCorpus MUST yield today's exact tool-only instruction.
    const withCorpus = buildInNewsSystemInstructionWithCorpus(HEADLINE, STORY_ID, "", TOOL_CLAUSE);
    const toolOnly = buildInNewsSystemInstruction(HEADLINE, STORY_ID, TOOL_CLAUSE);

    expect(withCorpus).toBe(toolOnly);
  });

  it("treats a whitespace-only corpus as empty (still tool-only)", () => {
    const withCorpus = buildInNewsSystemInstructionWithCorpus(HEADLINE, STORY_ID, "   \n  ", TOOL_CLAUSE);
    const toolOnly = buildInNewsSystemInstruction(HEADLINE, STORY_ID, TOOL_CLAUSE);

    expect(withCorpus).toBe(toolOnly);
  });

  it("falls back without a tool clause too (omitted clause preserved)", () => {
    expect(buildInNewsSystemInstructionWithCorpus(HEADLINE, STORY_ID, "")).toBe(
      buildInNewsSystemInstruction(HEADLINE, STORY_ID),
    );
  });
});

describe("buildInNewsSystemInstructionWithCorpus — non-empty corpus", () => {
  const instruction = buildInNewsSystemInstructionWithCorpus(HEADLINE, STORY_ID, CORPUS, TOOL_CLAUSE);

  it("embeds the corpus inside a labeled STORY CONTEXT block", () => {
    expect(instruction).toContain("STORY CONTEXT (answer ONLY from this; each line is [passage_id] text):");
    expect(instruction).toContain(CORPUS);
  });

  it("reuses the same persona + single-story scope as the tool-only path (no voice drift)", () => {
    // WHY (Rule 3): the persona/scope must be the SAME string across both paths.
    expect(instruction).toContain("You are Jordan, one of blip's two news hosts");
    expect(instruction).toContain(`You are scoped to exactly ONE story (id ${STORY_ID}): "${HEADLINE}".`);
  });

  it("instructs corpus-only answering and forbids reading citations aloud / outside knowledge", () => {
    expect(instruction).toMatch(/answer ONLY from/i);
    expect(instruction).toContain("Never use outside knowledge.");
    expect(instruction).toContain("Never read passage ids or citations aloud.");
    expect(instruction).toMatch(/short and spoken-natural/);
  });

  it("appends the tool grounding clause last", () => {
    expect(instruction).toContain(TOOL_CLAUSE);
    expect(instruction.trimEnd().endsWith(TOOL_CLAUSE)).toBe(true);
  });

  it("does not embed a STORY CONTEXT block when omitting the tool clause but keeps the corpus", () => {
    const noClause = buildInNewsSystemInstructionWithCorpus(HEADLINE, STORY_ID, CORPUS);
    expect(noClause).toContain(CORPUS);
    expect(noClause).not.toContain(TOOL_CLAUSE);
  });
});
