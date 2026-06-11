/**
 * Grounded Q&A contract for the Detail view interrogation (Phase 2b / M2).
 *
 * **Why this file exists (NOT in `src/types/detail.ts`).** Phase 2c owns
 * `src/types/detail.ts`; the Q&A shapes are a separate, frozen seam consumed by
 * `src/lib/qa/askQuestion.ts` + the `Qa*` Detail components, so they live here.
 *
 * Mirrors the SP2 endpoint contract verbatim
 * (`agents/qa/models.py::{QuestionAnswer, AnswerCitation}`, reported in
 * `.agents/execution-reports/phase-2b-m2-grounded-interrogation-sub-2.md`):
 * `POST /api/story/{story_id}/question` ALWAYS returns HTTP 200 with a
 * {@link QuestionAnswer}. `answer_is_grounded === false` → the fixed refusal copy
 * + an empty `answer_citations` array (the `⌀ CAN'T ANSWER FROM SOURCE` state).
 *
 * SP2 extends the leaner `api-contracts.md` `AnswerCitation` ({@link source_url},
 * {@link source_quote}) with {@link source_outlet_name} (the chip label) and
 * {@link passage_id} (provenance) — this file follows the SP2 runtime shape, the
 * source of truth for what the endpoint actually returns. Verbose, entity-prefixed
 * names per CLAUDE.md.
 */

/**
 * One citation chip backing a grounded answer — an element of
 * {@link QuestionAnswer.answer_citations}.
 *
 * Maps `agents.qa.models.AnswerCitation`. Every grounded answer carries ≥1; a
 * refusal carries none. Each chip traces to a passage of the active story's
 * grounding corpus and the `story_sources` outlet that backs it.
 */
export interface AnswerCitation {
  /** The cited source article URL (`AnswerCitation.source_url`). */
  source_url: string;
  /** The supporting quote/snippet from the source (`AnswerCitation.source_quote`). */
  source_quote: string;
  /** Outlet name shown as the chip label, e.g. `"Reuters"` (`AnswerCitation.source_outlet_name`). */
  source_outlet_name: string;
  /** Stable corpus passage id the answer used, e.g. `"detail_chunk:0"` (`AnswerCitation.passage_id`). */
  passage_id: string;
}

/**
 * The grounded-Q&A answer payload — the HTTP 200 body of
 * `POST /api/story/{story_id}/question`.
 *
 * Maps `agents.qa.models.QuestionAnswer`. The trust guarantee (Decision #5,
 * Rule 9): when {@link answer_is_grounded} is `false`, {@link answer_text} is the
 * fixed refusal copy and {@link answer_citations} is empty — the UI renders the
 * `⌀ CAN'T ANSWER FROM SOURCE` refusal card and NEVER an answer bubble.
 */
export interface QuestionAnswer {
  /** The grounded answer text, or — when not grounded — the fixed refusal copy. */
  answer_text: string;
  /** Citation chips backing a grounded answer; empty `[]` for a refusal. */
  answer_citations: AnswerCitation[];
  /** `true` → render the grounded bubble + chips; `false` → render the refusal card only. */
  answer_is_grounded: boolean;
}

/**
 * One prior turn of the typed Q&A thread, sent with a follow-up so the worker
 * can resolve pronouns/references (`agents.qa.models.ConversationTurn`).
 *
 * Stateless-server multi-turn: the CLIENT holds the thread and ships the recent
 * turns with each request; the worker never stores a session.
 */
export interface QaConversationTurn {
  /** Who spoke: the reader (`"user"`) or the grounded answerer (`"model"`). */
  role: "user" | "model";
  /** The turn's text — the question, or the answer/refusal copy. */
  text: string;
}

/**
 * One completed question→answer exchange in the typed Q&A thread. The unit the
 * AskSheetType thread renders and `qaHistoryStore` persists per story.
 */
export interface CompletedQaTurn {
  /** The reader's question as submitted (trimmed). */
  question_text: string;
  /** The worker's answer payload (grounded answer or refusal). */
  answer: QuestionAnswer;
}

/**
 * The request body for `POST /api/story/{story_id}/question`.
 *
 * Maps `agents.worker.main.QuestionRequest`. `conversation_id` is reserved for
 * M3 multi-turn memory and unused (left optional so the shape stays
 * forward-compatible); `conversation_turns` is the shipped stateless multi-turn
 * mechanism (recent thread turns, most-recent-last).
 */
export interface QuestionRequest {
  /** The user's question text (`QuestionRequest.question_text`). */
  question_text: string;
  /** Reserved for M3 multi-turn memory; omitted in M2 (`QuestionRequest.conversation_id`). */
  conversation_id?: string;
  /** Recent prior thread turns for follow-up resolution; omitted on a first question. */
  conversation_turns?: QaConversationTurn[];
}
