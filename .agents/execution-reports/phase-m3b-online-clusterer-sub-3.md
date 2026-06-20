# Phase M3b — Sub-phase 3: Online clustering orchestrator — Execution Report

**Status:** SUCCESS
**Date:** 2026-06-20

## What shipped

`async def cluster_candidates(...) -> ClusterRun` in `agents/pipeline/clustering/online_clusterer.py`, composing the M3a/M3b primitives into the four-step §2C pipeline:

1. **Near-dup prefilter** — `group_near_duplicates` over `NearDupItem`s built from `input_text`; smallest `input_index` per group is the representative, the rest fold in as members.
2. **Embed** — only representative texts via `embed_texts` (one paid vector per kept story), aligned 1:1.
3. **Block + match** — `select_block(existing + spawned-this-run, ...)` then `best_match`.
4. **Assign or spawn** — `should_assign` joins (append members, fold centroid via running mean, bump counts, advance `last_seen`) or spawns (mint id, centroid = rep embedding).

## Files created

- `agents/pipeline/clustering/online_clusterer.py`
- `tests/agents/pipeline/clustering/test_online_clusterer.py`

(No other files touched. No commit.)

## Key design decisions / divergences

- **`ClusterRun.clusters` contains spawned AND touched-existing clusters** (untouched existing excluded), tracked in a `dict[id -> StoryCluster]`. SP4 persistence upserts exactly this set, which is correct: a joined existing cluster must be re-persisted with its new centroid/counts/`last_seen`.
- **Existing clusters are mutated in place** when joined (they are the same objects the caller passed and the same objects placed in the run). Intended — SP4 re-persists them.
- **Centroid folds once per representative**, weighted by the pre-join `cluster_member_count`, BEFORE the count is bumped — keeps the running mean consistent with the count. Dropped reprints share the rep's vector and are NOT re-folded.
- **`cluster_outlet_count` on join uses `max(prior_count, distinct_this_run)`.** Prior members of a cross-day existing cluster are not in this run's `members` list, so a pure recompute over this-run members would REGRESS outlet diversity. The `max` floor prevents that. Spawn uses the pure distinct count (all its members are present). Null outlets are excluded from the distinct count.
- **Determinism:** representatives processed in ascending `input_index` order → reproducible id minting, fold order, and `clusters` ordering.
- **Logging** matches the sibling convention (`get_logger("pipeline.clustering.online_clusterer")`, snake_case events, counts only — no text logged).

## Self-review findings + fixes

- **Outlet-count drift on cross-day join (medium):** caught in review — fixed with the `max(prior, this-run-distinct)` floor described above. Without it, an existing cluster joined today would have its `cluster_outlet_count` silently recomputed from only today's members and could drop below its persisted value.
- No critical/high issues. Type hints, Google docstrings with examples, 120-col, double quotes, 4-space indent all conform.

## Validation

- `pytest tests/agents/pipeline/clustering/test_online_clusterer.py -q` → **6 passed**
- `pytest tests/agents/pipeline/clustering/ -q` → **49 passed** (43 baseline + 6 new; NO regression)
- `ruff check online_clusterer.py test_online_clusterer.py` → **All checks passed**
- `embed_texts` patched with an `AsyncMock` (side_effect mapping text → deterministic 3-d unit vector); `mint_cluster_id` is an injected counter. No real Gemini/DB call.

## Definition of done: PASS

- (a) two byte-near-dup inputs → ONE cluster, two members, ONE id minted, embed called with one text ✅
- (b) two cos≥τ inputs → ONE cluster, centroid moved toward the mean and stays L2-normalized ✅
- (c) orthogonal input → SPAWNS a second cluster ✅
- (d) input matching a passed-in existing centroid (in-window) → JOINS across the day boundary, NO id minted, `last_seen`/`member_count` advanced ✅
- (e) `cluster_outlet_count` counts DISTINCT outlets (3 members / 2 outlets → 2) ✅
- Plus an empty-batch edge test (returns empty run, never embeds).

## Concerns for the orchestrator / SP4

1. **`ClusterRun.clusters` = spawned + touched-existing** (documented in the module docstring). SP4's `persist_run` should upsert every cluster in this list; touched-existing carry mutated centroid/counts/`last_seen`.
2. **Outlet-count is a floor for cross-day joins, not an exact recount.** Because prior members aren't loaded into memory, the engine cannot recompute the *true* distinct-outlet count across all-time members. If SP4/M3c needs an exact count it must recompute DB-side from `story_cluster_members`. The current value never regresses but may undercount if a brand-new outlet today duplicates one already in the cluster's history (it would count it as new). Low impact for blocking/ranking; flagged.
3. **`input_cluster_map` keys only representatives** (dropped reprints have no entry, by design — their URL still becomes a member row, so SP4's URL→story_id continuity sees them).
4. **`llm_client` is forwarded untouched to `embed_texts`** and unused on the empty-batch path; SP4 passes the real shared `LLMClient`.
5. No migration applied; no real Gemini/Supabase call in any test.
