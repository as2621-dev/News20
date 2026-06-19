# Phase SP2 — Sub-phase 3 execution report

**Sub-phase:** Make `allocate_test_feeds.py` source-aware (or refuse real users)
**Status:** SUCCESS

## What I implemented (and why)
**Chose: source-aware re-placement** (owner's primary choice), NOT refusal.
Re-placement was entirely practical here, so the refuse-real-user fallback was
not needed.

The bug: `allocate_test_feeds.py` calls `assemble_user_feed(...)` **without a
`source_stories` argument**, so the freshly-assembled feed contains ZERO source
slots. It then does a `daily_feeds.delete().eq(user).eq(date)` (dropping the
user's whole day, including any `feed_slot_kind='source'` reels produced by
`produce_source_reels.py`) and writes the source-less fresh feed — silently
evicting followed-source reels (the Nitish-eviction bug, second path).

The fix (surgical, one file):

1. **`_load_existing_source_rows(client, user_id, feed_date_iso)`** — BEFORE the
   delete, fetch the user's existing `feed_slot_kind='source'` rows
   (`feed_story_id, feed_score, feed_matched_interest_id, feed_position`, ordered
   by `feed_position`) off the standard fluent
   `.select().eq().eq().eq().order().execute().data` chain.

2. **`_replace_source_slots(slots, existing_source_rows)`** — re-place the carried
   source rows into the assembled feed **without overshooting `FEED_SLOT_BUDGET`
   (30)**. Source rows are placed FIRST (guaranteed to survive, up to 30), deduped
   on `feed_story_id` against each other; then the assembled topic slots fill the
   REMAINING budget (`30 - len(source)`), with the lowest-priority topic slots
   trimmed from the tail and any topic whose story id collides with a carried
   source id dropped (so `uq_daily_feed_story` can't trip). Positions are
   reassigned 1..len via `model_copy(update={...})` so `uq_daily_feed_position`
   holds. Returns `slots` unchanged (identity) when there are no prior source rows.

3. **Wiring in `main`**: capture → re-place → structured `source_rows_replaced`
   log (when any survived) → existing delete+`write_daily_feed` (unchanged).

4. **`_slot_category`** now returns `"source"` for `SLOT_KIND_SOURCE` slots (was
   falling through to `"culture"` since source slots carry no matched interest),
   so the `user_feed_allocated` audit log labels them correctly.

Mirrors the SP1 source-row shape in `produce_source_reels.py`:
`feed_slot_kind='source'`, no matched interest, `feed_score` carried.

## Files modified
- `scripts/e2e/allocate_test_feeds.py` (only this file — did NOT touch
  `scripts/produce_source_reels.py`).

## Divergences from the plan + why
- **Source rows LEAD the feed** (positions 1..k) rather than landing at an
  allocation-sequence position. Reason: `allocate_test_feeds.py` runs
  `assemble_user_feed` with no source allocation/`source_stories` knowledge, so
  there is no source sequence position to honor. Leading the feed is the simplest
  collision-free preservation that GUARANTEES survival within the 30-budget. For an
  e2e harness this is acceptable; it does not affect the DoD (survival).
- **Preserve, not refuse.** The 4 seeded test users (in
  `.agents/e2e/state/test-users.json`) are UIDs `40504599…/d65af13f…/b57882f4…/
  e3c311e2…`; ash `b316800d-…` is NOT among them, so the normal path never targets
  ash. The re-placement guard makes the script safe **even if** a real user were
  ever passed (source rows survive). No allowlist added — preservation covers the
  risk without new config to maintain (Rule 2/3).

## Code-review findings + fixes
- **(low) Stale inline comment** — first draft's wiring comment said source rows
  are placed "after the assembled topic slots"; implementation leads with them.
  **Fixed** to "leading the feed, within the 30-budget".
- **(low) Full-feed eviction risk in first draft** — the initial
  `_replace_source_slots` APPENDED source rows at `len(slots)+1…` and capped at 30,
  which would DROP source rows when the assembled feed already filled 30 (ash's
  case). **Fixed** by re-placing source rows first and trimming the topic tail, so
  source rows are guaranteed-preserved. Verified by Case 1 below.
- **Delete safety (reviewed, no change):** delete still runs only after the source
  rows are captured; the source rows are then re-inserted by `write_daily_feed` in
  the same single `insert`. The pre-existing non-transactional delete-then-insert
  window is UNCHANGED (not worsened) — write-fail-safety for the OTHER file is
  SP1/SP2's scope.
- **Constraints (reviewed):** positions reassigned 1..len (unique →
  `uq_daily_feed_position` safe); story ids deduped across source+topic (→
  `uq_daily_feed_story` safe); `feed_score` coerced `≥ 0.0` (model `ge=0.0` safe).

No outstanding critical/high/medium.

## Validation results
Commands (ran from repo root):
- `./.venv/bin/ruff check scripts/e2e/allocate_test_feeds.py` → `All checks passed!`
- `./.venv/bin/python -c "import ast; ast.parse(...)"` → `SYNTAX OK`
- **Throwaway inline check of `_replace_source_slots`** (pure function; no live
  allocator run):
  - **CASE1 (ash-like):** full 30-topic assembled feed + 4 prior source rows →
    all 4 source reels survive (lead positions), total=30, positions 1..30, 26
    topics retained (4 trimmed from tail). PASS.
  - **CASE2:** no prior source rows → `slots` returned unchanged (identity). PASS.
  - **CASE3:** a source story id colliding with a topic story id → deduped, source
    wins, no duplicate. PASS.
  - **CASE4:** short feed (5 topics) + 2 source → 7 total, all kept, positions
    1..7. PASS.
  - `ALL CHECKS PASS`.

No permanent test file created (SP4 owns the test dir; its scope is
`produce_source_reels.py`). The throwaway snippet was inline and discarded.

## Definition of done: PASS
DoD: "invoking the script for ash either preserves his source rows (asserted by a
test) or aborts; invoking for a seeded test profile still rebuilds normally."
- **Preserves ash's source rows:** verified by CASE1 — even with a full 30-topic
  feed, all prior source reels survive (the worst case for ash). The capture
  happens before the delete; re-placement guarantees survival within 30.
- **Seeded test profile still rebuilds normally:** verified by CASE2 — a profile
  with no prior source rows gets the unchanged assembled feed (identity return),
  so normal rebuild behavior is untouched.

## Concerns for the orchestrator
- **Ordering change:** for a user WITH prior source rows, the rewritten feed now
  LEADS with source reels (positions 1..k). If a downstream check asserts the feed
  starts with a topic slot or a specific category, it may need updating. Test
  profiles (no prior source rows) are unaffected.
- **Topic-tail trim:** when prior source count + assembled topics > 30, the
  lowest-priority topic slots are trimmed. This is correct (source rows were part
  of the original 30), but the feed will show fewer topic slots than a no-source
  run — expected, not a regression.
- **Write window unchanged:** the non-transactional delete→insert still has a brief
  partial-write window. SP1/SP2 added the fail-safe write only to
  `produce_source_reels.py`; this file was out of their scope. If the orchestrator
  wants the same guard here, that's a follow-up (not in this sub-phase's DoD).
