/**
 * `storyQaTool` — the Gemini Live `ask_about_story` function-call wiring for
 * in-news Voice mode (phase-3b SP3).
 *
 * **What this is.** The bridge between the Live transport ({@link useGeminiLive})
 * and M2's shipped in-context grounded-Q&A path ({@link askQuestion} →
 * `POST /api/story/{story_id}/question`). It exposes three pieces the Voice layer
 * wires together:
 *   1. {@link askAboutStoryDeclaration} — the function the model may call;
 *   2. {@link STORY_QA_TOOL_GROUNDING_CLAUSE} — the system-instruction clause that
 *      FORBIDS the model answering any factual question without calling the tool;
 *   3. {@link buildAskAboutStoryHandler} — the `onToolCall` handler factory that
 *      runs the grounded round-trip for the active story and returns the response
 *      the model speaks back.
 *
 * **No retriever here (Pinecone/RAG dropped 2026-05-31).** The grounding lives
 * server-side in the existing endpoint; this module only routes the Live tool call
 * to it. The trust contract (Decision #5 / Rule 9) is enforced by the server's
 * verification stage and surfaced verbatim: when `answer_is_grounded === false`
 * the handler relays the refusal copy and empty citations unchanged — it NEVER
 * fabricates an answer or a citation.
 *
 * @example
 * const handler = buildAskAboutStoryHandler(story.digest_id);
 * // <VoiceConversation toolsSlot={[askAboutStoryDeclaration]} onToolCallSlot={handler}
 * //   tool_grounding_clause={STORY_QA_TOOL_GROUNDING_CLAUSE} />
 */

import { logger } from "@/lib/logger";
import { askQuestion } from "@/lib/qa/askQuestion";
import type { GeminiToolCall, GeminiToolDeclaration } from "@/lib/voice/useGeminiLive";

/**
 * The function name the model emits in `toolCall.functionCalls[].name`. Kept as a
 * named constant so the declaration and the handler's guard agree on one spelling.
 */
export const ASK_ABOUT_STORY_TOOL_NAME = "ask_about_story";

/**
 * The Gemini Live function declaration for the grounded-answer tool.
 *
 * A single required string `question_text`. The description steers the model to
 * route every factual question about the active story through this tool (which is
 * the only path to the story's verified sources); the hard "you MUST call it"
 * enforcement lives in {@link STORY_QA_TOOL_GROUNDING_CLAUSE} on the system
 * instruction.
 */
export const askAboutStoryDeclaration: GeminiToolDeclaration = {
  name: ASK_ABOUT_STORY_TOOL_NAME,
  description:
    "Answer the user's question about THIS news story (or anything related to it) using its " +
    "verified sources, with a web-search fallback for related questions the sources don't cover. " +
    "Call this for every factual question; it returns a sourced answer with citations, or a " +
    "refusal/pushback when the question can't be answered or is unrelated to the story.",
  parameters: {
    type: "object",
    properties: {
      question_text: {
        type: "string",
        description: "The user's question about this story, in their own words.",
      },
    },
    required: ["question_text"],
  },
};

/**
 * The system-instruction clause that FORBIDS answering without the tool.
 *
 * Appended to the in-news instruction via `buildInNewsSystemInstruction`'s
 * `tool_grounding_clause` arg. It mechanizes Decision #5 / Rule 9: the model must
 * call {@link askAboutStoryDeclaration} for every factual question, must speak back
 * the tool's `answer_text` (and only that), and — when `answer_is_grounded` is
 * false — must deliver that refusal text verbatim, never guessing or adding facts.
 */
export const STORY_QA_TOOL_GROUNDING_CLAUSE =
  `You have one tool, ${ASK_ABOUT_STORY_TOOL_NAME}(question_text). ` +
  "You MUST NOT answer any factual question about this story — or related to it — from your own knowledge. " +
  `For every such question, call ${ASK_ABOUT_STORY_TOOL_NAME} with the user's question, then speak ONLY the tool's answer_text. ` +
  "When the tool returns answer_is_grounded false, say its answer_text verbatim as a brief refusal and DO NOT guess, invent, or add any facts of your own.";

