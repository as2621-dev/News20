/**
 * Grounded Q&A client (Phase 2b SP3) for the swipe-right Detail view of the
 * audio-first karaoke reel (blip / "News20").
 *
 * Calls the SP2 worker endpoint `POST /api/story/{story_id}/question` and returns
 * the typed {@link QuestionAnswer}. The static-export SPA cannot hold the LLM key
 * or run verification client-side, so this is a thin HTTP client over the remote
 * Python worker (`reference/prototype-port-map.md` §6) reached over HTTPS.
 *
 * **The trust contract is server-side (Rule 9).** This client does NOT decide
 * grounded-vs-refused — the worker's verification stage does. The endpoint ALWAYS
 * returns HTTP 200 with a `QuestionAnswer`; `answer_is_grounded` is the single
 * source of truth the UI maps to the two visual states (`.qa-bubble-a` + chips vs
 * `.qa-refusal`). This module's only job is to fetch + validate the shape, and —
 * on any transport/parse failure — return a SAFE refusal (never a fabricated
 * answer, never a thrown error that breaks the conversation), matching the
 * worker's own HTTP-200-graceful-fallback posture.
 *
 * @example
 * const answer = await askQuestion("s1", "Why does Hormuz matter?");
 * answer.answer_is_grounded; // → true (grounded bubble + chips) | false (refusal card)
 */

import { logger } from "@/lib/logger";
import type { AnswerCitation, QaConversationTurn, QuestionAnswer } from "@/types/qa";

/**
 * The maximum number of prior thread turns shipped with a follow-up. Matches
 * the worker's prompt budget (it renders at most the last 6 turns) and keeps
 * the request body bounded.
 */
export const MAX_CONVERSATION_TURNS_SENT = 6;

/**
 * The fixed client-side refusal copy used ONLY when the request itself fails
 * (network down, non-200, malformed body) — i.e. when the worker's own refusal
 * copy never reached us. Mirrors the worker's intent: tell the user we can't
 * answer from the source right now, never invent one. A failed request degrades
 * to a refusal, never to a fabricated grounded answer (Rule 9 / Rule 12).
 */
export const CLIENT_REFUSAL_ANSWER_TEXT =
  "I can only answer from this story's source — that isn't available right now. " +
  "Try a suggested question, or ask again in a moment.";

/**
 * Build a SAFE refusal {@link QuestionAnswer}. Used on every client-side failure
 * path so a broken request can never surface as a grounded answer.
 *
 * @returns A refusal payload: not grounded, fixed copy, zero citations.
 */
function buildClientRefusal(): QuestionAnswer {
  return {
    answer_text: CLIENT_REFUSAL_ANSWER_TEXT,
    answer_citations: [],
    answer_is_grounded: false,
  };
}

/**
 * Resolve the Q&A worker base URL. Empty string (the default) makes the request a
 * same-origin relative path (`/api/story/...`), which is the right behaviour when
 * a reverse-proxy/dev rewrite fronts the worker; set `NEXT_PUBLIC_QA_API_BASE_URL`
 * to the deployed worker origin (e.g. `https://worker.example.com`) for the
 * Capacitor static build, which has no same-origin server.
 *
 * @returns The base URL with any trailing slash stripped, or `""` for same-origin.
 */
function getQaApiBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_QA_API_BASE_URL ?? "";
  return base.replace(/\/+$/, "");
}

/**
 * Narrow an unknown JSON body to a valid {@link QuestionAnswer}.
 *
 * The server is the trust authority, but a malformed body must never be rendered
 * as a grounded answer — so we validate the shape and, critically, enforce the
 * refusal invariant: `answer_is_grounded === false` ⇒ no citations are surfaced
 * (Rule 9; the refusal card never carries chips).
 *
 * @param body - The parsed JSON response body (unknown shape).
 * @returns A validated {@link QuestionAnswer}, or `null` when the shape is invalid.
 */
