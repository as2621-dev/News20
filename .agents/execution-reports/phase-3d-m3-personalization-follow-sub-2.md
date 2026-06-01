# Execution report — phase-3d-m3-personalization-follow · SP2

**Sub-phase:** SP2 — Interest-weighted ranking + **follow boost** (the only ranking change in 3d)
**Status:** SUCCESS
**Date:** 2026-05-31

## What this sub-phase did
Extended the existing M1 daily profile-update loop so a story a user **follows**
persistently boosts the `profile_weight` of that story's matched interest node(s),
re-applied on **every** run while followed, inside the existing over-narrowing
guards. Updated `reference/ranking-spec.md` §4 so the follow contribution is
sourced from the persistent `follows` set, not a one-shot `player_signals.follow`
row. Did NOT touch the base scorer/allocator (done in M1).

## Files modified
- `agents/memory/session_processor.py` — added `_load_follows` reader; extended
  `compute_weight_updates` with an optional `followed_story_ids` param that folds a
  `FOLLOW_BOOST_DELTA` per followed story into the SAME `raw_delta_by_interest`
  accumulator (shared cap/decay/clamp); wired `_load_follows` into
  `run_profile_update_job` and widened the `story_interests` read to cover followed
  stories too.
- `agents/memory/player_signals.py` — added the `FOLLOW_BOOST_DELTA = 0.40`
  constant near the other §4 deltas; removed `follow` from `_STRONG_POSITIVE_EVENTS`
  and gave it an explicit inert (`0.0`) branch so a transient `follow` signal is not
  double-counted; updated module docstring.
- `reference/ranking-spec.md` — §4 surgical edit (follow now sourced from the
  persistent `follows` set; the signal table's strong-positive row corrected to
  `complete/save/ask/voice`, dropping the stale `follow` listing).
- `tests/agents/memory/test_session_processor.py` — added 7 focused tests (5 pure
  core + 2 end-to-end through the mocked Supabase client).

**Did NOT touch:** base scorer (`agents/pipeline/stages/ranking.py`), allocator
(`feed_assembly.py`), any migration, any client/TS file, any 3b file.

## The join used + double-count prevention
- **Source of truth for the follow contribution = the persistent `follows` table.**
- **Join (text = text, NO uuid cast):** `_load_follows` reads
  `follows.follow_user_id, follows.follow_story_id` (both `str`). In
  `compute_weight_updates`, each `followed_story_id` is resolved through the same
  `tags_by_story` index built from `story_interests`
  (`story_interest_story_id` text → `story_interest_interest_id` uuid +
  `story_interest_match_depth`) that the signal pass already uses. Followed-story
  ids are added to the `story_interests` read set in `run_profile_update_job`, so
  the tags resolve. The matched interest node is boosted; ancestor-tagged nodes are
  attenuated by `DEPTH_ATTENUATION` exactly like signals (Rule 11 — mirrored, not a
  new mechanism).
- **Double-count prevention:** `follow` was removed from
  `player_signals._STRONG_POSITIVE_EVENTS` and given an explicit `return 0.0` branch.
  So a transient `player_signals` row with `event_type='follow'` contributes
  nothing; the follow is counted exactly once, from the `follows` set. Asserted by
  `test_transient_follow_signal_is_inert_no_double_count` (same story present as both
  a `follow` signal and a `follows` row → `raw_delta == FOLLOW_BOOST_DELTA`, not 2×).

## Follow-strength constant + bounds
- `FOLLOW_BOOST_DELTA = 0.40` (in `player_signals.py`, near the signal-weight
  constants — one named config constant, no scattered magic numbers).
- Stronger than a single transient strong signal (`STRONG_POSITIVE_DELTA = 0.30`),
  reflecting a deliberate, durable "more of this subniche."
- **Strictly below** the per-run cap `MAX_DELTA_PER_RUN = 0.5`, so a single follow
  in one run nudges hard yet cannot jump a weight a full step toward the ceiling.
- Feeds the SAME accumulator → the SAME `MAX_DELTA_PER_RUN` cap, the SAME slow decay
  toward `BASELINE_WEIGHT`, and the SAME `[PROFILE_WEIGHT_FLOOR=0.1,
  PROFILE_WEIGHT_CEILING=5.0]` clamp. There is no second/divergent guard system, so
  the boost cannot push a weight past the ceiling or collapse the feed.
- Re-applied every run while followed; un-following removes it and the weight decays
  back toward baseline.

