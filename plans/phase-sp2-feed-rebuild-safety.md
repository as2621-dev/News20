# Phase SP2: Feed-rebuild safety (the Nitish-eviction bug)

**Milestone:** M1 — Kill breaking + taxonomy cleanup (`plans/shared-pool-rework-master-plan.md`)
**Status:** Not started
**Estimated effort:** S

## Goal
A re-run of the source-reel producer (or the e2e allocator) can **never silently evict a previously-produced reel** from a user's `daily_feeds`: existing `feed_slot_kind='source'` rows survive a short/empty produce run, the write is fail-safe, and a regression test proves it.

## Context for the executor
Diagnosed 2026-06-18 (read-only against prod): `scripts/produce_source_reels.py` `_rebuild_feed` (lines ~225–356) does a **non-transactional `delete().eq(user).eq(date)` then `insert(new_rows)`** (lines ~343–346) and **drops existing source rows** at lines ~275–276 (`if row.get("feed_slot_kind") == "source": continue`), re-placing source slots ONLY from reels produced *this* run (`produced_yt`/`produced_x`). So a re-run where the source pool came back empty — a 6h cadence skip (no `FORCE_REINGEST=1`), an RSS throttle, or a single-source verification halt — wipes the prior source reels and back-fills with topic reels. The `/tmp/ash_feed_backup_<date>.json` is opened `"w"` (overwrites same-day), so the only backup is destroyed on the second run. A second destructive path is `scripts/e2e/allocate_test_feeds.py` (~318–326): it deletes the user's day then rewrites from `assemble_user_feed` with **zero source-reel awareness**. NOTE: this phase is a *safety guard*, not a feature — keep changes surgical (Rule 3).

## Sub-phases

### Sub-phase 1: Preserve + merge existing source rows in `_rebuild_feed`
- **Files touched:** `scripts/produce_source_reels.py` (the `_rebuild_feed` bucketing at ~273–280 and the source-slot fill at ~306–315).
- **What ships:** existing `feed_slot_kind='source'` rows are **carried forward** (keyed by `feed_story_id`) and merged with this run's `produced_yt`/`produced_x` instead of being dropped — a story already produced is reused (it already does `sp3-<id>` reuse), and a slot is never left empty if a prior source reel exists for it.
- **Definition of done:** a unit/integration test (mock supabase) where the produced lists are EMPTY but prior `source` rows exist → the rebuilt `new_rows` still contains those prior source rows at their source positions (none replaced by topic reels).
- **Dependencies:** none

### Sub-phase 2: Fail-safe write + non-clobbering backup
- **Files touched:** `scripts/produce_source_reels.py` (the backup write ~249–252 and the delete+insert ~343–346).
- **What ships:** the rebuild **aborts (logs `fail_loud`, non-zero exit) instead of deleting** when `new_rows` would have fewer source reels than currently exist; the `/tmp` backup path is **timestamped/suffixed** (e.g. `ash_feed_backup_<date>_<epoch>.json`) so a same-day re-run never overwrites the prior backup; the delete+insert is guarded (no delete if the new set is a strict regression).
- **Definition of done:** running the rebuild with an empty produce set leaves `daily_feeds` unchanged and exits non-zero with a structured `feed_rebuild_aborted` log; two consecutive runs leave two distinct backup files in `/tmp`.
- **Dependencies:** Sub-phase 1

### Sub-phase 3: Make `allocate_test_feeds.py` source-aware (or refuse real users)
- **Files touched:** `scripts/e2e/allocate_test_feeds.py` (~214–216 `_category_for_slot`, ~318–326 delete+write).
- **What ships:** after the from-scratch `assemble_user_feed`, existing `feed_slot_kind='source'` rows for that user/date are **re-placed** into the new feed; OR the script **refuses to run against a non-test user** (a UID/email allowlist; ash `b316800d-…` is NOT a test user) with a loud error.
- **Definition of done:** invoking the script for ash either preserves his source rows (asserted by a test) or aborts with `refuse_real_user`; invoking for a seeded test profile still rebuilds normally.
- **Dependencies:** none (different file — parallel-safe with SP1/SP2)

### Sub-phase 4: Regression test — two-run source survival
- **Files touched:** new `tests/scripts/test_produce_source_reels_rebuild.py` (or nearest existing test dir for scripts).
- **What ships:** a test that simulates run-1 (produces N source reels) → run-2 (empty pool) against a mocked supabase and asserts all N source reels survive run-2 and the backup files don't collide.
- **Definition of done:** `pytest tests/scripts/test_produce_source_reels_rebuild.py` is green; the test FAILS if SP1/SP2 are reverted (i.e. it encodes the intent, not just the mechanics — Rule 9).
- **Dependencies:** Sub-phases 1, 2

## Phase-level definition of done
Re-running source production with an empty/short source pool leaves all prior source reels intact (no topic-reel back-fill eviction); `allocate_test_feeds.py` can't silently wipe a real user's source reels; backups never clobber; the new regression test is green and encodes the survival intent. Single commit at phase end.

## Out of scope
- Fixing today's already-broken feed for ash (that's a one-off reassembly, done manually after this phase makes reassembly safe).
- The broader move off `produce_source_reels.py` to the shared-pool path (M3+).
- Tuning the verification gate that empties the pool (that's Phase SP-source / M5).

## Open questions
1. **Abort vs. preserve-and-continue** when produce is short: recommend **preserve prior source rows AND continue** (SP1) so the feed stays full, with abort (SP2) only as the last resort when even prior rows can't fill the slots. Confirm at run-time.
2. **Real-user allowlist for `allocate_test_feeds.py`:** refuse-by-default vs. source-aware-rebuild — recommend source-aware so the e2e harness still works on real-ish profiles.

## Self-critique

**Product lens:** PASS. Directly protects the MVP's "finite 30 = caught up" loop and the followed-source reels (in-brief personalization). No new feature, no scope creep. Not the rework's riskiest assumption (that's clustering, M3) — this is a safety prerequisite that makes the taxonomy reassembly (SP3) runnable without data loss, so it correctly comes first.

**Engineering lens:** PASS. Every DoD is fresh-context checkable (a pytest assertion on mocked-supabase `new_rows`, a non-zero exit + structured log, two backup files present). SP1 (merge logic) and SP2 (write guard + backup) touch the same function but are distinct outcomes (preserve rows vs. fail-safe write) — kept separate with an explicit dependency. SP3 is a different file (parallel-safe). SP4 locks nothing premature — it's the verification.

**Risk lens:** PASS with flags. **File-boundary:** SP1+SP2 both edit `_rebuild_feed` → SP2 depends on SP1 (marked) to avoid a write conflict; SP3 + SP4 are separate files. **Reversibility:** no DB migration, no destructive change — this phase REMOVES a destructive behavior. **Test coverage:** SP4 is the Rule-9 test; SP1's DoD is also a test. **Painting-into-a-corner:** 1→2→3→4 simulated — SP1 makes rows survive, SP2 guards the write, SP3 fixes the other path, SP4 proves it; no corner.

**Irreversible sub-phases:** none.
