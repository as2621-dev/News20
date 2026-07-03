# Residual review findings — slice #1 (interview engine)

Date: 2026-07-03. From the B9 multi-agent review panel (correctness, simplicity, security,
contract). The HIGH/MEDIUM defects were fixed in the slice commit; the items below were
deferred as advisory or needing a human call. None block the slice.

## Deferred (advisory / human decision)

- **MEDIUM (correctness) — forced-terminate can discard drill specificity.** On a hard
  terminate (drill cap or tap budget), the engine consumes `decision.micro_interests` from
  the same turn's response. If the model returned `action="ask"` (ignored the
  `must_terminate_now` CONTROL hint), its interests are empty and the engine falls back to
  root-level interests — losing the user's drill depth. The prompt instructs the model to
  terminate when CONTROL says so, and the fallback never dead-ends, so impact is a
  quality/specificity loss on the boundary turn, not a break. *Fix if it shows up in
  practice:* on a forced terminate with empty/all-invalid drafts, issue one
  extraction-only Gemini call (action pinned to terminate) before the roots-only fallback.

- **MEDIUM (observability) — interview auth reuses `verify_supabase_user`, which logs
  `assemble_mine_*` event names.** Auth *behavior* is correct and identical to
  `/feed/assemble-mine` (the intended contract), but a 401 on the interview route logs
  `assemble_mine_auth_missing_token` etc. with "on /feed/assemble-mine" messages,
  misattributing interview auth failures in log-based alerting. *Fix:* factor the JWT
  verifier out of `pipeline_routes.py` into a shared auth helper with neutral event names
  (or a route-label param). Deferred because it touches `pipeline_routes.py` + its tests —
  a cross-cutting change better done as its own small slice.

- **LOW (cost) — malformed/blocked structured JSON retries 3× with backoff.** In
  `LLMClient.call_gemini_json`, a `parsed is None` raises inside the retry wrapper, so a
  deterministically unparseable/safety-blocked response costs ~3 Gemini calls + ~6s before
  surfacing (still an HTTP-200 retry to the client — contract honored, just slow/costly on
  the failing turn). *Fix:* treat `parsed is None` as non-retryable.

- **LOW (correctness) — non-root real taps on turn 1 read as "skip everything".**
  `lit_roots_from_state` resolves turn-1 taps only via `ROOT_SLUG_BY_LABEL`; an unknown
  turn-1 label yields empty lit_roots and (with no free text) an empty roots-only terminal.
  Only reachable via a malformed/adversarial client body — the real client offers only the
  8 roots on turn 1. *Fix if the client contract loosens:* distinguish "tapped nothing"
  from "tapped an unrecognized label".

- **LOW (contract) — `InterviewTurnResponse` is a flat bag-of-optionals, not a Pydantic
  discriminated union.** A `question` response still serializes `micro_interests: []`,
  `error_code: null`, etc. The `response_kind` discriminant is sufficient for a dumb
  renderer, but TS codegen won't get a tagged union. Kept flat deliberately for renderer
  simplicity; revisit with slice #4 if the client wants compile-time branch guarantees.

- **LOW (simplicity) — `engine.__all__` re-exports guards helpers.** Only the tests use the
  re-export; production imports just `run_interview_turn`. Harmless single import surface;
  could be dropped in favor of importing pure helpers from `agents.interview.guards`.

- **LOW (simplicity) — `GeminiJsonResult.elapsed_ms` is unused by the engine** (it measures
  its own turn wall time, which includes validation). Kept as a legitimate attribute of the
  call result; drop if no consumer emerges.

## Intentional (reviewer-flagged, accepted as designed)

- **`had_free_text` widens terminal traceability.** After the fix, an interest under a
  NON-lit root is accepted only as a root-level park (never a deep niche), and only when
  the user typed free text. Deep niches are always confined to lit (tapped) roots. The
  terminal list is user-confirmed (spec §2) and scoped to the caller's own onboarding, so
  residual risk is minimal.
