# Phase SP2 — Sub-phase 4 execution report

**Sub-phase:** Regression test — two-run source survival (Rule 9)
**Status:** SUCCESS

## File created
- `tests/scripts/test_produce_source_reels_rebuild.py`
  (placed in `tests/scripts/` — it already exists and is the project's script-test
  dir; matches the `tests/scripts/seed_catalog/` precedent.)

No other files were modified. `scripts/produce_source_reels.py` was NOT changed
(it only shows as Modified in `git status` because SP1/SP2 edited it and the
orchestrator hasn't committed yet — that is the pending phase work, not mine).

## What the test covers (3 assertions, all intent-encoding)

1. `test_source_reels_survive_empty_second_run` — run-1 has 2 live
   `feed_slot_kind='source'` reels + topic reels; run-2 drives `_rebuild_feed`
   with EMPTY `produced_yt`/`produced_x`. Asserts the inserted feed still contains
   both source ids (`sp3-yt1`, `sp3-x1`) at `source` slots, and exactly 2 source
   rows survive — never replaced by topic back-fill. **Encodes SP1's
   carry-forward.**
2. `test_two_runs_produce_distinct_backups` — two consecutive rebuilds must leave
   two distinct `ash_feed_backup_<date>_*.json` files (backups redirected into the
   test's tmp dir via an autouse fixture; glob + count). **Encodes SP2's
   non-clobbering backup suffix.**
3. `test_shrinking_rebuild_aborts_without_delete` — allocation with ZERO
   youtube/x budget + empty produce forces a 2→0 source shrink. Asserts: exit `1`;
   `delete_calls == 0`; `insert_calls == []`; live rows untouched; and a structured
   `feed_rebuild_aborted` log with `fail_loud=True`, `current_source_row_count=2`,
   `new_source_row_count=0`, `feed_date`, `backup_path`. **Encodes SP2's fail-safe
   guard.**

## Mocking strategy
`_FakeSupabase` is an observable client mocked at the supabase boundary (no real
service). It holds a mutable `daily_feeds` row store: `delete()` clears it and
`insert()` repopulates it, and **records** every delete/insert so the abort path
is proven to do NEITHER. `user_feed_allocation` + `interests` are static fixture
queries. Chains support `.select().eq().order().execute().data`,
`.delete().eq().eq().execute()`, and `.insert(rows).execute()` to match the real
call shapes in `_rebuild_feed`. The `/tmp` backup writes are redirected into the
pytest `tmp_path` by patching `mod.open` and `mod.os.path.exists` (autouse
fixture) — keeps SP2's real filename pattern under test while leaving no `/tmp`
litter (verified: my run adds zero new `/tmp/ash_feed_backup_*` files).

## Name discrepancies vs SP1/SP2 reports
None. Every name verified against `scripts/produce_source_reels.py` directly and
matched:
- `_rebuild_feed(supabase, target, produced_yt, produced_x) -> int` ✓
- `_count_existing_source_rows` exists ✓ (not exercised directly — the 3 tests
  drive `_rebuild_feed`, the smallest entrypoint that exercises both SP1 + SP2; the
  empty-produce → `_count_existing_source_rows` seam lives in `_main`, which is
  async + does live `create_client`/`load_dotenv` and is out of scope to drive
  here. The carry-forward + guard logic it routes into IS fully covered.)
- SP1 log `source_rows_carried_forward` ✓ (emitted on the carry-forward path
  exercised by test 1; not asserted on directly — test 1 asserts the stronger
  observable OUTCOME, survival of the source ids, per Rule 9).
- SP2 abort: exit `1`; `feed_rebuild_aborted` with `fail_loud`,
  `current_source_row_count`, `new_source_row_count`, `backup_path`, `feed_date` ✓
- Backup glob `/tmp/ash_feed_backup_<date>_*.json` ✓ (real pattern is
  `_<epoch>_<pid>[_<n>].json`; the `_*` glob matches).

### One implementation note on log capture
structlog's stdlib JSON handler does NOT propagate cleanly to pytest's `caplog`
(an empty-records probe confirmed it). So the abort test spies on
`mod.logger.error` at the call boundary (monkeypatch) to capture the event name +
structured fields deterministically — still asserts the exact `feed_rebuild_aborted`
event and all five documented fields. This is the CLAUDE.md "mock at the boundary"
strategy, not a weakening.

## Validation results
```
.venv/bin/pytest tests/scripts/test_produce_source_reels_rebuild.py -v
test_source_reels_survive_empty_second_run PASSED
test_two_runs_produce_distinct_backups       PASSED
test_shrinking_rebuild_aborts_without_delete PASSED
3 passed, 1 warning in 0.57s
.venv/bin/ruff check ... → All checks passed!
```

## Rule-9 revert-would-fail proof (performed, then reverted)
I temporarily applied all three reverts to `scripts/produce_source_reels.py`
(restored immediately after, verified clean — no `REVERT` markers remain, file
back to the SP1/SP2 baseline):
1. SP1 carry-forward → old `continue` drop of source rows.
2. SP2 guard → `if False and ...` (always write).
3. SP2 backup name → fixed `ash_feed_backup_<date>.json` (clobbering).

Result with reverts applied:
```
test_source_reels_survive_empty_second_run   FAILED
test_two_runs_produce_distinct_backups       FAILED
test_shrinking_rebuild_aborts_without_delete FAILED
3 failed
```
All three tests fail on revert → they encode the survival INTENT, not just
mechanics. File restored; the 3 tests are green again against the real code.

## Definition of done: PASS
- `pytest tests/scripts/test_produce_source_reels_rebuild.py` green ✓
- Test FAILS if SP1/SP2 reverted ✓ (proven above, all 3)

## Concerns for the orchestrator
- A pre-existing stray `/tmp/ash_feed_backup_2026-06-18.json` (no suffix, dated
  Jun 18) exists from SP1/SP2's throwaway inline checks — NOT created by my test
  and not matched by my glob pattern. Left untouched (Rule 3); harmless.
- `_count_existing_source_rows` and `_main`'s empty-produce early-return seam are
  not directly unit-tested (async + live-client entrypoint). The logic they route
  into (`_rebuild_feed` carry-forward + guard) is fully covered. If the
  orchestrator wants the seam covered too, that needs a separate `_main`-level test
  with `create_client`/`load_dotenv`/ingestion all mocked — larger scope than this
  sub-phase.

## Return to orchestrator
1. **STATUS:** SUCCESS
2. **Files touched:** `tests/scripts/test_produce_source_reels_rebuild.py` (new)
3. **Validation:** PASS — 3/3 green, ruff clean, no `/tmp` litter added.
4. **Definition of done:** PASS — green AND fails-on-revert proven.
5. **Concerns:** pre-existing stray /tmp backup (not mine); `_main` empty-produce
   seam not directly tested (logic it routes into is covered).
