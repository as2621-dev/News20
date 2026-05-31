# Execution report — Phase 1d SP1: Interest-keyed ingestion + dedup pool + ancestor tagging

**Date:** 2026-05-31
**Status:** ✅ COMPLETE (mock-tested; no cost, no writes, no live API hit)
**Phase file:** `plans/phase-1d-daily-content-pipeline.md` (SP1)

## What shipped
The SP1 ingest spine, fully unit-tested with mocked externals:

| File | Decision | Purpose |
|---|---|---|
| `agents/ingestion/models.py` | NEW (News20-native) | `InterestNode`, `ActiveInterest`, `CandidateStory`, `CanonicalStory`, `StoryInterestTag`, `IngestionResult` |
| `agents/ingestion/adapters/base.py` | PORT (pattern) | `BaseNewsAdapter` ABC — two-phase `search()` / `extract_body()` |
| `agents/ingestion/adapters/gdelt_doc.py` | NEW | GDELT DOC 2.0 JSON adapter (sort=hybridrel, timespan 1–3d, ≤1 req/5s throttle) + `trafilatura` body extraction |
| `agents/ingestion/dedup.py` | PORT primitives + ADAPT | `normalize_url` + `compute_title_similarity` (verbatim) + `StoryClusterer` (cross-outlet clustering → `CanonicalStory` w/ distinct outlet count) |
| `agents/ingestion/ancestor_tagging.py` | NEW | `build_ancestor_tags` (leaf→parent→grandparent, relative depth 0/1/2) + `merge_story_tags` (min-depth per interest) |
| `agents/ingestion/interest_keyed_pipeline.py` | NEW | `build_active_interest_set` (empty-safe fail-loud) + `ingest_active_interests` (fan-out → cluster → extract → tag → `IngestionResult`) |
| `agents/shared/exceptions.py` | edit (additive) | `IngestionError`, `AdapterFetchError` reintroduced as ingestion is ported |
| `requirements.txt` | edit | added `trafilatura>=1.6` |
| `tests/agents/ingestion/{conftest,test_gdelt_adapter,test_dedup,test_ancestor_tagging,test_interest_keyed_pipeline}.py` | NEW | 37 tests, all mocked |

## DoD mapping (phase file SP1)
- ✅ **Ingestion against a mocked feed returns typed candidate stories** — `test_gdelt_adapter.py::TestGdeltSearchParsing` (mocked httpx + GDELT JSON fixture → typed `CandidateStory`, junk row skipped).
- ✅ **Dedup merges near-duplicate items and counts covering outlets** — `test_dedup.py::TestStoryClusterer` (2 outlets, near-identical titles → 1 story, `story_outlet_count=2`, sorted distinct `covering_outlets`; same-domain twice → count 1).
- ✅ **Leaf `sport.soccer.arsenal` → rows for Arsenal(0), Soccer(1), Sport(2) with correct match_depth** — `test_ancestor_tagging.py::test_leaf_match_tags_self_parent_grandparent` (the exact DoD example) + grandparent cap + multi-interest min-depth merge.
- ✅ **Active-interest set is empty-safe (fails loud w/ fix_suggestion if no profiles)** — `test_interest_keyed_pipeline.py::test_empty_followed_ids_raises` (`IngestionError`).
- ✅ **Unit tests assert adapter parsing + dedup clustering + ancestor tagging (mocked HTTP, no live key)** — 37 tests; `ruff check` + `ruff format --check` clean; agent files < 500 LoC.

## Deviations from the written phase file (flagged — Rule 12/7)
1. **Adapter is `gdelt_doc.py`, not `newsapi.py`** — resolved in the progress file Step 0 (no NewsAPI key; GDELT validated live 2026-05-31).
2. **`dedup.py` = PORT primitives + ADAPT into a clusterer** — donor `deduplicate_batch` only drops dupes; SP1's DoD needs clustering + covering-outlet count (reuse-map Decision #6). Primitives ported verbatim.
3. **`models.py` = News20-native**, not the donor per-user `ContentItemRecord` (which carries `user_id`/`source_type` for youtube/podcast). News20 stories are shared + interest-keyed.
4. **`feed_utils.py` deferred** — it is RSS/feedparser-specific; GDELT DOC is JSON, so porting it now = dead code (Rule 2). Port it when a real RSS news adapter lands. (`feedparser` is also absent from the venv — irrelevant to SP1.)
5. **New dep `trafilatura`** (already present in the venv; added to `requirements.txt`).

## Verification
- `pytest tests/agents/ingestion/` → **37 passed**.
- `pytest tests/agents/` (full agent suite) → **65 passed**, no regressions (1 pre-existing pydub/audioop deprecation warning, unrelated).
- `ruff check agents/ingestion tests/agents/ingestion` → All checks passed.
- `ruff format --check` → all formatted (matched the repo's de-facto ruff-default 88-col style — no config file; the maintained M0 files conform).

## NOT done in SP1 (by design — deferred / out of scope)
- **No live GDELT call, no body fetch, no DB writes** — all externals mocked. The real ingest run is SP4's manual batch.
- **Supabase reads that build the inputs** (the followed-interest ids + the `interests` taxonomy map) are injected here; the actual Supabase-loading glue is the SP4 orchestrator's job. SP1 is a pure function over injected data.
- Persisting `story_interests` rows is **SP3** (`persist.py`); SP1 only produces the row payloads.

## Side note (this run)
Mid-SP1, the owner re-scoped **Phase 2b** to drop Pinecone/RAG in favor of in-context grounding (the per-story corpus is tiny). That is unrelated to 1d; recorded in `plans/phase-2b-*`, `plans/master-plan.md`, `reference/{integrations,reuse-map,prototype-port-map}.md`, and memory `news20-qa-incontext-grounding`.

## Next
**SP2 — Produce-once gate → single-source script + verification.** Still mock-tested / no cost. SP3 (paid TTS/image + DB writes) and SP4 (cron + paid APIs + writes) will checkpoint with the owner before spending.
