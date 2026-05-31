# Phase 2c — Sub-phase 4 execution report

**Sub-phase:** Persist + orchestrator wiring + fetch + seed (INTEGRATION)
**Status:** SUCCESS (live per-story e2e GATED — awaiting owner GO + migration 0004 apply + outlets seed)
**Tree:** main working tree (no worktree; a sibling builds phase-2b frontend concurrently on disjoint files)
**Date:** 2026-05-31

## What was implemented

### 1. Orchestrator wiring (`agents/pipeline/orchestrator.py`)
- New private helper `_run_detail_stages(story, script, segment_slug, llm_client, outlets_lookup, gdelt_adapter) -> (DetailEnrichment, CoverageReport | None)` — runs `run_detail_enrichment` (SP3) always, and `build_coverage_report` (SP2) only when BOTH a shared `gdelt_adapter` and an `outlets_lookup` are injected (else coverage degrades to the legacy static derivation at persist). The SAME resolved `segment_slug` is passed to both so the second-analytic kind and the coverage mode agree (Decisions #2/#3).
- `orchestrate_story` gains 4 optional params: `enable_detail_enrichment: bool = False`, `interest_segment_lookup`, `outlets_lookup`, `gdelt_adapter`. The segment is resolved ONCE at the top via `resolve_segment_from_tags` and reused by both stages + persist. The enrichment+coverage results are threaded into `persist_digest`.
- **`enable_detail_enrichment` defaults False** (see divergence #1) so the existing SP3 produce path (and `daily_batch.run_daily_pipeline`, which I do not touch) is byte-for-byte unchanged — no surprise paid LLM calls and no test regressions.

### 2. Fixed the segment stub (`agents/pipeline/persist.py:_resolve_segment_slug`)
- Was a stub returning `"wildcard"` always → now resolves the real `story_segment_slug` from the story's best-matched interest (lowest `story_interest_match_depth` = leaf/closest) against the injected `interest_segment_lookup` (`{interest_id: segment_slug}`), via the new pure `resolve_segment_from_tags`. Falls back to `wildcard` only when nothing resolves. **Geopolitics now gets `partisan`/`market_impact` instead of a no-op.**

### 3. Persist (`agents/pipeline/persist.py` + `persist_helpers.py`, INSERT-only, parents-first)
- New pure builders in `persist_helpers.py`: `build_story_timeline_rows` (contiguous index order), `build_story_analytics_row` (each `analytic_rows[]` element re-validated by **`AnalyticRow.model_dump()`** — never a raw dict at the DB boundary), `build_detail_key_point_rows` (5, 0-based), `resolve_segment_from_tags`, plus `load_outlets_lookup` (the per-batch `{outlet_domain: bias_lean}` loader, service-role read, injected/mocked in tests).
- `build_story_row` extended to write `stories.story_key_figure_*` from the enrichment's grounded `KeyFigure`.
- `build_story_trust_row` extended: when a `CoverageReport` is supplied it writes the mode-correct counts + the 4 reach columns (`coverage_mode`/`coverage_momentum`/`coverage_originating_outlet_name`/`coverage_notable_outlet_names`); without one it keeps the legacy static derivation and omits `coverage_mode` (DB default `'partisan'` holds).
- `persist_digest` gains `enrichment`, `coverage_report`, `interest_segment_lookup` (all optional). A new private `_persist_detail_enrichment` writes `story_timeline` → `story_analytics` (1:1) → `detail_key_points` after the parents. `PersistResult` gains `timeline_event_count` / `detail_key_point_count` / `story_analytics_written` audit fields.
- Removed now-dead `DEFAULT_SEGMENT_SLUG`/`_VALID_SEGMENT_SLUGS` constants + the redundant post-resolve validity guard (the resolver only ever returns a valid slug).

### 4. Fetch (`src/lib/detail/fetchStoryDetail.ts`, additive)
- Two new concurrent reads: `story_analytics` (1:1, `.maybeSingle`) → `second_analytic` (or `null`); `detail_key_points` (`.order("key_point_index")`) → `detail_key_points`. `story_trust` select extended with the 4 reach columns → populates the optional `coverage_mode`/`coverage_momentum`/`coverage_originating_outlet`/`coverage_notable_outlets` on `TrustSummary`. Matches the existing per-table-error / `fix_suggestion` idiom + double-quote Biome rule. No existing read changed.

### 5. Seed (`supabase/seed/seedM0Digests.ts`, fixtures only)
- Added a `PHASE_2C_FIXTURES` map (s1–s5) with a realistic `second_analytic` (segment-correct kind: s1 geopolitics→market_impact, s2 sport→stakes, s3 tech→impact, s4 markets→ripple, s5 wildcard→why_it_matters), 5 `detail_key_points`, and `coverage_mode` (+reach fields where `reach`). Seeds `story_analytics` + `detail_key_points` in `seedStoryChildren` (idempotent upsert) and the `coverage_mode`/reach columns in the `story_trust` upsert. **Did not run the seed.**

## Divergences / conflicts surfaced (Rule 7/12)

1. **`enable_detail_enrichment` gate (default False).** `run_detail_enrichment` needs only the LLM, so wiring it unconditionally would (a) break the existing SP3 orchestrator tests (their mocked LLM supplies exactly 2 responses; enrichment is a 3rd `call_gemini` → `StopIteration`), and (b) make the next live `daily_batch` (which I'm not in scope to edit) silently start paying for enrichment with NO segment/outlets lookup fed in (→ everything `wildcard`, coverage skipped). I gated it behind an explicit opt-in so existing behavior is preserved and turning it on is a deliberate act. **The live e2e + a future `daily_batch` wiring must pass `enable_detail_enrichment=True` + the two lookups + the shared adapter.** Flagged for the owner.
2. **`outlets_lookup` loader lives in `persist_helpers.load_outlets_lookup` (does a live read).** `persist_helpers` is otherwise pure; this one function is the explicit injected-client boundary the brief asked for. The orchestrator/batch calls it ONCE per batch and threads the dict down; tests inject a mock (no live Supabase). Kept it here (next to the other persist concerns) rather than in `daily_batch.py` (out of scope).
3. **`daily_batch.py` NOT wired.** It's not in my touch list. So the production daily pipeline does NOT yet enrich — it calls `orchestrate_story` with the default `enable_detail_enrichment=False`. Wiring the batch loader (build `interest_segment_lookup` from `interests`, call `load_outlets_lookup`, share the ingestion `GdeltDocAdapter`, pass `enable_detail_enrichment=True`) is the **owner's next step** to light up the live path. Flagged.
4. **Segment for leaf interests.** Only depth-0 `interests` rows carry `interest_segment_slug` (seed-verified); leaves inherit their root's. So the `interest_segment_lookup` the batch builds must already resolve every interest_id (incl. leaves) to its root's segment (a single SQL ancestor join, or precompute). `resolve_segment_from_tags` consumes a flat dict and does not walk the tree itself — flagged so the loader populates it fully.

## File-size note (Rule 11 vs the soft 500-LoC guideline)
`persist.py` = 533, `orchestrator.py` = 624, `persist_helpers.py` = 661 LoC — over the soft 500 "agent logic" guideline but well under the 1000 hard limit. Consistent with the SP1 precedent (`models.py` kept at 522, justified as a docstring-heavy data/IO file). `persist.py`/`persist_helpers.py` are persistence/IO + pure-builder files (not agent reasoning); splitting the insert-orchestration into the pure-builders file would violate the codebase's "no I/O in helpers" convention. The overrun is ~40% docstrings (CLAUDE.md "DOCUMENTATION EVERYWHERE"). I trimmed where I could (removed dead constants, terse new docstrings). Flagged for awareness.

## Tests (mocked at the boundary)
- **`tests/agents/pipeline/test_persist_detail_analytics.py` (NEW, 7 tests):**
  - `persist_digest` writes ALL new tables correctly-shaped + ORDERED: `stories.story_segment_slug` resolves to `geopolitics` (not wildcard) + key figure; `story_timeline` indices `[0,1]`; `story_analytics` segment-correct kind + `analytic_rows` are plain dicts via `model_dump` (direction-only row keeps `value=None`); `detail_key_points` exactly 5 `[0..4]`. Audit counts asserted.
  - no-enrichment path skips all 3 new tables + key figure null + `coverage_mode` omitted.
  - reach `CoverageReport` populates all 4 reach columns.
  - `_resolve_segment_slug`: geopolitics-matched → non-wildcard (Rule 9 — fails if the stub returns); no-lookup → wildcard; closest-`match_depth` wins.
  - `load_outlets_lookup` maps domain→lean from a mocked `outlets` read, lowercases domains, skips rows missing a domain OR a lean.
- **`tests/lib/detail/fetchStoryDetail.test.ts` (EXTENDED, +5 tests):** maps `second_analytic` + 5 `detail_key_points` in index order (mocked supabase); partisan-mode + reach-mode coverage field mapping; `second_analytic` null when no `story_analytics` row; new `eq`-key assertions for the two new tables. The `storyDetailSchema` Zod gate extended with the 2c optionals (contract drift still fails the parse).

## Validation results

| Check | Command | Result |
|---|---|---|
| Ruff lint | `.venv/bin/ruff check agents/pipeline` | **PASS** ("All checks passed!") |
| Ruff format | `.venv/bin/ruff format --check persist.py persist_helpers.py orchestrator.py` | **PASS** ("3 files already formatted") |
| Pytest | `.venv/bin/python -m pytest tests/agents/pipeline -q` | **PASS** (127 passed — 120 prior + 7 new; no SP2/SP3 regressions) |
| TS typecheck | `npx tsc --noEmit` | **PASS** (exit 0, no errors) |
| Vitest (scoped) | `npx vitest run tests/lib/detail` | **PASS** (4 files / 31 tests) |
| Biome | `npx biome check src/lib/detail/fetchStoryDetail.ts tests/lib/detail/fetchStoryDetail.test.ts` | **PASS** (formatted; seed is outside biome's `includes` scope — validated by tsc) |

`next build` / whole `npm test` intentionally NOT run (sibling shares the tree). No live LLM/GDELT/Supabase calls (all mocked at the boundary). No `git add`/`commit`.

## Definition of done (phase file SP4, MINUS the gated live e2e)

| DoD item | Status |
|---|---|
| `persist_digest` writes `story_timeline` (ordered), `story_analytics` (validated rows), `detail_key_points` (5, ordered), `stories.story_key_figure_*`, `story_trust` reach cols — mocked test asserts shape + order | **PASS** |
| Segment stub fixed — geopolitics resolves non-wildcard | **PASS** |
| `fetchStoryDetail` returns `second_analytic`, 5 `detail_key_points`, timeline, key figure, mode-correct coverage; key points/timeline in index order — mocked test | **PASS** |
| `outlets_lookup` loaded once per batch (injected; mocked in tests) | **PASS** (`load_outlets_lookup`) |
| Seed s1–s5 extended with second-analytic + 5 key points + coverage_mode | **PASS** (file edited; not run) |
| Biome + Ruff pass | **PASS** |
| Live per-story e2e persists all new tables with no FK/constraint error | **GATED — awaiting owner GO + migration 0004 apply + outlets seed** |

## Live-e2e prerequisites + EXACT command (owner-gated — DO NOT auto-run)

The live per-story e2e (`tests/agents/pipeline/sp4_e2e_fixture_run.py`, paid) needs, in order:

1. **Apply migration 0004** (SP1 authored it; not yet applied) via the IPv4 session pooler — direct host is IPv6-only (memory `news20-supabase-ddl-connection`):
   ```bash
   cd /Users/asheshsrivastava/News20/News20
   supabase db push --db-url "postgresql://postgres.<PROJECT-REF>:<DB-PASSWORD>@aws-1-us-east-1.pooler.supabase.com:5432/postgres"
   ```
2. **Seed the static bias table** (SP1's `outlets.sql`; required or every GDELT domain is unrated → partisan counts all-zero, blindspot always None):
   ```bash
   psql "postgresql://postgres.<PROJECT-REF>:<DB-PASSWORD>@aws-1-us-east-1.pooler.supabase.com:5432/postgres" -f supabase/seed/outlets.sql
   ```
3. **Budget ack** — the per-story enrichment is a paid Gemini call; the GDELT census competes for the ingestion adapter's ≤1-req/5s throttle (≥5s × N stories).
4. **Wire the live path** (NOT done here — `daily_batch.py` is out of scope): build `interest_segment_lookup` (`{interest_id → root segment}`) from `interests`, call `load_outlets_lookup(supabase)` once, reuse the ingestion `GdeltDocAdapter`, and pass `enable_detail_enrichment=True` + those three into `orchestrate_story`. Then:
   ```bash
   RUN_LIVE_E2E=1 .venv/bin/python tests/agents/pipeline/sp4_e2e_fixture_run.py
   ```
   (or run the existing seed to render the s1–s5 fixtures against the new shapes:)
   ```bash
   npm run seed   # after 0004 applied — writes story_analytics + detail_key_points + coverage_mode for s1–s5
   ```

## Concerns
- **Live path not lit until `daily_batch.py` is wired** (divergence #1/#3) — enrichment defaults OFF. This is intentional (no surprise paid calls, no regressions), but means a plain live daily run still produces pre-2c stories until the owner wires the batch loader + flips the gate. The seed path (`npm run seed`) DOES render the new shapes once 0004 is applied.
- **`interest_segment_lookup` must resolve leaves to their root segment** (divergence #4) — the resolver consumes a flat dict; the loader owns the ancestor walk.
- **`analytic_rows` JSONB** is stored as `AnalyticRow.model_dump()` dicts (explicit nulls) — matches SP1's 0004 `jsonb` column + the TS `AnalyticRow` shape; the fetch reads them straight back (test-confirmed).

## Files touched
- `agents/pipeline/orchestrator.py` (wire two stages; `enable_detail_enrichment` gate; segment resolved once)
- `agents/pipeline/persist.py` (segment resolver; enrichment/coverage params; `_persist_detail_enrichment`; audit fields)
- `agents/pipeline/persist_helpers.py` (new builders + `resolve_segment_from_tags` + `load_outlets_lookup`; extended `build_story_row`/`build_story_trust_row`)
- `src/lib/detail/fetchStoryDetail.ts` (additive reads for `story_analytics`, `detail_key_points`, reach columns)
- `supabase/seed/seedM0Digests.ts` (s1–s5 Phase 2c fixtures)
- `tests/agents/pipeline/test_persist_detail_analytics.py` (new, 7 tests)
- `tests/lib/detail/fetchStoryDetail.test.ts` (extended, +5 tests)
