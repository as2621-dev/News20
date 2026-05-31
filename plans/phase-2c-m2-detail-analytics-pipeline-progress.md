# Progress: phase-2c-m2-detail-analytics-pipeline

**Phase file:** plans/phase-2c-m2-detail-analytics-pipeline.md
**Started:** 2026-05-31
**Phase-diff baseline commit:** de4f0da
**Execution mode:** PARALLEL with phase-2b (disjoint file sets, same working tree). Internally SP1 gates → {SP2 ∥ SP3} → SP4.

## Resume context
- Pre-started groundwork already committed (de4f0da): the phase plan, master-plan Decision #10, `reference/supabase-schema.md` 0004 entity docs, and **`supabase/seed/outlets.sql`** (269-line AllSides/Ad Fontes outlet→bias→domain seed — SP1's seed deliverable is DONE).
- SP1 still owes: the migration SQL file `supabase/migrations/0004_detail_analytics.sql`, `agents/pipeline/models.py` extensions, `src/types/detail.ts` extensions.

## Cross-phase guardrails (enforced in every sub-agent prompt)
- **Do NOT edit `agents/qa/*`, `agents/worker/*`, or any `src/components/detail/*.tsx`** — those are phase-2b's. 2c is DATA-PATH ONLY; the UI is owner-supplied.
- 2c owns: `supabase/migrations/0004*.sql`, `supabase/seed/outlets.sql`, `agents/pipeline/{models,prompts,persist,persist_helpers,orchestrator}.py`, `agents/pipeline/stages/{coverage_gdelt,detail_enrichment}.py`, `src/types/detail.ts`, `src/lib/detail/fetchStoryDetail.ts`, `reference/{supabase-schema,integrations}.md`, `supabase/seed/*` fixtures.

## Sub-phase progress
- [x] 1: Migration 0004 + Pydantic models + TS contract (outlets seed pre-done) — **COMPLETE (code) 2026-05-31; LIVE DDL APPLY GATED**. tsc 0, 11 pytest pass (5 reject-malformed), ruff clean. Files: `supabase/migrations/0004_detail_analytics.sql`, `agents/pipeline/models.py` (extend, now 522 LoC), `src/types/detail.ts` (extend), `tests/agents/pipeline/test_detail_analytics_models.py`. Report: sub-1.md.
  - **⚠ GATED — owner must run the live apply before SP4 e2e:** `supabase db push --db-url <IPv4 pooler>` for 0004, then `psql … -f supabase/seed/outlets.sql`. SP2/SP3 do NOT need it (mocked).
  - **Concerns for SP2–SP4:** (1) new `TrustSummary`/`StoryDetail` fields are OPTIONAL `?` (required would break 3 existing callers) — **SP4 must populate** `second_analytic`, `detail_key_points`, coverage-reach fields. (2) seed `on conflict` target is `outlet_name` (0001 UNIQUE) — needs only the `outlet_domain` *column* from 0004; run 0004 before seed. (3) models.py 522 LoC (mandated docstrings; hard limit 1000 clear).
- [x] 2: GDELT coverage stage (deterministic) — **COMPLETE 2026-05-31** (14 tests pass, ruff clean; noise-filter + partisan/reach + blindspot + graceful AdapterFetchError fallback all verified). Files: `agents/pipeline/stages/coverage_gdelt.py` (new ~470 LoC), `reference/integrations.md` (+9/−5 GDELT reconciliation), `tests/agents/pipeline/test_coverage_gdelt.py`. Report: sub-2.md.
  - **SP4 wiring:** `build_coverage_report(story, story_segment_slug, outlets_lookup: dict[str, BiasLean], adapter) -> CoverageReport` → persist into `story_trust` reach columns. **SP4 must** (a) pass the SAME `GdeltDocAdapter` instance ingestion uses (shared ≤1-req/5s throttle), (b) load `outlets_lookup` once per batch from the **seeded** outlets table (needs gated apply+seed; inject mock in tests), (c) resolve real `story_segment_slug` (reused `derive_blindspot_lean`, not the dead static `derive_coverage_counts`).
