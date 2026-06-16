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
    "Use ONLY for questions the story context does not cover (a related fact the story " +
    "doesn't state). Fetches a web-searched answer with citations, or a refusal/off-topic " +
    "pushback when the question can't be answered or is unrelated to the story. Do NOT call " +
    "this for anything already answerable from the STORY CONTEXT — answer those directly.",
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
 * The system-instruction clause for the corpus-first / tool-on-miss / web-only path.
 *
 * Appended LAST to the corpus-in-context instruction via
 * `buildInNewsSystemInstructionWithCorpus`'s `tool_grounding_clause` arg (the
 * latency-fix hybrid). It mechanizes the new contract: the full story already lives
 * in the model's STORY CONTEXT, so it answers corpus-answerable questions DIRECTLY;
 * it calls {@link askAboutStoryDeclaration} ONLY when the answer is NOT in that
 * context (a related fact the story doesn't state). Before calling the tool it says
 * one short filler line ("let me check that") to mask the round-trip; it then speaks
 * the tool's `answer_text` verbatim, and — when `answer_is_grounded` is false —
 * delivers it as a brief refusal/pushback, adding nothing of its own.
 */
export const STORY_QA_TOOL_GROUNDING_CLAUSE =
  "You have the full story in your STORY CONTEXT. Answer questions from it directly — that is the fast path. " +
  `Call ${ASK_ABOUT_STORY_TOOL_NAME}(question_text) ONLY when the answer is NOT in your STORY CONTEXT ` +
  "(for example, a related fact the story doesn't state). Before calling it, say ONE short filler line like " +
  '"let me check that." Then speak the tool\'s answer_text verbatim. ' +
  "When the tool returns answer_is_grounded false, deliver its answer_text as a brief refusal or pushback and DO NOT guess, invent, or add any facts of your own.";

/**
 * The legacy tool-forced clause — the PRE-hybrid behavior, kept for the
 * `NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT` flag's OFF path (clean A/B, SP4).
 *
 * This is the original `STORY_QA_TOOL_GROUNDING_CLAUSE` wording (recovered
 * byte-for-byte from git HEAD) BEFORE it was rewritten to corpus-first semantics.
 * It FORBIDS the model answering any factual question from its own knowledge and
 * forces a tool call for EVERY such question — there is no injected STORY CONTEXT
 * on the flag-OFF path, so the model has no corpus to answer from and must route
 * everything through {@link buildAskAboutStoryHandler}. Flag ON uses the new
 * corpus-first {@link STORY_QA_TOOL_GROUNDING_CLAUSE}; flag OFF uses this.
 */
export const LEGACY_TOOL_FORCED_CLAUSE =
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
      // Reason: the corpus already failed AT THE MODEL (this tool fires only on a
      // STORY CONTEXT miss), so route to the server's web-only path — skipping the
      // wasted corpus answer+verify. web_only is the 5th positional arg; pass the
      // defaults for conversation_turns + fetchImpl unchanged.
      const answer = await askQuestion(story_id, question_text, [], fetch, true);
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