## Divergences from plan
- **`voice` added to the §4 strong-positive row.** The plan's §4 table listed
  `complete/save/follow/ask`. The actual M1 code already treats `voice` as a strong
  positive (`_STRONG_POSITIVE_EVENTS`). Since I was removing the stale `follow` from
  that row anyway, I corrected the row to `complete/save/ask/voice` to match the code
  (Rule 7/12 — surface, don't blend). This is the truthful M1 contract.
- No other divergences. Job signature stayed backward-compatible
  (`followed_story_ids` is an optional kwarg defaulting to `None`), so `daily_batch.py`
  and the 7 existing `compute_weight_updates` call sites are unaffected.

## Review findings + fixes
- Correct text=text join, no cast — PASS (no fix needed).
- Double-count avoidance — PASS (designed in + test).
- Guard/decay correctness, no over-narrowing — PASS (shared guards + ceiling test).
- Ancestor attenuation mirrors signals — PASS (shared `_accumulate` helper).
- Backward-compat signature — PASS (optional kwarg).
- One pre-run fix: the end-to-end test's `non_follower_weight` lookup could KeyError
  because a baseline-1.0 non-follower decays to exactly 1.0 and the row is not written
  (the wrapper skips unchanged weights). Changed to `.get("u_non_follower", baseline)`.
- No swallowed exceptions added; job stays fail-loud.

## Validation results
- `ruff format` — 2 files reformatted, then clean.
- `ruff check agents/memory/session_processor.py agents/memory/player_signals.py
  tests/agents/memory/test_session_processor.py` — **All checks passed.**
- `pytest tests/agents/memory/ -q` — **14 passed** (7 prior + 7 new).
- `pytest tests/agents/ -q` — **233 passed**, 2 pre-existing third-party deprecation
  warnings. No regressions from removing `follow` from `_STRONG_POSITIVE_EVENTS`
  (grep confirmed no other code depended on it).

## Definition of done — PASS
All three DoD clauses asserted on the resulting/persisted `profile_weight` (Rule 9):
1. Follow RAISES the matched interest's weight vs. an identical non-following user —
   `test_following_a_story_raises_matched_interest_vs_non_following_user` (pure core)
   + `test_run_job_follow_raises_weight_vs_identical_non_following_user` (end-to-end,
   asserts on the persisted update payload via the mocked client). PASS.
2. Un-following before the run yields no boost —
   `test_unfollowing_before_the_run_yields_no_boost` +
   `test_run_job_no_follows_table_rows_is_a_no_op_for_follow_boost`. PASS.
3. Boost stays within bounds/decay (no over-narrowing past floor/ceiling) —
   `test_follow_boost_cannot_push_weight_past_the_ceiling` + ceiling assertions in
   the rises-vs-peer tests. PASS.

## Concerns for the phase-end DoD / slop / CSO pass
- **`FOLLOW_BOOST_DELTA = 0.40` is first-draft / tunable** (phase Open Q on follow
  strength + decay). It is bounded by the §4 cap, so it cannot over-narrow, but the
  exact equilibrium weight a single persistent follow settles at (boost vs. decay)
  is a tuning call for the 2-user manual run, not yet empirically validated.
- **§4 contract edit + `player_signals.py` behavioral change** touch the M1 ranking
  contract (flagged in the plan). The `follow` signal is now inert; if any future
  code newly relies on a transient `follow` nudge, it must read the `follows` set
  instead. Cross-doc staleness (master-plan/memory referencing `follow` as a signal)
  noted by the plan as opportunistic cleanup — not done here (out of SP2 scope).
- Migration `0005_m3_follows.sql` (SP3) is **not yet live-applied** (per progress
  file + memory). The job reads `follows` via the injected service-role client; tests
  mock it. Live e2e correctness depends on `0005` being applied before the daily job
  runs against the real DB.
- `_load_follows` has no `since`/active filter — it boosts ALL current follows every
  run by design (the follows set IS the live "currently followed" truth; un-follow
  deletes the row). No staleness window needed.

---

## Return to orchestrator
1. **STATUS:** SUCCESS
2. **Files touched:** `agents/memory/session_processor.py`,
   `agents/memory/player_signals.py`, `reference/ranking-spec.md`,
   `tests/agents/memory/test_session_processor.py`
3. **Validation:** PASS — ruff format+check clean; `pytest tests/agents/memory/` 14
   passed; `pytest tests/agents/` 233 passed, no regressions.
4. **Definition of done:** PASS — all three clauses asserted on resulting/persisted
   `profile_weight`.
5. **Concerns:** `FOLLOW_BOOST_DELTA=0.40` is tunable (bounded, can't over-narrow);
   §4 + `player_signals.py` `follow`-now-inert change touches the M1 contract (flagged);
   migration `0005` not yet live-applied; cross-doc staleness left for opportunistic
   cleanup (out of scope).
