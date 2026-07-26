---
title: A recovery path that shares a budget with the thing it recovers from is dead at the shipped default
tags: [ceilings, budgets, retry, fallback, spend-safety, defaults, entry-points, unreachable-code]
problem_type: architecture-pattern
symptoms: a newly built failure-recovery mechanism (retry, promotion, backfill, fallback) is
  fully tested and provably correct in unit tests, but never fires in production; no error, no
  log, no crash — the recovery is simply refused on its first iteration, every run
root_cause: the recovery drew from the SAME ceiling as the primary path, and the shipped default
  of that ceiling was already fully consumed by the primary path, so the remaining budget was
  always exactly zero
date: 2026-07-26
---

Found in issue #74. The daily batch was inverted to select each user's 30 BEFORE producing, with
a standby-promotion loop to replace any reel that failed production. The loop computed
`remaining_budget = max(0, MAX_PRODUCE - len(attempted))` — attempts, deliberately, because a
failed reel has usually already billed its script and bounding attempts is the frugal reading of
a spend ceiling.

But **both production entry points default `MAX_PRODUCE` to 8, and `enforce_overall_ceiling`
trims the candidate pool to 8 first.** So the selection was ~8, `attempted` was ~8, the remaining
budget was 0, and `promote_standbys` refused on its first iteration of every single run. The
whole feature was unreachable on the only path that matters. Tests passed — they all set their
own ceiling or none at all.

**The tell:** a recovery path is not "bounded by" a shared ceiling, it is *gated by whatever the
primary path left over*. If the primary path is sized to consume the ceiling — and it usually is,
because that is what a ceiling is for — the leftover is zero by construction. Check the new
mechanism against the **shipped defaults of every knob it shares**, not only against its own
logic. Grep the entry points for the default (`_DEFAULT_MAX_TOTAL_PRODUCTIONS`,
`os.environ.get("MAX_PRODUCE", "8")`), substitute it, and hand-evaluate the guard.

**The fix that holds:** budget the recovery on the dimension it actually affects, and bound
runaway separately.
- A promotion *replaces* a failure; it does not add to the feed. So the ceiling counts reels
  **shipped** (`MAX_PRODUCE - len(produced)`), which lets a run at the default ceiling backfill a
  failure while never shipping more than the ceiling.
- Runaway retries then need their own, different bound — here `MAX_STANDBY_PROMOTION_ROUNDS = 3`,
  a round count, not a spend count.

Two bounds with two jobs beats one bound doing neither. Compare
[[each-spend-rung-needs-its-own-arm]]: same shape, one knob asked to express two independent
decisions and collapsing to the useless answer. And per
[[safety-default-read-inline-is-not-a-default]], the check belongs where both entry points read
it, not in the script you happened to test with.

**The test that catches it:** pin the shipped default explicitly —
`test_max_produce_at_the_selection_size_still_backfills_a_failure` runs the batch with
`max_total_productions=8` (the real default, not a convenient one) and asserts a promotion still
happens *and* the ceiling still holds. A test that omits the ceiling proves nothing about
production.
