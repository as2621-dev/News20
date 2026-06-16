import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Tests for phase-3b SP3 — the `ask_about_story` Gemini Live tool wiring.
 *
 * Rule 9 — these encode WHY the trust contract matters, not just WHAT the wiring
 * does:
 *   1. **On-topic round-trip** — a `toolCall` carrying `question_text` runs the
 *      grounded path exactly once for the active `story_id` and relays the grounded
 *      `answer_text` + its citations. This is the path that lets a commuter actually
 *      interrogate the story.
 *   2. **Refusal is relayed, never replaced** — when the server says
 *      `answer_is_grounded:false` (refusal copy, empty citations), the handler's
 *      response carries that verdict and copy verbatim and emits NO fabricated
 *      answer or citation. This test FAILS the moment someone makes the handler
 *      invent content on a refusal — the zero-hallucination contract (Decision #5).
 *   3. **The declaration is the contract the model sees** — name `ask_about_story`
 *      with a single required string `question_text`.
 *   4. **The system instruction FORBIDS answering without the tool** — the assembled
 *      `buildInNewsSystemInstruction(..., STORY_QA_TOOL_GROUNDING_CLAUSE)` contains
 *      the forbidding clause, so the model is mechanically pushed onto the grounded
 *      path (not relying on the base persona alone).
 *
 * `@/lib/qa/askQuestion` is MOCKED at the boundary (CLAUDE.md mocking rule): no real
 * HTTP, worker, or LLM is touched. The worker is undeployed (`news20-m2-2b-2c-gated-state`)
 * so every assertion here is mock-verified.
 */

import type { QuestionAnswer } from "@/types/qa";

// ---- askQuestion mock (boundary) -------------------------------------------

vi.mock("@/lib/qa/askQuestion", () => ({
  askQuestion: vi.fn(),
}));

// Imported AFTER the mock so the tool picks up the spied client.
import { askQuestion } from "@/lib/qa/askQuestion";
import {
  ASK_ABOUT_STORY_TOOL_NAME,
  askAboutStoryDeclaration,
  buildAskAboutStoryHandler,
  LEGACY_TOOL_FORCED_CLAUSE,
  STORY_QA_TOOL_GROUNDING_CLAUSE,
} from "@/lib/voice/storyQaTool";
import { buildInNewsSystemInstructionWithCorpus } from "@/lib/voice/storyVoicePrompts";
import type { GeminiToolCall } from "@/lib/voice/useGeminiLive";

const askQuestionMock = vi.mocked(askQuestion);

const STORY_ID = "s1";

/** A grounded answer the server might return for an on-topic question. */
const GROUNDED_ANSWER: QuestionAnswer = {
  answer_text: "Roughly a fifth of global oil flows through the strait.",
  answer_is_grounded: true,
  answer_citations: [
    {
      source_url: "https://reuters.example/hormuz",
      source_quote: "About 20% of global oil passes through the Strait of Hormuz.",
      source_outlet_name: "Reuters",
      passage_id: "detail_chunk:0",
    },
  ],
};

/** The refusal the server returns when its sources can't support the question. */
const REFUSAL_ANSWER: QuestionAnswer = {
  answer_text: "I can only answer from this story's source — that isn't available right now.",
  answer_is_grounded: false,
  answer_citations: [],
};

/** Build a model→client tool call carrying a question. */
function makeToolCall(args: Record<string, unknown>): GeminiToolCall {
  return { id: "call-1", name: ASK_ABOUT_STORY_TOOL_NAME, args };
}

beforeEach(() => {
  askQuestionMock.mockReset();
});

describe("askAboutStoryDeclaration", () => {
  it("declares ask_about_story with a single required string question_text", () => {
    // WHY (Rule 9): this is the exact contract the model is given; a drift in the
    // name or the required arg would silently break every tool call the model emits.
    expect(askAboutStoryDeclaration.name).toBe("ask_about_story");

    const parameters = askAboutStoryDeclaration.parameters as {
      type: string;
      properties: { question_text?: { type: string } };
      required: string[];
    };
    expect(parameters.type).toBe("object");
    expect(parameters.properties.question_text?.type).toBe("string");
    expect(parameters.required).toEqual(["question_text"]);
  });
});

describe("STORY_QA_TOOL_GROUNDING_CLAUSE in the assembled system instruction", () => {
  it("enforces corpus-first answering with tool-on-miss + a spoken filler line", () => {
    // WHY (Rule 9): the clause mechanizes the latency-fix contract — answer from the
    // injected STORY CONTEXT directly, and call the tool ONLY on a corpus miss, after
    // a short spoken filler. The assembled instruction must actually carry it (not
    // just the softer base persona) or the model would route every question through
    // the slow tool path (defeating the whole latency fix) or answer ungrounded.
    const instruction = buildInNewsSystemInstructionWithCorpus(
      "Why does Hormuz matter?",
      STORY_ID,
      "[p0] About 20% of global oil passes through the Strait of Hormuz.",
      STORY_QA_TOOL_GROUNDING_CLAUSE,
    );

    expect(instruction).toContain(STORY_QA_TOOL_GROUNDING_CLAUSE);
    expect(instruction).toContain("ask_about_story");
    // Corpus-first: answer from STORY CONTEXT directly.
    expect(instruction).toMatch(/STORY CONTEXT/);
    expect(STORY_QA_TOOL_GROUNDING_CLAUSE).toMatch(/Answer questions from it directly/);
    // Tool-on-miss only.
    expect(STORY_QA_TOOL_GROUNDING_CLAUSE).toMatch(/ONLY when the answer is NOT in your STORY CONTEXT/);
    // Spoken filler before the round-trip.
    expect(STORY_QA_TOOL_GROUNDING_CLAUSE).toMatch(/let me check that/);
    // Verbatim relay + refusal handling preserved.
    expect(STORY_QA_TOOL_GROUNDING_CLAUSE).toMatch(/answer_text verbatim/);
    expect(STORY_QA_TOOL_GROUNDING_CLAUSE).toMatch(/answer_is_grounded false/);
  });

  it("keeps a separate LEGACY_TOOL_FORCED_CLAUSE for the flag-OFF A/B path", () => {
    // WHY (Rule 9): the NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT=off path must be a TRUE
    // revert to pre-phase behavior — every factual question forced through the tool,
    // never answered from the model's own knowledge (there is no STORY CONTEXT on the
    // OFF path). If this legacy clause silently drifts to corpus-first wording, flag-OFF
    // would stop forcing the tool and could answer ungrounded. These assertions encode
    // the original tool-forced contract.
    expect(LEGACY_TOOL_FORCED_CLAUSE).toContain("You MUST NOT answer any factual question");
    expect(LEGACY_TOOL_FORCED_CLAUSE).toMatch(/For every such question, call ask_about_story/);
    expect(LEGACY_TOOL_FORCED_CLAUSE).toMatch(/answer_is_grounded false/);
    // The legacy clause is tool-FORCED, NOT corpus-first: it must not tell the model it
    // already has the story in a STORY CONTEXT (the OFF path injects no corpus).
    expect(LEGACY_TOOL_FORCED_CLAUSE).not.toMatch(/STORY CONTEXT/);
    // And it is distinct from the new corpus-first clause (clean A/B).
    expect(LEGACY_TOOL_FORCED_CLAUSE).not.toBe(STORY_QA_TOOL_GROUNDING_CLAUSE);
  });

  it("declaration description scopes the tool to web-only fallback usage", () => {
    // WHY: the model reads the declaration description to decide WHEN to call the
    // tool. It must say "only for questions the story context does not cover" so the
    // common (corpus-answerable) case never pays the round-trip.
    expect(askAboutStoryDeclaration.description).toMatch(/story context does not cover/i);
    expect(askAboutStoryDeclaration.description).toMatch(/web-searched answer with citations/i);
    expect(askAboutStoryDeclaration.description).toMatch(/refusal|pushback/i);
  });
});

describe("buildAskAboutStoryHandler — on-topic grounded round-trip", () => {
  it("calls askQuestion once for the story and relays the grounded answer + citations", async () => {
    askQuestionMock.mockResolvedValue(GROUNDED_ANSWER);
    const handler = buildAskAboutStoryHandler(STORY_ID);

    const response = await handler(makeToolCall({ question_text: "Why does Hormuz matter?" }));

    // Round-trip: the grounded path is hit exactly once, scoped to THIS story, with
    // web_only=true (the tool fires only on a corpus miss → server skips the wasted
    // corpus answer+verify and answers from web search). The 5th positional arg is
    // the load-bearing contract this whole latency fix relies on.
    expect(askQuestionMock).toHaveBeenCalledTimes(1);
    expect(askQuestionMock).toHaveBeenCalledWith(STORY_ID, "Why does Hormuz matter?", [], fetch, true);

    // The response the model speaks back carries the grounded answer + its citations.
    expect(response.answer_is_grounded).toBe(true);
    expect(response.answer_text).toBe(GROUNDED_ANSWER.answer_text);
    expect(response.answer_citations).toEqual(GROUNDED_ANSWER.answer_citations);
    expect((response.answer_citations as unknown[]).length).toBeGreaterThan(0);
  });
});

describe("buildAskAboutStoryHandler — off-source refusal (zero-hallucination)", () => {
  it("relays the refusal verbatim and emits NO fabricated answer or citations", async () => {
    askQuestionMock.mockResolvedValue(REFUSAL_ANSWER);
    const handler = buildAskAboutStoryHandler(STORY_ID);

    const response = await handler(makeToolCall({ question_text: "Who wins the next election?" }));

    // WHY (Rule 9 / Decision #5): a refusal MUST pass through untouched. This test
    // FAILS if a future change makes the handler synthesize an answer or citation
    // when the server refused — the whole point of the grounded contract.
    expect(response.answer_is_grounded).toBe(false);
    expect(response.answer_text).toBe(REFUSAL_ANSWER.answer_text);
    expect(response.answer_citations).toEqual([]);
    expect(response.answer_text).not.toBe(GROUNDED_ANSWER.answer_text);
  });
});

describe("buildAskAboutStoryHandler — malformed tool call (Rule 12)", () => {
  it("treats a missing/non-string question_text as empty and still routes through the grounded path", async () => {
    // WHY: a malformed model call must degrade to a refusable empty question, never
    // throw and break the conversation. The server (mocked here as a refusal) stays
    // the trust authority on an empty question.
    askQuestionMock.mockResolvedValue(REFUSAL_ANSWER);
    const handler = buildAskAboutStoryHandler(STORY_ID);

    const missingArg = await handler(makeToolCall({}));
    expect(askQuestionMock).toHaveBeenLastCalledWith(STORY_ID, "", [], fetch, true);
    expect(missingArg.answer_is_grounded).toBe(false);

    const nonStringArg = await handler(makeToolCall({ question_text: 42 }));
    expect(askQuestionMock).toHaveBeenLastCalledWith(STORY_ID, "", [], fetch, true);
    expect(nonStringArg.answer_is_grounded).toBe(false);
  });
});
