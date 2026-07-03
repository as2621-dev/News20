/**
 * Interview turn client (FSR slice #4) — the SPA's thin HTTP client over the
 * worker's `POST /api/interview/turn` (slice #1).
 *
 * The worker is stateless: the whole conversation so far travels with every
 * request (spec §2). This module ships the accumulated {@link InterviewExchange}s
 * with the user's OWN Supabase session access token as `Authorization: Bearer …`
 * — the SAME JWT seam as `assembleFirstRunFeed` / `/feed/assemble-mine` (the worker
 * derives the user from the verified token alone; no shared secret).
 *
 * **Never a dead-end (spec §2, Rule 12).** The turn endpoint is graceful-failure:
 * it returns HTTP 200 with a typed `retry` body on its own failures, and this
 * client SYNTHESIZES a `retry` turn on any transport/parse failure (no session,
 * network down, non-200, malformed body). It NEVER throws into the chat UI — the
 * component renders an in-place retry from a `retry` turn, so a failed request is
 * always recoverable, never a crash or a blank screen.
 */

import { logger } from "@/lib/logger";
import { getCurrentSession } from "@/lib/supabase/auth";
import type {
  InterviewBubble,
  InterviewBubbleKind,
  InterviewExchange,
  InterviewTurn,
  TerminalMicroInterest,
} from "@/types/interview";

/**
 * Resolve the worker base URL. Empty string (the default) makes the request a
 * same-origin relative path (`/api/interview/turn`), correct when a reverse-proxy
 * fronts the worker; set `NEXT_PUBLIC_QA_API_BASE_URL` to the deployed worker
 * origin for the Capacitor static build (no same-origin server). Reuses the SAME
 * env var every other client→worker call reads (askQuestion, assembleFirstRunFeed).
 *
 * @returns The base URL with any trailing slash stripped, or `""` for same-origin.
 */
function getWorkerBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_QA_API_BASE_URL ?? "";
  return base.replace(/\/+$/, "");
}

/**
 * Build a synthetic `retry` turn for a client-side failure the worker never got to
 * answer. Keeps the chat UI on a recoverable path (spec §2) rather than throwing.
 *
 * @param turnIndex - The turn the client was fetching (so the UI re-asks the same one).
 * @param errorCode - A stable machine code (e.g. `"transport_error"`).
 * @param errorMessage - Human copy shown above the retry affordance.
 * @returns A typed {@link InterviewTurn} of kind `retry`.
 */
function buildRetryTurn(turnIndex: number, errorCode: string, errorMessage: string): InterviewTurn {
  return {
    response_kind: "retry",
    turn_index: turnIndex,
    error_code: errorCode,
    error_message: errorMessage,
    retry_hint: "Tap retry to try again.",
  };
}

/** Narrow one raw bubble entry to an {@link InterviewBubble}, defaulting an unknown kind to `option`. */
function parseBubble(raw: unknown): InterviewBubble | null {
  if (typeof raw !== "object" || raw === null) {
    return null;
  }
  const candidate = raw as Record<string, unknown>;
  if (typeof candidate.bubble_label !== "string") {
    return null;
  }
  const kind = candidate.bubble_kind;
  const bubble_kind: InterviewBubbleKind =
    kind === "skip" || kind === "type_your_own" || kind === "option" ? kind : "option";
  return { bubble_label: candidate.bubble_label, bubble_kind };
}

/** Narrow one raw micro-interest to a {@link TerminalMicroInterest} (the persistence backstop re-validates deeply). */
function parseMicroInterest(raw: unknown): TerminalMicroInterest | null {
  if (typeof raw !== "object" || raw === null) {
    return null;
  }
  const candidate = raw as Record<string, unknown>;
  if (
    typeof candidate.display_label !== "string" ||
    typeof candidate.canonical_slug !== "string" ||
    !Array.isArray(candidate.ladder) ||
    !Array.isArray(candidate.search_anchor_terms)
  ) {
    return null;
  }
  return {
    display_label: candidate.display_label,
    canonical_slug: candidate.canonical_slug,
    ladder: candidate.ladder.map((rung) => String(rung)),
    search_anchor_terms: candidate.search_anchor_terms.map((term) => String(term)),
    strict: candidate.strict === true,
  };
}

/**
 * Narrow an unknown worker JSON body to a typed {@link InterviewTurn}, discriminated
 * on `response_kind`. A body that doesn't match any known shape yields `null` so the
 * caller degrades to a synthetic retry (never renders a malformed turn).
 *
 * @param body - The parsed JSON response body (unknown shape).
 * @returns A typed turn, or `null` when the shape is unrecognized.
 */
