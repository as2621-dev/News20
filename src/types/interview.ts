/**
 * Interview onboarding — the TERMINAL payload contract the SPA consumes.
 *
 * This is the TypeScript twin of the worker's validated terminal schema
 * (`agents/interview/models.py::MicroInterest` / the `terminal` branch of
 * `InterviewTurnResponse`) — see `reference/interview-onboarding-spec.md` §4.
 * The worker has ALREADY validated every field (root-anchoring, >=2 anchor terms,
 * traceability, dedup) before it reaches the client; the persistence layer
 * ({@link import("@/lib/interviewProfile").persistInterviewInterests}) re-validates
 * as a backstop before any write (never trust a payload at a privileged boundary).
 *
 * The terminal shape plus the question / retry turn shapes (the chat-UI slice's
 * concern, added by slice #4) all live here — the client renders whichever the
 * worker returns, keyed on `response_kind`.
 */

/** One validated terminal micro-interest (spec §4). */
export interface TerminalMicroInterest {
  /** The user's own vocabulary — drives feed section headers, stored per-user on the profile row. */
  display_label: string;
  /** Root-anchored dotted convergence key, e.g. `sport.cricket.ipl.auctions`. */
  canonical_slug: string;
  /**
   * Ordered PARENT chain derived from the slug (excludes the leaf), e.g.
   * `["sport", "sport.cricket", "sport.cricket.ipl"]`. Empty for a root-level pick.
   * This IS the fallback ladder the assembler climbs one level at a time.
   */
  ladder: string[];
  /** >= 2 concrete anchor terms for the batched BigQuery query (feeds `interest_search_query`). */
  search_anchor_terms: string[];
  /** When true the feed never climbs the ladder for this interest (maps to `profile_is_strict`). */
  strict: boolean;
}

/** One SKIP-TUNE mute term (spec §6). Persisted to `user_mute_terms`, hard-filtered at assembly. */
export interface TerminalMuteTerm {
  /** The root category slug the mute belongs to (e.g. `sport`). */
  mute_category: string;
  /** The user-vocabulary term to mute (a tapped option or typed text). */
  mute_term: string;
}

/** One ANGLE-TUNE preference (spec §6) — which lens the user reads a category through. */
export interface TerminalAnglePreference {
  /** The root category slug the angle belongs to. */
  angle_category: string;
  /** The user-vocabulary lens label (a tapped option or typed text). */
  angle_label: string;
}

/** The terminal interview payload the client confirms, then persists. */
export interface InterviewTerminalPayload {
  /** The extracted micro-interests. Empty when the user skipped through with no roots lit. */
  micro_interests: TerminalMicroInterest[];
  /**
   * True when the interview terminated on the skip path (feed falls back to roots-only).
   * Informational for the client; persistence keys off the interests themselves, not this flag.
   */
  roots_only_fallback?: boolean;
  /** SKIP-TUNE mute terms (spec §6). Persisted to `user_mute_terms` as hard assembly filters. */
  mute_terms?: TerminalMuteTerm[];
  /** ANGLE-TUNE preferences (spec §6). Additive; not yet consumed by assembly in this slice. */
  angle_preferences?: TerminalAnglePreference[];
}

// ─── Turn protocol (spec §2–§3) — the TS twin of the worker's turn models ─────
//
// The worker is stateless: the whole conversation so far travels with every
// request (`agents/interview/models.py::InterviewTurnRequest`). The client is a
// dumb renderer — it POSTs the accumulated exchanges and renders whichever of the
// three response shapes comes back, discriminated by `response_kind`.

/**
 * A bubble's role. `option` is a real answer; `skip` ("not really / skip") and
 * `type_your_own` ("something else — type it") are the two affordances the worker
 * guardrails ALWAYS append (spec §3), rendered with distinct treatment.
 */
export type InterviewBubbleKind = "option" | "skip" | "type_your_own";

/** One rendered bubble in a non-terminal turn (twin of `InterviewBubble`). */
export interface InterviewBubble {
  /** The user-vocabulary label to render on the bubble. */
  bubble_label: string;
  /** option / skip / type_your_own — drives the client affordance. */
  bubble_kind: InterviewBubbleKind;
}

/**
 * One completed prior turn, echoed back to the stateless worker on the next
 * request (twin of `InterviewExchange`). `bubbles_tapped` is a subset of
 * `bubbles_offered`; `free_text_entered` is set only on a "type it" turn.
 */
export interface InterviewExchange {
  /** The question the client showed the user this turn. */
  question_text: string;
  /** The bubble labels shown (includes the skip / type-your-own affordances). */
  bubbles_offered: string[];
  /** The bubble labels the user tapped this turn (may be empty on a skip). */
  bubbles_tapped: string[];
  /** What the user typed via "something else", if anything. */
  free_text_entered?: string | null;
}

/** The interview turn request body — the full conversation state so far (spec §2). */
export interface InterviewTurnRequest {
  /** Ordered prior exchanges; empty on the very first turn. */
  conversation_state: InterviewExchange[];
}

/** A non-terminal turn: the next question plus its bubbles. */
export interface InterviewQuestionTurn {
  response_kind: "question";
  /** 0-based index of the turn this response is for. */
  turn_index: number;
  /** The question text to render. */
  question_text: string;
  /** The bubbles to render (≤ 6 options + skip + type-your-own). */
  bubbles: InterviewBubble[];
}

/**
 * The terminal turn: the extracted micro-interest list for confirmation.
 *
 * NOTE: the worker also returns `deferred_questions` on the terminal payload (every
 * skipped question, for later in-app resurfacing — interview spec §5/§6, added by
 * slice #14). Like `turn_cost`, it is intentionally NOT modeled here yet — the
 * in-app resurfacing surface is a fast-follow; the extra JSON is ignored for now.
 */
export interface InterviewTerminalTurn {
  response_kind: "terminal";
  turn_index: number;
  /** The extracted interests (empty on the skip-everything path). */
  micro_interests: TerminalMicroInterest[];
  /** True when the interview terminated on the skip path (roots-only feed). */
  roots_only_fallback: boolean;
  /** SKIP-TUNE mute terms (spec §6) — persisted to `user_mute_terms`, hard-filtered at assembly. */
  mute_terms: TerminalMuteTerm[];
  /** ANGLE-TUNE preferences (spec §6) — additive terminal output carried with the profile. */
  angle_preferences: TerminalAnglePreference[];
}

/**
 * The graceful-failure turn (spec §2): a typed body the worker returns with HTTP
 * 200 (or the client synthesizes on transport/parse failure). The client renders
 * an in-place retry — a failed turn is NEVER a dead-end screen.
 */
export interface InterviewRetryTurn {
  response_kind: "retry";
  turn_index: number;
  /** Stable machine error code (e.g. `"llm_unavailable"`, `"transport_error"`). */
  error_code: string | null;
  /** Human-readable message to show above the retry affordance. */
  error_message: string | null;
  /** What the client should do next (advisory copy). */
  retry_hint: string | null;
}

/**
 * One interview turn — discriminated by `response_kind`.
 *
 * NOTE: the worker also returns `turn_cost` (per-turn LLM token/latency, PRD #24/#25)
 * on every response. It is intentionally NOT modeled here yet — client-side cost
 * accumulation is deferred to the cost-observability slice; the extra JSON is ignored.
 */
export type InterviewTurn = InterviewQuestionTurn | InterviewTerminalTurn | InterviewRetryTurn;
