# Progress: phase-m3b-online-clusterer

**Phase file:** plans/phase-m3b-online-clusterer.md
**Started:** 2026-06-20

## Execution mode
PARALLEL SP1∥SP2 (independent, disjoint files), then SP3 (deps SP1+SP2), then SP4 (deps SP3).

## Worktrees
- Sub-phase 1: ../News20-sub-1
- Sub-phase 2: ../News20-sub-2

## Sub-phase progress
- [x] 1: Engine I/O models + time-window blocking — MERGED (10 tests + doctests green; 48h edge INCLUSIVE; ClusterRun.input_cluster_map = kept input_index→cluster_id)
- [x] 2: Assign-or-spawn decision + running-mean centroid — MERGED (15 tests green; FLAG: re-L2-norm each fold ⇒ multi-fold chain ≠ unweighted batch mean (true only single-fold); function kept as spec'd, test asserts real invariant. Confirm M4 MMR / M6 don't assume unweighted mean.)
- [x] 3: Online clustering orchestrator — DONE (6 new tests, 49 total no-regression, ruff clean, DoD PASS; ClusterRun.clusters = spawned+touched-existing; outlet_count on join = non-regressing floor)
- [x] 4: Cross-day id bridge + persistence wiring — DONE (7 tests, 56 total no-regression, ruff clean, DoD PASS; reuses agents.ingestion.dedup.normalize_url verbatim — parity tested; multi-id tie-break = min(existing_ids); run_and_persist entry point added, NOT yet wired into daily_batch (M3c))

## Merge checkpoint
SP1+SP2 merged to main worktree (disjoint new files, conflict-free). Combined: `pytest tests/agents/pipeline/clustering/ -q` → 43 passed; ruff clean. Worktrees torn down.
- [ ] 4: Cross-day id bridge + persistence wiring — PENDING
