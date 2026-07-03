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
 * Only the terminal shape lives here — the question / retry turn shapes are the
 * chat-UI slice's (#4) concern and are added when that slice lands.
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

/** The terminal interview payload the client confirms, then persists. */
export interface InterviewTerminalPayload {
  /** The extracted micro-interests. Empty when the user skipped through with no roots lit. */
  micro_interests: TerminalMicroInterest[];
  /**
   * True when the interview terminated on the skip path (feed falls back to roots-only).
   * Informational for the client; persistence keys off the interests themselves, not this flag.
   */
  roots_only_fallback?: boolean;
}
