# Phase SP2 — Sub-phase 2 execution report

**Sub-phase:** Fail-safe write + non-clobbering backup
**Status:** SUCCESS

## What I implemented (the three changes)

### 1. Route empty-produce into the safe rebuild (the SP1 seam)
`_main`'s `if not produced_yt and not produced_x:` block (was a blind
`return 1`) now:
- calls a new helper `_count_existing_source_rows(supabase, target)`,
- early-returns `1` ONLY when `prior_source_row_count == 0` (genuinely nothing
  to preserve — a no-op return is correct),
- otherwise **falls through into `_rebuild_feed`** so SP1's carry-forward runs
  and the prior source reels survive.

New helper `_count_existing_source_rows` does a lightweight
`select("feed_slot_kind").eq(user).eq(date)` and counts `feed_slot_kind ==
"source"` rows.

### 2. Fail-safe write guard (no delete on source regression)
Immediately before the `delete().eq().eq()` + `insert(new_rows)` (~373), the
rebuild now counts source rows in the CURRENT feed (`existing`) vs. the proposed
`new_rows`. If `new_source_row_count < current_source_row_count` it:
- logs a structured `logger.error("feed_rebuild_aborted", fail_loud=True, ...)`
  with `current_source_row_count`, `new_source_row_count`, `backup_path`,
  `feed_date`, and a `fix_suggestion`,
- prints a loud `FAIL: feed_rebuild_aborted ...`,
- `return 1` **before any delete or insert** — `daily_feeds` left untouched.

### 3. Non-clobbering backup path
The `/tmp` backup (was fixed `ash_feed_backup_<date>.json` opened `"w"`) is now
`ash_feed_backup_<date>_<epoch>_<pid>.json`, with a `while os.path.exists(...)`
counter-bump fallback so even a same-process / same-second double rebuild yields
two distinct files. `import time` was added.

## Files modified
- `scripts/produce_source_reels.py` (only this file)

## How I resolved SP1's early-return seam
SP1 flagged that `_main` early-returns `1` at ~217–219 when both produced lists
are empty, so the live path never reached the carry-forward. I did NOT delete
the early return (a truly-empty feed with no source rows should still no-op).
Instead I made it **conditional on prior source rows**: query the current source
count first; return early only if it's `0`; otherwise route into `_rebuild_feed`
so SP1's carry-forward refills the source slots from the carried rows. This keeps
the normal produce path (non-empty produce) completely unchanged — it skips the
whole `if` and goes straight to the rebuild as before.

