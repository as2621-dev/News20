# Residual review findings — slice #18 (chat onboarding UI)

Parent HEAD: `aaddc86de8576181282c8a39e964566e98311a77`

The multi-agent review panel (correctness · simplicity · contract/architecture · data-integrity)
ran against the slice diff. The CRITICAL contract finding (offer accepts unreachable in-UI) was
FIXED in this commit (engine: offer-accept bubbles `skip`→`option`; UI keeps `handleSkip → []`).
The items below are advisory/low-value and deferred (no user-visible impact, or a passing hot path
the correctness lens verified sound). Recorded here so they are not lost.

## Deferred (advisory)

1. **LOW–MEDIUM · `src/components/onboarding/InterviewChat.tsx` · `pendingStateRef` is redundant
   with `conversation`.** Because `fetchTurn` never rejects (it synthesizes a `retry` turn),
   `setConversation(nextState)` has already committed by the time `phase === "retry"`, so
   `conversation` always equals the failed-fetch state. `handleRetry` could be `advance(conversation)`
   and the ref deleted. Deferred: the correctness lens verified the current code is sound, and the
   ref documents "the state the failed turn was fetching"; removing it is a hot-path refactor with
   no behavioural gain. Safe to apply in a future cleanup.

2. **LOW · `src/types/interview.ts:105` · `InterviewTurnRequest` is an unused TS export.** The client
   builds the request body inline in `turnClient.ts`; this interface survives as documentation of the
   wire shape. Pre-existing file, out of this slice's scope. Either drop it or have `turnClient` type
   its body with it (`... satisfies InterviewTurnRequest`).

3. **LOW · `src/components/onboarding/InterviewChat.tsx` (offer turns) · offers render as
   multi-select chips.** After the engine fix, a skip-fast-forward offer is two `option` chips
   (decline + accept); nothing stops tapping both. The engine's `_tapped_label` uses `any(...)`, so
   "accept wins" — deterministic, not corrupting. A single-select treatment for offer turns is a
   nicety, not a correctness need. There is no turn-kind field to key it off, so it would require a
   heuristic — left for a future engine turn-kind enrichment (slice #19 territory).

## INFO (not a defect)

4. **Data-integrity lens · persist-failure remount shows the resume prompt.** On a persist failure the
   parent returns to the `interview` step, remounting `InterviewChat`, whose start effect (transcript
   intentionally retained) surfaces "Pick up where you left off?" rather than dropping straight back
   into the terminal confirm. This is a resume-cache UX quirk that pre-dates this slice — no server
   state and no `user_onboarded_at` stamp are affected. The onboarding-gate invariant holds.
