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

import { buildInNewsSystemInstruction } from "@/components/voice/VoiceConversation";
// Imported AFTER the mock so the tool picks up the spied client.
import { askQuestion } from "@/lib/qa/askQuestion";
import {
  ASK_ABOUT_STORY_TOOL_NAME,
  askAboutStoryDeclaration,
  buildAskAboutStoryHandler,
  STORY_QA_TOOL_GROUNDING_CLAUSE,
} from "@/lib/voice/storyQaTool";
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
  it("forbids answering without calling the tool", () => {
    // WHY (Rule 9): the clause is what mechanically pushes the model onto the
    // grounded path. The assembled instruction must actually carry it (not just the
    // softer base persona) or the model could answer from its own knowledge.
    const instruction = buildInNewsSystemInstruction(
      "Why does Hormuz matter?",
      STORY_ID,
      STORY_QA_TOOL_GROUNDING_CLAUSE,
    );

    expect(instruction).toContain(STORY_QA_TOOL_GROUNDING_CLAUSE);
    expect(instruction).toContain("ask_about_story");
    expect(instruction).toMatch(/MUST NOT answer/);
    expect(instruction).toMatch(/answer_is_grounded false/);
  });
});

describe("buildAskAboutStoryHandler — on-topic grounded round-trip", () => {
  it("calls askQuestion once for the story and relays the grounded answer + citations", async () => {
    askQuestionMock.mockResolvedValue(GROUNDED_ANSWER);
    const handler = buildAskAboutStoryHandler(STORY_ID);

    const response = await handler(makeToolCall({ question_text: "Why does Hormuz matter?" }));

    // Round-trip: the grounded path is hit exactly once, scoped to THIS story.
    expect(askQuestionMock).toHaveBeenCalledTimes(1);
    expect(askQuestionMock).toHaveBeenCalledWith(STORY_ID, "Why does Hormuz matter?");

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
    expect(askQuestionMock).toHaveBeenLastCalledWith(STORY_ID, "");
    expect(missingArg.answer_is_grounded).toBe(false);

    const nonStringArg = await handler(makeToolCall({ question_text: 42 }));
    expect(askQuestionMock).toHaveBeenLastCalledWith(STORY_ID, "");
    expect(nonStringArg.answer_is_grounded).toBe(false);
  });
});