function parseQuestionAnswer(body: unknown): QuestionAnswer | null {
  if (typeof body !== "object" || body === null) {
    return null;
  }
  const candidate = body as Record<string, unknown>;
  if (
    typeof candidate.answer_text !== "string" ||
    typeof candidate.answer_is_grounded !== "boolean" ||
    !Array.isArray(candidate.answer_citations)
  ) {
    return null;
  }

  const answer_is_grounded = candidate.answer_is_grounded;
  // Reason: the refusal invariant (Rule 9) — an un-grounded answer NEVER carries
  // citation chips, regardless of what the body contains. Grounded answers map
  // each well-formed citation; malformed citation entries are dropped, not faked.
  const answer_citations: AnswerCitation[] = answer_is_grounded
    ? candidate.answer_citations.filter(isAnswerCitation)
    : [];

  return {
    answer_text: candidate.answer_text,
    answer_citations,
    answer_is_grounded,
  };
}

/**
 * Type guard for one {@link AnswerCitation} entry (all four string fields present).
 *
 * @param value - A raw element of `answer_citations`.
 * @returns `true` when `value` is a well-formed citation.
 */
function isAnswerCitation(value: unknown): value is AnswerCitation {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.source_url === "string" &&
    typeof candidate.source_quote === "string" &&
    typeof candidate.source_outlet_name === "string" &&
    typeof candidate.passage_id === "string"
  );
}

/**
 * Ask a grounded question about one story and return the typed answer.
 *
 * POSTs `{ question_text }` to `POST /api/story/{story_id}/question` and maps the
 * HTTP 200 body to a {@link QuestionAnswer}. Every failure mode (network error,
 * non-200, malformed JSON) degrades to a SAFE refusal — the conversation never
 * breaks and a broken request never surfaces as a grounded answer (Rule 12).
 *
 * @param story_id - The `stories.story_id` slug (the reel `Story.digest_id`).
 * @param question_text - The user's question.
 * @param conversation_turns - Recent prior thread turns (most-recent-last) for
 *   follow-up resolution; only the last {@link MAX_CONVERSATION_TURNS_SENT} are
 *   sent, and the field is omitted entirely on a first question.
 * @param fetchImpl - Injectable fetch (defaults to the global `fetch`; tests pass a mock).
 * @returns The grounded answer, or a refusal on any failure.
 *
 * @example
 * const answer = await askQuestion("s1", "Why does Hormuz matter?");
 * if (answer.answer_is_grounded) {
 *   // render .qa-bubble-a + one .cite-chip per answer.answer_citations
 * } else {
 *   // render the ⌀ CAN'T ANSWER FROM SOURCE refusal card
 * }
 */
export async function askQuestion(
  story_id: string,
  question_text: string,
  conversation_turns: QaConversationTurn[] = [],
  fetchImpl: typeof fetch = fetch,
): Promise<QuestionAnswer> {
  const endpoint = `${getQaApiBaseUrl()}/api/story/${encodeURIComponent(story_id)}/question`;
  const trimmedTurns = conversation_turns.slice(-MAX_CONVERSATION_TURNS_SENT);
  logger.info("ask_question_started", {
    story_id,
    question_length: question_text.length,
    conversation_turn_count: trimmedTurns.length,
  });

  try {
    const response = await fetchImpl(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question_text,
        ...(trimmedTurns.length > 0 ? { conversation_turns: trimmedTurns } : {}),
      }),
    });

    if (!response.ok) {
      logger.error("ask_question_non_200", {
        story_id,
        status: response.status,
        fix_suggestion:
          "Confirm the Q&A worker is deployed and NEXT_PUBLIC_QA_API_BASE_URL points at it; the worker should return HTTP 200 even on failure.",
      });
      return buildClientRefusal();
    }

    const body: unknown = await response.json();
    const answer = parseQuestionAnswer(body);
    if (answer === null) {
      logger.error("ask_question_malformed_body", {
        story_id,
        fix_suggestion: "Endpoint must return { answer_text, answer_citations[], answer_is_grounded }.",
      });
      return buildClientRefusal();
    }

    logger.info("ask_question_completed", {
      story_id,
      answer_is_grounded: answer.answer_is_grounded,
      citation_count: answer.answer_citations.length,
    });
    return answer;
  } catch (error: unknown) {
    logger.error("ask_question_failed", {
      story_id,
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion: "Check network connectivity and that the Q&A worker endpoint is reachable over HTTPS.",
    });
    return buildClientRefusal();
  }
}