- [x] 3: LLM detail-enrichment stage (grounded) — **COMPLETE 2026-05-31** (14 tests pass, ruff clean; grounded-or-omitted number gate verified — ungrounded `+4%` dropped + `analytic_is_grounded=false`). Files: `agents/pipeline/stages/detail_enrichment.py` (new, ~480 LoC, defines local `DetailEnrichment` aggregate), `agents/pipeline/prompts.py` (append-only +101), `tests/agents/pipeline/test_detail_enrichment.py`. Report: sub-3.md.
  - **SP4 wiring:** `run_detail_enrichment(story, script, llm_client, segment_slug) -> DetailEnrichment` (key_figure→`stories.story_key_figure_*`, timeline→`story_timeline`, second_analytic→`story_analytics`, key_points(×5)→`detail_key_points`). **⚠ SP4 MUST fix `persist.py:_resolve_segment_slug` (stubs to `wildcard`) and pass the real segment** — else every story gets `why_it_matters` and the skinned tab is a no-op.
- [x] 4: Persist + orchestrator wiring + fetch + seed — **COMPLETE (code) 2026-05-31; LIVE E2E GATED**. 127 pytest pass (7 new), tsc 0, 31 vitest pass (5 new), ruff clean. Files: `agents/pipeline/{orchestrator,persist,persist_helpers}.py`, `src/lib/detail/fetchStoryDetail.ts`, `supabase/seed/seedM0Digests.ts` (PHASE_2C_FIXTURES s1–s5, edited not run), `tests/agents/pipeline/test_persist_detail_analytics.py`, `tests/lib/detail/fetchStoryDetail.test.ts`. Report: sub-4.md.
  - **Enrichment defaults OFF** (`enable_detail_enrichment=False`) — live path stays dark until owner wires `daily_batch.py` (build interest_segment_lookup, call load_outlets_lookup, share ingestion's GdeltDocAdapter, pass enable=True).
  - **⚠ Live e2e prereqs (owner):** (1) apply migration 0004 (`supabase db push --db-url <IPv4 pooler>`); (2) seed `supabase/seed/outlets.sql`; (3) budget ack (paid Gemini/story + GDELT throttle); (4) wire daily_batch then `RUN_LIVE_E2E=1 .venv/bin/python tests/agents/pipeline/sp4_e2e_fixture_run.py` (or `npm run seed` after 0004 to render s1–s5).
  - **Slop-scan flag:** persist.py 533 / orchestrator.py 624 / persist_helpers.py 661 LoC — over soft 500 (mostly docstrings), under hard 1000.

## Phase-level passes (DoD partial — live apply GATED)
- **DoD (code):** PASS — mocked `persist_digest` writes all new tables correctly-ordered; mocked `fetchStoryDetail` returns the extended `StoryDetail` (second_analytic + 5 key points + timeline + key figure + mode-correct coverage); segment stub fixed. Combined green: Python 217 · tsc 0 · biome 72 · vitest 136. **Live e2e (paid Gemini + writes, needs 0004 applied + outlets seeded) GATED.**
- **Slop scan:** PASS — no TODO/console.log/dead code/swallowed errors. Accepted-with-justification: `orchestrator.py` 624 / `persist_helpers.py` 661 / `persist.py` 533 / `models.py` 522 LoC over the soft 500 (docstring-heavy pipeline/contract files, under the 1000 hard limit; splitting is a separate refactor — Rule 3).
- **CSO:** PASS — additive migration 0004 (no DROP) + RLS public-read; no secrets; GDELT query is a free-text HTTP param (no injection). Shared findings file `.agents/cso-findings/phase-2b-2c-m2.md`.
- **Status: COMPLETE (code) — committed. Live e2e GATED.**

## Gated (need explicit owner GO — paid/irreversible)
- **SP1 live DDL apply** (`supabase db push --db-url <IPv4 pooler>` for 0004) — ⚠ irreversible. SP1 AUTHORS + locally validates the migration, then STOPS; owner GO required before the live apply.
- **SP4 live e2e persist** (paid Gemini LLM enrichment + writes) — owner GO required.
</content>