/**
 * The response object the handler returns to the model. A `Record<string, unknown>`
 * subset of {@link QuestionAnswer} — the fields the model needs to speak the answer
 * (or the refusal) and surface its citations. Declared explicitly so the contract
 * the round-trip relays is legible at the call site.
 */
interface AskAboutStoryToolResponse extends Record<string, unknown> {
  /** The grounded answer text, or — when not grounded — the fixed refusal copy. */
  answer_text: string;
  /** `true` → grounded answer + citations; `false` → refusal, no fabrication. */
  answer_is_grounded: boolean;
  /** Citation chips backing a grounded answer; empty `[]` for a refusal. */
  answer_citations: unknown[];
}

/**
 * Extract a usable `question_text` from a tool call's args.
 *
 * Guards the boundary: a missing or non-string arg is treated as an empty question
 * (which the grounded endpoint will refuse) rather than throwing — a malformed tool
 * call must degrade to a refusal, never crash the conversation (Rule 12).
 *
 * @param args - The raw `toolCall.args` the model supplied.
 * @returns The trimmed question string, or `""` when absent/ill-typed.
 */
function extractQuestionText(args: Record<string, unknown>): string {
  const raw = args.question_text;
  return typeof raw === "string" ? raw : "";
}

/**
 * Build the `onToolCall` handler for the active story's grounded Q&A round-trip.
 *
 * Returns a handler that extracts `question_text` from the model's call, runs the
 * shipped in-context grounded-Q&A path ({@link askQuestion}, scoped to `story_id`),
 * and returns the `{ answer_text, answer_is_grounded, answer_citations }` response
 * the model speaks back. `askQuestion` already degrades every failure mode to a
 * SAFE refusal, so this handler never throws and never fabricates: it relays the
 * server's verdict unchanged (Decision #5 / Rule 9).
 *
 * @param story_id - The active reel story's id (`Story.digest_id`) to scope the Q&A.
 * @returns An async `onToolCall` handler for {@link useGeminiLive}.
 *
 * @example
 * const handler = buildAskAboutStoryHandler("s1");
 * const response = await handler({ id: "c1", name: "ask_about_story", args: { question_text: "Why does Hormuz matter?" } });
 * response.answer_is_grounded; // → true (grounded) | false (refusal)
 */
export function buildAskAboutStoryHandler(
  story_id: string,
): (toolCall: GeminiToolCall) => Promise<AskAboutStoryToolResponse> {
  return async (toolCall: GeminiToolCall): Promise<AskAboutStoryToolResponse> => {
    const question_text = extractQuestionText(toolCall.args);
    logger.info("ask_about_story_tool_called", {
      story_id,
      tool_call_id: toolCall.id,
      question_length: question_text.length,
    });

    try {
      const answer = await askQuestion(story_id, question_text);
      logger.info("ask_about_story_tool_completed", {
        story_id,
        tool_call_id: toolCall.id,
        answer_is_grounded: answer.answer_is_grounded,
        citation_count: answer.answer_citations.length,
      });
      // Reason: relay the server's verdict verbatim. When answer_is_grounded is
      // false this is the refusal copy + empty citations — never substitute or
      // invent content (Decision #5 / Rule 9). askQuestion is the trust boundary.
      return {
        answer_text: answer.answer_text,
        answer_is_grounded: answer.answer_is_grounded,
        answer_citations: answer.answer_citations,
      };
    } catch (error: unknown) {
      // Reason: askQuestion already returns a safe refusal on every failure mode, so
      // reaching here is unexpected — still degrade to a refusal, never a guess (Rule 12).
      logger.error("ask_about_story_tool_failed", {
        story_id,
        tool_call_id: toolCall.id,
        error_message: error instanceof Error ? error.message : "Unknown error",
        fix_suggestion:
          "askQuestion should never throw (it returns a safe refusal); inspect the @/lib/qa/askQuestion import and the tool-call args shape.",
      });
      return {
        answer_text: "I can't answer that from this story's sources right now.",
        answer_is_grounded: false,
        answer_citations: [],
      };
    }
  };
}
