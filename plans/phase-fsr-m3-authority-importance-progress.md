# Progress: phase-fsr-m3-authority-importance

**Phase file:** plans/phase-fsr-m3-authority-importance.md
**Started / shipped:** 2026-06-30
**Branch:** claude/feed-source-revamp-plan-388edf

## Test-env note
pytest runs from the uv tool env `/root/.local/share/uv/tools/pytest/bin/python`.
No new deps needed for this phase (pure math over fixtures). Baseline carried ~18
pre-existing orchestrator/clustering/poster failures (missing ffmpeg/PIL/datasketch)
— confirmed identical before and after this phase; NOT this phase's concern.

## Sub-phase progress
- [x] 1: Source-tier authority table (Python config + lookup) — COMPLETED
- [x] 2: E1 `story_importance` un-normalized terms + combine — COMPLETED
- [x] 3: Within-category normalization + `score_clusters` wiring — COMPLETED
- [x] 4: Raise β (0.3 → 0.45) + route ranking importance through E1 — COMPLETED

## STATUS: PHASE SHIPPED

Implements the **existing** shared-pool E1 `story_importance` model
(`reference/shared-pool-pipeline.md` §4, PRD Decision #5) — NOT a fork (Rule 7).
No DB migration (0018 already provides the columns). The authority table is a pure
Python config artifact.

### Files added/changed
- `agents/pipeline/importance/__init__.py` (new package)
- `agents/pipeline/importance/source_tiers.py` (new) — `outlet_authority` +
  `authority_and_diversity` (SP1)
- `agents/pipeline/importance/story_importance.py` (new) — `compute_story_importance_terms`,
  `combine`, `normalize_importance_within_category`, `score_clusters` (SP2/SP3)
- `agents/pipeline/stages/ranking.py` — `IMPORTANCE_WEIGHT` 0.3 → **0.45**;
  `compute_story_score` gains an optional `cluster_importance` param that REPLACES the
  raw `min(1, outlet_count/12)` when the story is clustered, else falls back (additive
  seam, Rule 3) (SP4)
- `agents/pipeline/sim/world.py` — entity-boost twin pair outlet_count 5 → 16 (see
  regression note)
- `tests/agents/pipeline/importance/` (new) — `test_source_tiers.py`,
  `test_story_importance.py`
- `tests/agents/pipeline/test_ranking.py` — new `TestImportanceWeightFlip` (pins β)

### β value pinned: 0.45
SP4 flip test (`TestImportanceWeightFlip`) derives the threshold: with a fixture pair
(big story high E1 importance / modest affinity×depth vs minor story high affinity×depth
/ low E1 importance) the ordering flips at β ≈ 0.4118. At OLD β=0.3 the minor wins
(reproduces the diagnosed bug); at NEW β=0.45 the big wins. The live constant test
fails if β drifts below 0.45. α=0.5 stays affinity-dominant overall.

### Regression surfaced + fixed (Rule 12)
Raising β broke `test_ranking_simulation::test_entity_follow_lifts_story_above_twin_within_category`:
the entity-boost twins (outlet_count 5) were calibrated to both clear the 4-slot
business budget at β=0.3. Raising β reorders a category by Importance, so a
mid-importance twin fell out of the budget, making the EntityBonus invariant
unobservable. Fix: set BOTH twins to outlet_count=16 (cycle max) so both stay
top-importance and clear the budget under any β ≥ 0.3 — preserving the invariant's
intent (the bonus is still the sole differentiator BETWEEN the equal twins). This is a
fixture-data calibration, not an EntityBonus-logic change.

### DoD: PASS
`tests/agents/pipeline/importance/ tests/agents/pipeline/test_ranking.py
tests/agents/pipeline/test_produce_gate.py tests/agents/pipeline/test_ranking_simulation.py`
= 65 passed. Full `tests/agents/pipeline/` (excluding the 3 missing-dep
collection-error modules) = 353 passed, 18 pre-existing failures only.

### Entity-prominence term (graceful degrade — Rule 12)
`StoryCluster` carries NO entity column today (0018 has none). The E1 `entity` term is
an INJECTED optional `entity_prominence` per cluster, defaulting to 0.0 — so importance
is correct (entity contributes 0) until the clusterer threads an entity-prominence
signal in. Wired, not faked.

### LIVE-E2E residual (deferred, NOT run — offline sandbox)
Importance-ordering sanity on a REAL day's clustered pool: confirm E1 ranks the day's
genuinely big stories above minor well-matched ones end-to-end through `daily_batch` →
`cluster_store` → assembly → `daily_feeds`. Requires GDELT egress + Supabase creds
(absent). Plus: `score_clusters` is wired but not yet CALLED from `daily_batch`, and
`compute_story_score`'s `cluster_importance` param is not yet THREADED from the
cluster↔story bridge at assembly — that plumbing + the live re-score are the M6b
(feed assembly/ranking) residual.

### Concerns flagged for downstream phases
- **M6b (feed assembly/ranking):** must (a) call `score_clusters` in `daily_batch`
  after clustering and persist `cluster_importance` via `cluster_store.upsert`, and
  (b) thread the cluster's E1 `cluster_importance` onto the scored candidate at the
  `cluster_id ↔ story_id` bridge so `compute_story_score(..., cluster_importance=...)`
  actually receives it. Today the seam exists and defaults to the raw outlet count.
- **M4-consolidation:** the authority weights here (`SOURCE_TIER_BY_DOMAIN` +
  `TIER_WEIGHT`) and M4's curated trusted-outlet domain set are TWO related but
  separate config artifacts. They should be reconciled (a domain trusted enough to
  fetch from should not be a content-farm tier) — flagged as a content-ops tuning
  consolidation, not a blocker.
