# Residual review findings — issue #30 (terminal-persist data core)

Head at review: `987f47e`. Multi-agent review panel (correctness / simplicity-reuse /
data-integrity / contract). All CONCRETE findings were APPLIED before the commit; the one
item below is deferred with a rationale.

## Applied (folded into the slice commit)
- **[data-integrity, Low-Med]** `persistDeferredQuestions` first-run was insert-only → non-idempotent
  (dup-on-retry, since deferred has a surrogate PK and no natural conflict key). Fixed: delete-first
  whenever there are rows to write (`replace_existing || rows.length > 0`), so a lost-response retry
  converges; an empty first-run set stays a true no-op. Docstrings + tests updated.
- **[simplicity-reuse, Medium]** New `selectedCategoryBucketsFromInterests` forked the root→bucket
  fold. Extracted to `feedBuckets.ts::categoryBucketsFromMicroInterests` (the Rule-7 single source of
  truth, beside `categoryBucketsFromFollows`/`…FromInterestVector`) and consumed it in the orchestrator.
- **[simplicity-reuse + correctness, Low-Med]** The 4 deferral kinds were hand-written as a runtime
  allow-list in two files on top of the union + SQL CHECK. Collapsed to ONE seed
  `INTERVIEW_DEFERRAL_KINDS` in `types/interview.ts`; the union derives from it and both parse/persist
  allow-lists import it.
- **[contract + correctness, Low]** Clamp `deferred_root_slug`/`deferred_subniche_label` to the
  migration-0030 `≤ 200` CHECK locally (loud drop-to-length, not an opaque DB throw).
- **[contract + data-integrity, Low doc]** Reworded the orchestrator "delete-last internally" comment:
  interests is destructive-LAST internally; mutes + deferred are delete-FIRST; the destructive STEP
  still lands last across the four (deferred is the tail).

## Deferred (advisory — not applied in this slice)
- **[simplicity-reuse, Medium] `src/components/onboarding/OnboardingFlow.tsx:~202-217`** still carries a
  PRE-EXISTING inline copy of the same micro-interest→bucket fold that now lives canonically in
  `feedBuckets.ts::categoryBucketsFromMicroInterests`. Not deduped here because #30 is a **library +
  migration** slice (Rule 3 — do not touch a user-facing component outside the slice's surface). When
  **#19** wires the closing arc (it already edits `OnboardingFlow`/`InterviewChat` and calls
  `persistOnboardingTerminal`, which re-derives the buckets internally), replace the inline loop with a
  call to the shared helper so the fold exists in exactly one place. Behavior is identical (same
  warn-on-unmapped, same result), so it is a pure mechanical dedupe — no product decision needed.
