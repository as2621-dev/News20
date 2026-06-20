# Execution report — phase-m3b-online-clusterer, Sub-phase 2

**Sub-phase:** Assign-or-spawn decision + running-mean centroid update
**Status:** SUCCESS

## What I implemented
`agents/pipeline/clustering/assign.py` — three pure functions + constants:
- `DEFAULT_TAU_ASSIGN = 0.75`, `NO_MATCH_SCORE = -1.0` (sentinel; strictly below any real cosine so `should_assign(NO_MATCH_SCORE)` is always False → orchestrator spawns).
- `best_match(candidate_embedding, block_clusters) -> (StoryCluster|None, float)` — max `cosine_similarity` (reused from `embeddings.py`) over each cluster's centroid, **skipping** clusters with `cluster_centroid is None`. Returns `(None, NO_MATCH_SCORE)` for empty block or all-null centroids.
- `should_assign(score, *, tau_assign=DEFAULT_TAU_ASSIGN) -> bool` — inclusive boundary (`score >= tau_assign`).
- `update_centroid_running_mean(old_centroid, old_count, new_embedding) -> list[float]` — `((old*n)+new)/(n+1)` component-wise then re-L2-normalize. Validates non-empty / equal-length / `old_count >= 1`.
- Private `_l2_normalize` replicated inline (per instructions; `embeddings._l2_normalize` is private), zero-norm guard matches `embeddings.py` behavior.

Pure Python, no numpy. Verbose names, full type hints, Google-style docstrings with examples, double quotes, 4-space indent, line length 120.

## Files created
- `agents/pipeline/clustering/assign.py`
- `tests/agents/pipeline/clustering/test_assign.py`

## Divergence from the DoD wording (surfaced, Rule 7/12) — IMPORTANT
DoD (c) reads: "running-mean of three unit vectors added one at a time **equals the batch mean direction**". This is **mathematically false for a chain of folds when each fold re-L2-normalizes** (which the spec for `update_centroid_running_mean` mandates). Re-normalizing the centroid to unit length after every fold discards magnitude, so older members get rescaled-up each step; a 3-fold chain does NOT equal the unweighted batch mean. Verified empirically: incremental `[0.632, 0.632, 0.447]` vs batch direction `[0.577, 0.577, 0.577]`.

The clean property (`incremental == batch direction`) holds **exactly for a single fold** (no intermediate normalization to perturb it) — verified: two orthogonal unit vectors → `[0.7071, 0.7071]` both ways.

Resolution (did NOT weaken the function — the spec's re-normalize-every-fold is kept verbatim): I split the test into two that assert the *real* business intent:
1. `test_single_fold_equals_batch_mean_direction` — pins the exact mean for one fold (the precise property).
2. `test_centroid_tracks_all_members_not_just_last` — after folding v2 then v3, the centroid retains a substantial pull along v1, v2 AND v3 (not collapsed onto the last vector), and `‖v‖≈1`. This is the downstream-meaningful invariant. Its docstring documents the re-normalization caveat for the orchestrator.

This is a documentation/precision fix to the DoD's math claim, not a logic change. Flagged for the orchestrator in case M4's MMR or M6 tuning assumes an unweighted mean.

## Self code-review findings + fixes
- `best_match` first-cluster init: uses `best_cluster is None or score > best_score`, so the first non-null cluster always initializes regardless of the `-1.0` sentinel, and `>` (not `>=`) makes selection deterministic (first max wins). Correct — no fix needed.
- Sentinel choice `-1.0`: below the valid cosine floor for unit vectors, so it can never be mistaken for a real score and never assigns. Kept.
- Zero-norm guard in `_l2_normalize`: returns input unchanged (matches `embeddings.py`); documented. No high/critical issues found.

## Validation results
- `pytest tests/agents/pipeline/clustering/test_assign.py -q` → **15 passed**.
- `ruff check assign.py test_assign.py` → **All checks passed**.
- Full suite `pytest tests/agents/pipeline/clustering/ -q` → **33 passed** (M3a 18 + blocking/assign/etc.; no regressions).
- One fix-and-rerun cycle used (the DoD-(c) math correction above), well within the 2-attempt bound.

Note: the worktree has no local `.venv`; I ran the project interpreter at `/Users/asheshsrivastava/News20/News20/.venv/bin/python` with cwd in the worktree so the worktree's source is imported. Orchestrator may want to confirm that's the intended interpreter.

## DoD check
- (a) cos≈0.9 vs cos≈0.2 block → best_match returns the 0.9 cluster + its score — **PASS**
- (b) should_assign True at 0.75 and just above, False just below — **PASS**
- (c) running mean L2-normalized (‖v‖≈1) + tracks all members — **PASS** (with the equals-batch-direction claim corrected to single-fold; chain caveat documented)
- (d) empty block and all-null-centroid block → (None, sentinel) — **PASS**
- pytest green + ruff clean — **PASS**

**Definition of done: PASS** (with the surfaced precision correction to claim (c)).

## Concerns for orchestrator
1. **(c) math claim**: re-normalize-every-fold ≠ unweighted batch mean over a chain. Confirm no downstream consumer (M4 MMR / M6 τ-tuning) assumes an unweighted running mean. If a true unweighted mean is ever needed, the centroid would have to be stored un-normalized and normalized only at compare time — a schema/contract change, out of scope here.
2. **Interpreter**: no `.venv` in the worktree; used the main worktree's venv. Tests are env-independent (pure math + pydantic), so this is low risk.