## Divergences + why
- **Extra `daily_feeds` query** in the empty-produce path
  (`_count_existing_source_rows` then `_rebuild_feed`'s own `existing` select).
  Chosen over threading the count/rows through `_rebuild_feed`'s signature to
  keep the change surgical (Rule 3) and avoid touching SP1's logic. Cost: one
  extra cheap select, only on the empty-produce branch.
- **Backup suffix uses `time` + pid + counter**, not uuid. The task allowed
  `int(time.time())` or `os.getpid()`+counter; I combined epoch+pid+counter for
  a monotonic, collision-free name without a new import beyond `time` (already
  the simplest robust option). NOTE the task said the workflow forbids
  `time.time()`-via-`Date` "in some contexts" but explicitly OK'd it in a plain
  Python script — this is a plain script.

## Code-review findings + fixes
- **(low) Double query on empty-produce** — accepted (surgical; only on the
  empty branch). No fix.
- **(low → fixed) Same-process/same-second backup collision** — first cut used
  `os.path.exists` → pid fallback, but two runs in ONE process share a pid and
  would reuse the same name. Hardened to a `while os.path.exists` counter bump so
  the DoD ("two distinct files") holds unconditionally. Re-validated.
- **(documented) Legitimate-shrink edge case** — the abort-on-`new < current`
  guard could in principle block a user who genuinely UNFOLLOWED a source. Out of
  scope here: this script has no unfollow path (it only ingests *followed*
  sources), so a strict-less-than abort cannot deadlock a legitimate shrink in
  this script. Documented inline at the guard. A future intentional-shrink caller
  must take a different path.
- Verified the guard does NOT fire on the normal carry-forward case (empty
  produce + prior rows → SP1 refills → `new == current` → write proceeds).

No outstanding critical/high.

## Validation results
- `.venv/bin/ruff check scripts/produce_source_reels.py` → `All checks passed!`
- `ast.parse(...)` → `SYNTAX OK`
- **Inline mocked-supabase checks (throwaway, not committed):**
  - **CASE a** — empty produce + 2 prior source rows (`sp3-yt1`, `sp3-x1`) →
    `rc=0`, delete happened (legitimate non-regressing rebuild), both source
    reels present in `inserted` at source positions. PASS.
  - **CASE guard** — empty produce, allocation with zero youtube/x budget (forces
    a source shrink 2→0) → `rc=1`, `deleted=False`, `inserted=None`, structured
    `feed_rebuild_aborted` + `fail_loud=True` logged. PASS.
  - **CASE b** — two consecutive rebuilds → two distinct `/tmp` backup files.
    PASS (and re-verified for same-process/same-second after the hardening).
  - `_count_existing_source_rows` returns `2` for the 2-source fixture. PASS.
- Did NOT run the live producer. Did NOT create the formal test file (SP4 owns it).

## Definition of done: PASS
- **Empty produce leaves `daily_feeds` unchanged (no shrink-delete):** verified
  by CASE a (rows preserved, write proceeds without losing source reels) and
  CASE guard (on a would-be regression the delete is skipped and rc is non-zero
  with a `feed_rebuild_aborted` structured log).
- **Two consecutive runs → two distinct backup files in `/tmp`:** verified by
  CASE b and the same-second hardening re-check.

## Concerns for the orchestrator and SP4

**Exact names SP4's regression test should assert on:**
- Function under test: `_rebuild_feed(supabase, target, produced_yt, produced_x)`.
- New helper SP4 may exercise for the early-return seam:
  `_count_existing_source_rows(supabase, target) -> int`.
- **Abort exit code:** `_rebuild_feed` returns `1` (and `_main` returns `1`) on
  a source regression.
- **Abort log event:** `feed_rebuild_aborted` (level `error`) with fields
  `fail_loud=True`, `current_source_row_count`, `new_source_row_count`,
  `backup_path`, `feed_date`, `fix_suggestion`.
- **Carry-forward log event (SP1):** `source_rows_carried_forward` with
  `carried_source_row_count`, `feed_date`.
- **Backup filename pattern:** `/tmp/ash_feed_backup_<date>_<epoch>_<pid>[_<n>].json`
  — SP4 should glob `/tmp/ash_feed_backup_<date>_*.json` and assert the count
  increases by the number of rebuilds (not a fixed name). Mock must NOT mock the
  real `open`/`os.path.exists` if it wants to assert file distinctness, OR assert
  on the printed/derived `backup_path` instead.

**Mock shape (extends SP1's):** in addition to SP1's `daily_feeds` /
`user_feed_allocation` / `interests` selects, the mock must serve a
`daily_feeds.select("feed_slot_kind").eq().eq().execute().data` for
`_count_existing_source_rows`, and must let `daily_feeds.delete()...execute()` /
`daily_feeds.insert(rows).execute()` be *observable* (a flag/capture) so the test
can assert "no delete, no insert" on the abort path.

**Guard semantics SP4 should encode (Rule 9 — intent):** the test must FAIL if
the guard is removed — i.e. simulate run-1 (N source reels) → run-2 (empty pool,
allocation that would drop source slots) and assert (a) no delete fired and (b)
exit `1` with `feed_rebuild_aborted`. The *normal* empty-produce-with-carry-
forward case must still write (rc 0) — both branches matter.

## Return to orchestrator
1. **STATUS:** SUCCESS
2. **Files touched:** `scripts/produce_source_reels.py`
3. **Validation:** PASS (ruff clean, ast OK, 4 inline mocked-supabase cases pass)
4. **Definition of done:** PASS
5. **Concerns:** double-query on empty branch (accepted, surgical); legitimate-
   unfollow shrink is out-of-scope-and-documented; SP4 must assert on the exact
   names above and observe delete/insert to prove the no-shrink intent.
