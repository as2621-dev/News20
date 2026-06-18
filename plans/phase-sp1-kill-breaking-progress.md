# Progress: phase-sp1-kill-breaking

**Phase file:** plans/phase-sp1-kill-breaking.md
**Started:** 2026-06-18
**Base commit (pre-phase):** WIP snapshot (working tree clean)

## Sub-phase progress
- [x] 1: Remove breaking from the Python pipeline — COMPLETED (5 files; ruff clean; 12 stale tests + run_live_batch.py:448 deferred to SP4)
- [x] 2: Remove breaking from the frontend (TS) — COMPLETED (3 files; in-scope clean; out-of-scope consumers ArticleLayer/ReelStage/fixtureFeed/supabaseFeed deferred to SP4)
- [x] 3: Data migration + enum decision — COMPLETED (0017_drop_breaking_allocation.sql authored; LIVE APPLY DEFERRED to human checkpoint)
- [x] 4: Update sims + tests, verify end-to-end — COMPLETED (pytest 262 pass; npm build green; npm test 466 pass / 1 pre-existing unrelated tabBar fail)

## Status: COMPLETE — committed 12380e2

## Notes
- Execution mode: SEQUENTIAL (SP3 irreversible).
- SP3 live `db push` DEFERRED to an explicit user checkpoint (per session agreement).

## PENDING LIVE APPLY (batched to end-of-run checkpoint)
- `supabase/migrations/0017_drop_breaking_allocation.sql` — apply via session pooler `:6543`, then verify `select count(*) from user_feed_allocation where allocation_category='breaking'` = 0.
- MUST land before the next live daily batch (CategoryAllocation now rejects a 'breaking' row at the boundary).

## Known unrelated red (NOT this phase)
- `tests/lib/app/tabBar.test.tsx` expects 4 tabs; a "Thirty" tab was added in earlier (now WIP-committed) work. One-line fix available; left out of SP1's scope.