function parseTurn(body: unknown): InterviewTurn | null {
  if (typeof body !== "object" || body === null) {
    return null;
  }
  const candidate = body as Record<string, unknown>;
  const turnIndex = typeof candidate.turn_index === "number" ? candidate.turn_index : 0;

  switch (candidate.response_kind) {
    case "question": {
      if (typeof candidate.question_text !== "string" || !Array.isArray(candidate.bubbles)) {
        return null;
      }
      const bubbles = candidate.bubbles.map(parseBubble).filter((bubble): bubble is InterviewBubble => bubble !== null);
      return {
        response_kind: "question",
        turn_index: turnIndex,
        question_text: candidate.question_text,
        bubbles,
      };
    }
    case "terminal": {
      const rawInterests = Array.isArray(candidate.micro_interests) ? candidate.micro_interests : [];
      return {
        response_kind: "terminal",
        turn_index: turnIndex,
        micro_interests: rawInterests
          .map(parseMicroInterest)
          .filter((interest): interest is TerminalMicroInterest => interest !== null),
        roots_only_fallback: candidate.roots_only_fallback === true,
      };
    }
    case "retry": {
      return {
        response_kind: "retry",
        turn_index: turnIndex,
        error_code: typeof candidate.error_code === "string" ? candidate.error_code : null,
        error_message: typeof candidate.error_message === "string" ? candidate.error_message : null,
        retry_hint: typeof candidate.retry_hint === "string" ? candidate.retry_hint : null,
      };
    }
    default:
      return null;
  }
}

/**
 * Fetch the next interview turn for the given conversation state.
 *
 * POSTs `{ conversation_state }` (spec §2) with the user's bearer JWT and maps the
 * HTTP 200 body to a typed {@link InterviewTurn}. Every failure mode (no session,
 * network error, non-200, malformed JSON) degrades to a SYNTHETIC `retry` turn —
 * the chat UI stays on a recoverable path and NEVER crashes (spec §2 / Rule 12).
 *
 * @param conversationState - The ordered prior exchanges (empty on the first turn).
 * @param fetchImpl - Injectable fetch (defaults to the global `fetch`; tests pass a mock).
 * @returns The next turn (question / terminal / retry) — always resolves, never rejects.
 *
 * @example
 * const turn = await fetchInterviewTurn([]);
 * // turn.response_kind === "question" → render turn.bubbles
 */
export async function fetchInterviewTurn(
  conversationState: InterviewExchange[],
  fetchImpl: typeof fetch = fetch,
): Promise<InterviewTurn> {
  const nextTurnIndex = conversationState.length;
  const session = await getCurrentSession();
  const accessToken = session?.access_token;
  if (!accessToken) {
    // Reason: the worker requires the user's own JWT (spec §2). Without a session we
    // cannot scope the turn — surface a recoverable retry rather than throwing.
    logger.warn("interview_turn_no_session", {
      turn_index: nextTurnIndex,
      fix_suggestion: "A Supabase session must exist before an interview turn; the chat shows a retry affordance.",
    });
    return buildRetryTurn(nextTurnIndex, "no_session", "Your session expired — sign in again to continue.");
  }

  const endpoint = `${getWorkerBaseUrl()}/api/interview/turn`;
  logger.info("interview_turn_started", { turn_index: nextTurnIndex, exchange_count: conversationState.length });

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Reason: the worker derives user_id from THIS verified token — never logged.
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ conversation_state: conversationState }),
    });
  } catch (error: unknown) {
    logger.error("interview_turn_transport_failed", {
      turn_index: nextTurnIndex,
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion: "Check network connectivity and that NEXT_PUBLIC_QA_API_BASE_URL points at the reachable worker.",
    });
    return buildRetryTurn(nextTurnIndex, "transport_error", "We couldn't reach the interview just now.");
  }

  if (!response.ok) {
    logger.error("interview_turn_non_200", {
      turn_index: nextTurnIndex,
      status: response.status,
      fix_suggestion: "Worker should return HTTP 200 even on failure (typed retry body); 401 = expired session.",
    });
    return buildRetryTurn(nextTurnIndex, "http_error", "The interview hit a snag.");
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch (error: unknown) {
    logger.error("interview_turn_parse_failed", {
      turn_index: nextTurnIndex,
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion: "Endpoint must return a JSON InterviewTurnResponse body.",
    });
    return buildRetryTurn(nextTurnIndex, "parse_error", "The interview sent back something we couldn't read.");
  }

  const turn = parseTurn(body);
  if (turn === null) {
    logger.error("interview_turn_malformed_body", {
      turn_index: nextTurnIndex,
      fix_suggestion: "Body must be a question / terminal / retry InterviewTurnResponse (spec §2).",
    });
    return buildRetryTurn(nextTurnIndex, "malformed_body", "The interview sent back something unexpected.");
  }

  logger.info("interview_turn_completed", { turn_index: nextTurnIndex, response_kind: turn.response_kind });
  return turn;
}
