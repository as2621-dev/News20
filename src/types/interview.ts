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

/**
 * The four skippable-question kinds (spec §5 skip semantics) — the ONE declared seed the
 * union type, the two runtime parse/persist allow-lists, and (implicitly) the migration-0030
 * CHECK all derive from, so they can never drift (Rule 7). The TS twin of
 * `agents/interview/models.py::DeferralKind`.
 */
export const INTERVIEW_DEFERRAL_KINDS = ["subniche_skip", "category_skip", "who_drill_skip", "roots_skip"] as const;

/** Which skippable question a deferred record came from — derived from {@link INTERVIEW_DEFERRAL_KINDS}. */
export type InterviewDeferralKind = (typeof INTERVIEW_DEFERRAL_KINDS)[number];

/**
 * One question the user SKIPPED during the interview, recorded at terminal for later in-app
 * resurfacing (spec §5/§6). The TS twin of `agents/interview/models.py::DeferredQuestion`.
 * Engine-emitted + deterministic (never model judgment). Persisted to `user_deferred_questions`
 * (migration 0030); NOT consumed by feed assembly.
 */
export interface InterviewDeferredQuestion {
  /** Which skippable turn produced this record. */
  deferral_kind: InterviewDeferralKind;
  /** The exact question the user skipped (as shown that turn). */
  question_text: string;
  /** The category root the skip belongs to, when applicable (`null` for a roots-level skip). */
  root_slug: string | null;
  /** The sub-niche label the WHO drill was about, when applicable (`null` otherwise). */
  subniche_label: string | null;
}

/**
 * One X-cluster the user selected in the in-chat CLUSTERS picker (slice #20). Carries
 * enough to persist WITHOUT re-reading the catalog at terminal: the cluster's own id
 * (for the `user_source_clusters` cluster ref — sweep scheduling / theme attribution)
 * plus its already-resolved member followables (which the picker expanded from the
 * catalog when it loaded). Client-computed in the closing arc, like {@link InterviewTerminalPayload.top_split}.
 */
export interface InterviewClusterPick {
  /** `source_clusters.cluster_id` — the cluster ref written to `user_source_clusters`. */
  cluster_id: string;
  /** `source_clusters.cluster_slug` — for logging / provenance. */
  cluster_slug: string;
  /** The cluster's `content_sources.source_id` members to follow (→ `user_content_sources`). */
  member_source_ids: string[];
  /** The cluster's `personalities.personality_id` members to follow (→ `user_personalities`). */
  member_personality_ids: string[];
}

/**
 * The user's in-chat source picks (slice #20) — the YOUTUBE grid selections + the X
 * CLUSTERS selections. Empty on both axes is VALID (those slots default to news at
 * assembly). Persisted at terminal ONLY (never on toggle) by
 * {@link import("@/lib/onboardingTerminal").persistOnboardingTerminal}.
 */
export interface InterviewSourceFollows {
  /** Selected `content_sources.source_id`s (youtube_channel axis) → `user_content_sources`. */
  youtube_source_ids: string[];
  /** Selected X clusters, each pre-expanded to its members + cluster ref. */
  clusters: InterviewClusterPick[];
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
  /**
   * Every question the user SKIPPED (spec §5/§6). Persisted to `user_deferred_questions`
   * (migration 0030) for later in-app resurfacing. Optional: absent = no skips recorded.
   */
  deferred_questions?: InterviewDeferredQuestion[];
  /**
   * The user's CLOSING-ARC feed split across the three axes `{ news, youtube, x }` summing to
   * 30. CLIENT-COMPUTED (the worker never sends it — it is captured in the chat's closing arc),
   * so it is optional here; the terminal-persist orchestrator
   * ({@link import("@/lib/onboardingTerminal").persistOnboardingTerminal}) rejects a payload
   * whose split does not sum to exactly 30 before any allocation write.
   */
  top_split?: { news: number; youtube: number; x: number };
  /**
   * The in-chat YOUTUBE + X CLUSTERS picks (slice #20). CLIENT-COMPUTED in the closing arc
   * (the worker never sends it), so optional here. Absent = no source picks (valid — the
   * youtube/x slots default to news). Persisted at terminal ONLY: channel sources +
   * cluster member expansion + cluster refs.
   */
  source_follows?: InterviewSourceFollows;
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
 * NOTE: the worker also returns `turn_cost` (per-turn LLM token/latency) on the terminal
 * body. It is intentionally NOT modeled here yet — client-side cost accumulation is deferred
 * to the cost-observability slice; the extra JSON is ignored for now.
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
  /**
   * Every question the user SKIPPED (spec §5/§6), narrowed off the worker terminal body — for
   * later in-app resurfacing (persisted to `user_deferred_questions`, migration 0030). Empty
   * when nothing was skipped.
   */
  deferred_questions: InterviewDeferredQuestion[];
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
