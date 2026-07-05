# Residual review findings — issue #19 (budget card + YOUR-30 + terminal-persist wiring)

Head at review: `7913a35` (pre-commit). Claude-native multi-agent review panel: correctness ·
simplicity/reuse · contract/architecture · data-integrity, run in parallel on the slice diff.

All CONCRETE, in-scope findings were APPLIED before the commit. The items below are deferred with
a rationale (needs-human-decision or out-of-slice-scope).

## Applied (folded into the slice commit)
- **[simplicity-reuse + contract + correctness, Low doc]** Three stale `build`-step references in
  `OnboardingFlow.tsx` comments (step-5 docblock, the `SKIP_AUTH` docblock, and the
  `selectedCategoryBuckets` comment) survived deleting the separate `build` step. Rewritten to
  reference the source step / in-chat closing-arc allocation. No behavior change.
- **[contract, Low doc]** `RebuildFeedFlow.tsx` header docstring still described the old
  `persistInterviewInterests` two-call path; rewritten to the single `persistOnboardingTerminal`
  clean-replace seam.
- **[reuse, applied in-slice]** OnboardingFlow's pre-existing inline micro-interest→bucket fold was
  deduped against the shared `feedBuckets.categoryBucketsFromMicroInterests` helper (the #30 review
  residual asked for this) — now one root→bucket fold, single source of truth (Rule 7).

## Deferred (needs human decision — NOT applied in this slice)
- **[correctness, MED] Source-axis slots are allocated at the chat confirm, before the source step,
  with no followed-backing guarantee.** The budget card runs inside the interview (before
  `SourceClusterScreen`) and defaults to `20 news / 7 YouTube / 3 X`; `persistOnboardingTerminal`
  writes a `youtube:7` + `x:3` coarse allocation block immediately. A follow-nothing / skip-everything
  user therefore reserves 10 of 30 slots on source axes with zero backing — the same shape as the
  2026-06-17 "phantom allocation" owner rule. The retired `BuildYour30` flow avoided this by deriving
  `followedSourceBuckets` from real follows so a followed-nothing axis never appeared; that guarantee
  is gone now that the budget card owns the split.

  **Why deferred, not fixed here:**
  1. The rescoped issue #19 spec is explicit — "default 20 news / 7 YouTube / 3 X, remixable 0–30 per
     axis" and "`user_feed_allocation` already supports youtube/x enum rows summing to 30". Gating the
     source axes by actual follows contradicts the stated spec.
  2. The issue's architecture is "ONE terminal persist at the chat confirm." Reconciling the
     source-axis allocation *after* the source step (the reviewer's fix option) reintroduces a second
     allocation write — the exact coupling the rescope removed.
  3. AC7 ("skip-everything → roots-only *working* feed") still holds: the assembler's produce-cap
     headroom (2026-06-22) + niche/roots fallback backfill (slice #7) fill unbacked source slots from
     the category/roots pool, so the feed still reaches 30. The only degradation is cosmetic — a
     "YouTube"/"X" slot may surface a news story for a user who followed nothing on that axis.

  **Decision needed from the owner:** either (a) accept the current behavior + document that the
  assembler backfills unbacked source blocks; or (b) file a follow-up slice to clamp/omit the
  `youtube`/`x` budget axes for users who follow nothing on them (which requires moving or splitting
  the budget card relative to the source step). No code changed pending that call.

## Non-blocking advisories (informational — no action taken)
- **[data-integrity, Low, #30-owned]** `persistOnboardingTerminal` passes an explicit `userId` to the
  interests/mutes/deferred writes, but `saveUserFeedAllocation` independently re-derives the authed
  user id from the client session (`feedAllocation.ts`). Both come from the same session at every call
  site and RLS backstops any divergence, so no cross-user write is possible — a latent scope-source
  inconsistency inside the #30 seam, not this slice's diff. Left untouched (Rule 3).
- **[UX, Low]** An empty-interests *rebuild* still walks the user through the budget card before the
  "keep old profile" short-circuit fires. No data impact (nothing persists); minor UX wart.
- **[UX, Low]** On a persist *failure* re-entry the budget selection resets to `20/7/3` (remount-fresh
  design); the user redoes the budget card after resuming. Expected given the resume-fresh contract.
