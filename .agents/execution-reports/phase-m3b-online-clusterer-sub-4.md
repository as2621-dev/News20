# Execution report — phase-m3b-online-clusterer, Sub-phase 4

**Sub-phase:** 4 — Cross-day id bridge + persistence wiring (final sub-phase)
**Status:** SUCCESS
**Date:** 2026-06-20

## What I implemented

### `agents/pipeline/clustering/continuity.py` (new)
- `resolve_cluster_story_ids(run, *, resolve_existing_story_ids, mint_story_id) -> dict[str, str]`
  - Groups each cluster's member URLs, normalizes each with the canonical
    `agents.ingestion.dedup.normalize_url`, calls the injected resolver ONCE over the
    union of all normalized URLs.
  - If ANY of a cluster's normalized member URLs already aliases to an existing
    `story_id`, REUSES it (cross-day continuity). Else mints via `mint_story_id()`.
  - Tie-break for a cluster spanning >1 existing story id: picks `min(existing_ids)`
    (lexicographically smallest), logged at `resolve_cluster_story_ids_multi_id_tiebreak`.
- `persist_run(client, run) -> None`
  - Upserts each cluster (`cluster_store.upsert_cluster`), groups members by
    `cluster_id`, and calls `cluster_store.add_cluster_members` once per cluster.

### `agents/pipeline/clustering/online_clusterer.py` (extended — SP3 logic untouched)
- Added `async def run_and_persist(candidates, *, llm_client, client, existing_clusters,
  mint_cluster_id, mint_story_id, resolve_existing_story_ids, tau_assign=..., window_hours=...)
  -> tuple[ClusterRun, dict[str, str]]` — a thin sequencer: `cluster_candidates` →
  `resolve_cluster_story_ids` → `persist_run`. Returns the run + the
  cluster_id→story_id map so M3c has ONE entry point. Added `Any` to the `typing` import
  and a `continuity` import. SP3's `cluster_candidates` and helpers are unchanged.

### `tests/agents/pipeline/clustering/test_continuity.py` (new)
- 7 tests, resolver + supabase client mocked (mirrors `test_cluster_store._RecordingClient`).

## Files touched
- `agents/pipeline/clustering/continuity.py` (new)
- `agents/pipeline/clustering/online_clusterer.py` (extended — entry point only)
- `tests/agents/pipeline/clustering/test_continuity.py` (new)

## Normalizer parity (Open Question #2)
- **Reused:** `normalize_url` from `agents/ingestion/dedup.py` (line 93).
- **How parity was confirmed:** `grep` showed the alias WRITE path is
  `agents/pipeline/persist_helpers.py:build_story_url_alias_rows` (line 656), which builds
  `{"alias_normalized_url": normalize_url(member_url), ...}` from that SAME function
  (lines 680-686). The resolver (`daily_batch.build_story_id_resolver`, lines 415-461)
  reads rows keyed on `alias_normalized_url`. So write-key, lookup-key, and my
  continuity-key are all `dedup.normalize_url`. Test (d)
  `test_normalization_matches_alias_write_path_key_form` proves it dynamically: it builds
  real alias rows via `build_story_url_alias_rows` for a raw URL, then confirms the key
  `resolve_cluster_story_ids` queries the resolver with is a member of the write-key set,
  and that the cross-day id is reused. No new normalizer was invented.

## Multi-story-id tie-break decision
A cluster whose members alias to >1 existing story id resolves to `min(existing_ids)`
(smallest string). The resolver returns no seen-count / recency signal, so a stable string
ordering is the defensible deterministic choice; the collision is logged. Asserted by
`test_multi_story_id_cluster_picks_smallest_deterministically`.

## Self code-review findings + fixes
- No critical/high/medium correctness issues found in `continuity.py` logic.
- **Fixed (low):** duplicate `from typing import` lines in `online_clusterer.py` →
  consolidated to one `from typing import TYPE_CHECKING, Any, Callable`.
- **Fixed (low):** test constructed `CanonicalStory` with non-existent fields
  (`canonical_summary`, `canonical_category`) and missing required ones — corrected to the
  real schema (`canonical_story_id`, `canonical_published_utc`,
  `canonical_primary_outlet_domain`).
- **Fixed (low, ruff F401):** removed an unused `from agents.pipeline.clustering import
  continuity` import in the test.

## Validation results
- `pytest tests/agents/pipeline/clustering/test_continuity.py -q` → **7 passed**.
- `pytest tests/agents/pipeline/clustering/ -q` → **56 passed** (49 prior + 7 new; NO
  regression). Note: the phase file's "49 existing" wording counts the full prior suite;
  the run shows 56 total after adding the 7 new continuity tests.
- `ruff check` on the 3 touched files → **All checks passed**.
- No real Gemini / Supabase calls — resolver and client both mocked.

## Definition of done — PASS
- (a) aliased-member cluster reuses story S, `mint_story_id` NOT called — PASS.
- (b) only-unseen-URL cluster mints a fresh id — PASS.
- (c) `persist_run` upserts once per cluster + `add_cluster_members` with that cluster's
  members (call-arg asserted, mocked client) — PASS.
- (d) URL normalization matches the alias key form used by `build_story_id_resolver`
  (explicit parity test against the real write path) — PASS.
- pytest green, no regression, ruff clean — PASS.

## Concerns
- None blocking. The normalizer-parity guard is the load-bearing risk and is now pinned by
  a test that breaks if either side's normalization drifts. `run_and_persist` is composable
  but not yet wired into `daily_batch` — that wiring is explicitly M3c, per the phase scope.
- Did NOT commit (orchestrator commits at phase end). Touched only the three permitted files.
  SP3's `cluster_candidates` logic unchanged.
