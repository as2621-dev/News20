# Residual review findings — slice #2 (micro-interest persistence)

Commit: `feat(interests): mint micro-interest nodes + deep profile + ladder (slice #2)`.
Findings the multi-agent review panel raised that were DEFERRED (advisory / human-call /
cross-slice), with the reasoning. Applied fixes are in the commit itself and not listed here.

## 1. Mint atomicity across RPC calls (low-med, data-integrity + correctness lenses)

`persistInterviewInterests` mints each ladder via a SEPARATE `client.rpc("mint_interest_ladder")`
call (each its own committed transaction), then writes the profile rows in one batch upsert
AFTER the loop. A mint failure mid-loop `throw`s before the profile upsert, so earlier items'
taxonomy nodes are committed with no profile rows.

**Why deferred, not fixed:**
- Minted nodes are GLOBAL, shared, idempotent taxonomy — an unreferenced node is not an orphan
  (every seeded interest is unreferenced until someone picks it) and a retry re-mints it as a
  no-op. Criterion 6 (no `user_onboarded_at` stamp, clean retry) holds for the real failure mode.
- The sharp edge is a PERMANENT RPC raise on an item that passed TS validation — that would
  `throw` on every retry and never persist the user's profile. Today this cannot happen: TS
  `TOPIC_ROOT_SLUGS` (the 8 `cat` buckets) and the RPC's `::segment_slug` cast + depth-0
  root-anchoring are aligned, and TS is strictly stricter (it also caps depth). It requires a
  future twin-drift to trigger.
- The suggested fix — a single set-returning `mint_interest_ladder_batch(slugs[], queries[])`
  RPC that mints all ladders in ONE transaction and returns `slug→leaf_id`, then the profile
  upsert — is a real improvement (also collapses N round-trips into 1) but is an architectural
  change beyond this slice. Converting the mid-loop `throw` into a `rejected_interests` collect
  was explicitly REJECTED: it would wrongly swallow a transient DB/network error as a silent
  per-item drop (Rule 12), which is worse than surfacing it.

**Follow-up:** file a `slice` issue for the batched-mint RPC if profile-write atomicity or the
round-trip count becomes a concern once slice #4 (chat UI) drives real onboarding volume.

## 2. Worker `guards.py` has no segment-count depth cap (low, contract lens)

`agents/interview/guards.py::_is_valid_slug` validates root-anchoring + per-segment regex but
does NOT cap the number of dotted segments, whereas the persistence side (TS `rejectionReason`
and the RPC) rejects `> 4` segments as `slug_too_deep` (spec §3: drill-down ≤ 3). A model-
fabricated 5+ segment slug would be accepted by the worker and shipped to the client, then
rejected at persistence (surfaced loudly as a `rejected_interest`).

**Why deferred:** the persistence side is the spec-correct one and fails loud (no silent
mint), so impact is low. The fix belongs in slice #1's file (`guards.py`) — add
`len(segments) <= MAX_DRILL_DEPTH_PER_ROOT + 1` to `_is_valid_slug` so producer and consumer
agree. Left to the worker owner to avoid a cross-slice edit here.

## 3. Duplicated DB-write blocks (low, simplicity lens)

The `user_interest_profile` batch-upsert block and the default `user_interest_traits` upsert
block in `interviewProfile.ts` mirror the equivalent blocks in `onboardingProfile.ts`
(`persistInterestProfile`, `persistPickerFollows`). Extracting `upsertInterestProfileRows` /
`upsertDefaultTraits` helpers would DRY three call sites.

**Why deferred:** the extraction would rewire two sibling functions in `onboardingProfile.ts`
that a concurrent session recently changed (the onboarding-gate fix, commit `bd2e758`). The
blocks are small and carry site-specific error strings/`fix_suggestion`s that aid debugging
(CLAUDE.md: boring, debuggable code over premature abstraction). Low value vs. regression
surface mid-slice. Revisit as a standalone refactor slice if a fourth persister appears.
