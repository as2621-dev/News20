# Phase 2c — Sub-phase 1 execution report

**Sub-phase:** Schema migration 0004 + Pydantic models + TS contract
**Status:** SUCCESS (live DDL apply GATED — awaiting owner GO)
**Tree:** main working tree (no worktree)
**Date:** 2026-05-31

## What was implemented

1. **`supabase/migrations/0004_detail_analytics.sql` (NEW, 92 lines)** — additive, forward-only, NO DROPs:
   - Enums `coverage_mode` (`partisan`|`reach`) and `analytic_kind` (`market_impact`|`ripple`|`impact`|`stakes`|`why_it_matters`).
   - `alter table outlets add column outlet_domain text` + partial unique index `uq_outlets_domain on outlets (outlet_domain) where outlet_domain is not null`.
   - Table `detail_key_points` (`key_point_story_id` FK, 0-based `key_point_index`, `key_point_text`, `uq_detail_key_point_order` unique per story) + index.
   - Table `story_analytics` (1:1 — `analytic_story_id` UNIQUE FK, `analytic_kind`, `analytic_tab_label`, `analytic_headline`, `analytic_summary_text`, `analytic_rows jsonb default '[]'`, `analytic_is_grounded`).
   - `alter table story_trust add` 4 reach columns (`coverage_mode` default `'partisan'`, `coverage_momentum`, `coverage_originating_outlet_name`, `coverage_notable_outlet_names text[] default '{}'`).
   - Public-read RLS (`enable row level security` + `for select using (true)`) on both new content tables.
   - Style matches 0001–0003: lowercase SQL, box-drawing dividers, verbose comments, columns transcribed verbatim from the schema doc.

2. **`agents/pipeline/models.py` (EXTEND, +287 lines)** — new type aliases (`AnalyticKind`, `CoverageMode`, `BiasLean`, `AnalyticRowDirection`, `CoverageMomentum`) + 6 Pydantic models: `AnalyticRow` (with `model_config = {"extra": "forbid"}`), `SecondAnalytic`, `CoverageReport`, `DetailTimelineEvent`, `KeyFigure`, `DetailKeyPoint`. Every `analytic_rows[]` element is validated through `AnalyticRow`; `SecondAnalytic.analytic_rows: list[AnalyticRow]` so a malformed element fails the whole model. Google-style docstrings with examples on every model.

3. **`src/types/detail.ts` (EXTEND additively, +101 lines)** — `AnalyticKind`, `CoverageMode`, `AnalyticRow`, `SecondAnalytic`, `DetailKeyPoint`; reach fields on `TrustSummary` (`coverage_mode`, `coverage_momentum`, `coverage_originating_outlet`, `coverage_notable_outlets`); `detail_key_points` + `second_analytic` on `StoryDetail`. **No existing field removed or renamed.**

4. **`tests/agents/pipeline/test_detail_analytics_models.py` (NEW, 227 lines, 11 tests)** — happy/failure/edge. Round-trips a valid `SecondAnalytic` + `AnalyticRow`s to the JSONB shape; asserts malformed `analytic_rows` elements (missing label, bad direction, unknown field, malformed element inside the array, out-of-enum `analytic_kind`) raise `ValidationError`; edge cases for zero rows, the 5-cap on `coverage_notable_outlet_names`, partisan-mode field defaults, and empty key-point text.

## Divergences / conflicts surfaced (Rule 7/12)

1. **New `TrustSummary` / `StoryDetail` fields are OPTIONAL (`?`), not required.** Adding them as *required* broke three existing construction sites that I am not in scope to edit: `src/lib/detail/fetchStoryDetail.ts:194,232` (SP4's file) and `tests/lib/detail/{storyDetail,trustStrip}.test.tsx` (phase-2 M2 UI tests). Marking the new fields optional keeps the contract strictly additive and non-breaking, lets `tsc` pass without touching out-of-scope files, and still lets SP4 populate them. **SP4 must populate these in `fetchStoryDetail` and the seed.** If the team prefers them required, SP4 should flip `?`→required *and* update those three call sites in the same change.

2. **Seed `on conflict` target is `outlet_name`, NOT `outlet_domain`.** `supabase/seed/outlets.sql:269` is `on conflict (outlet_name) do nothing` (and its header line 8 says the same). The task framing expected `on conflict (outlet_domain)`. The seed-as-written is **valid and idempotent** because `outlet_name` already has a `UNIQUE` constraint from migration 0001 — so it does NOT need `uq_outlets_domain` as its arbiter. (A partial unique index like `uq_outlets_domain ... WHERE outlet_domain IS NOT NULL` can't be a plain `on conflict (col)` arbiter without restating the predicate anyway.) The seed only needs the `outlet_domain` *column* to exist (it inserts into it) — which 0004 adds. **No seed rewrite needed; 0004 must run before the seed.** Flagging the framing/file mismatch per Rule 12.

3. **`agents/pipeline/models.py` is now 522 lines — 22 over the soft 500-line "agent logic" guideline.** This is a pure Pydantic *data/contract* file (not tools/prompts/orchestration), the phase plan explicitly names it as the single models file SP2–SP4 import from, and the overrun is entirely Google-style docstrings-with-examples (mandated by CLAUDE.md "DOCUMENTATION EVERYWHERE"). Splitting it would fragment the import surface and diverge from the plan. Left as one file; hard 1000-line limit is well clear. Flagging for awareness.

4. **Schema doc NOT edited.** `reference/supabase-schema.md` already documents every 0004 entity verbatim (enums §1, `detail_key_points`/`story_analytics` §2, `story_trust` ALTER §2, `outlets.outlet_domain` §2, RLS §6) and my SQL matches it with zero divergence. Marking it "applied" now would be false (live apply is gated, Rule 12), so no edit was made.

## Self-review findings + fixes

- **High (fixed):** required new TS fields broke 3 existing callers → made the new fields optional (divergence #1). tsc now clean.
- **Medium (fixed):** `models.py` referenced `BiasLean` which didn't exist in the file → added a `BiasLean = Literal["left","center","right"]` alias mirroring the Postgres enum + TS type.
- **Low (fixed):** ruff format reflowed long `Field(...)` lines in `models.py`/test → applied `ruff format`.
- **Considered, kept:** `AnalyticRow` uses `extra="forbid"` so a hallucinated extra JSONB key (e.g. a fabricated confidence score) is rejected — strengthens the grounding guardrail (Decision #5). Covered by a dedicated test.

## Validation results

| Check | Command | Result |
|---|---|---|
| TS typecheck | `npx tsc --noEmit` | **PASS** (exit 0, no errors) |
| Ruff lint | `.venv/bin/ruff check agents/pipeline/models.py tests/agents/pipeline/test_detail_analytics_models.py` | **PASS** ("All checks passed!") |
| Ruff format | `.venv/bin/ruff format --check agents/pipeline/models.py` | **PASS** ("1 file already formatted") |
| Pytest | `.venv/bin/python -m pytest tests/agents/pipeline/test_detail_analytics_models.py -q` | **PASS** (11 passed in 0.01s) |
| SQL | by-eye vs migrations 0001–0003 + schema doc (NOT applied) | **PASS** (round-trip self-review below) |

`next build` / `npm test` intentionally NOT run (sibling shares the tree — avoided `.next/` races).

## SQL round-trip self-review (not applied)

Against seeded story `s1` (exists from 0001 + seed):
- `INSERT INTO story_analytics (analytic_story_id, analytic_kind, analytic_tab_label, analytic_headline, analytic_summary_text, analytic_rows, analytic_is_grounded) VALUES ('s1','market_impact','MARKET IMPACT','…','…','[{"analytic_row_label":"Brent crude","analytic_row_value":"+4%","analytic_row_direction":"up","analytic_row_note":null}]'::jsonb,true);` — FK resolves, enum value legal, JSONB element shape == `AnalyticRow.model_dump()`. ✓
- `INSERT INTO detail_key_points (key_point_story_id,key_point_index,key_point_text) VALUES ('s1',0,'…'),…,('s1',4,'…');` — `uq_detail_key_point_order` holds for 0..4. ✓
- `SELECT analytic_kind, analytic_rows FROM story_analytics WHERE analytic_story_id='s1';` and `SELECT key_point_text FROM detail_key_points WHERE key_point_story_id='s1' ORDER BY key_point_index;` — round-trip in order. ✓
- `story_trust` ALTER on existing rows: `coverage_mode`→`'partisan'`, `coverage_notable_outlet_names`→`'{}'`, momentum/originating NULL. No NOT-NULL-without-default. ✓
- `SELECT outlet_bias_lean FROM outlets WHERE outlet_domain='cnn.com';` returns `'left'` **only after the seed runs** (seed inserts `('CNN','left','cnn.com')`). Column + `uq_outlets_domain` from 0004 back it. ✓

## Definition of done (per item)

| DoD item | Status |
|---|---|
| Migration 0004 authored, matches schema doc | **PASS** |
| `supabase db push --db-url <pooler>` applies 0004 with no error | **GATED** (owner-run live apply) |
| SQL `INSERT`/`SELECT` round-trip would work (self-review) | **PASS** |
| `outlets.outlet_domain` + unique index back the seed | **PASS** (column added; seed arbiter is `outlet_name` UNIQUE from 0001 — see divergence #2) |
| `SELECT … WHERE outlet_domain='cnn.com'` returns a lean | **GATED** (requires live apply + seed run) |
| `npx tsc --noEmit` passes with extended `StoryDetail` | **PASS** |
| Pydantic test rejects a malformed `analytic_rows` element (Rule 9) | **PASS** (5 rejection tests) |

## Owner-run live-apply command (GATED — do NOT auto-run)

Apply via the IPv4 session pooler (direct host is IPv6-only — memory `news20-supabase-ddl-connection`):

```bash
cd /Users/asheshsrivastava/News20/News20
# Replace <PROJECT-REF> and <DB-PASSWORD>; pooler host is the aws-1-us-east-1 session pooler.
supabase db push --db-url "postgresql://postgres.<PROJECT-REF>:<DB-PASSWORD>@aws-1-us-east-1.pooler.supabase.com:5432/postgres"
```

Then seed the bias table (after 0004 is applied — it depends on `outlets.outlet_domain`):

```bash
psql "postgresql://postgres.<PROJECT-REF>:<DB-PASSWORD>@aws-1-us-east-1.pooler.supabase.com:5432/postgres" \
  -f supabase/seed/outlets.sql
```

Verify:
```sql
\d story_analytics
\d detail_key_points
\d+ story_trust            -- shows coverage_mode/momentum/originating/notable
SELECT outlet_bias_lean FROM outlets WHERE outlet_domain = 'cnn.com';  -- → left
```

## Contract shapes SP2–SP4 will consume

- **SP2 (`coverage_gdelt.py`)** → returns `CoverageReport` (`agents.pipeline.models`): `coverage_mode` + partisan counts/`blindspot_lean` OR reach `coverage_outlet_count`/`coverage_momentum`/`coverage_originating_outlet_name`/`coverage_notable_outlet_names` (capped at 5). Persists to the `story_trust` reach columns.
- **SP3 (`detail_enrichment.py`)** → returns `KeyFigure`, ordered `DetailTimelineEvent[]`, a `SecondAnalytic` (rows are `AnalyticRow[]`, each grounded-or-direction-only; `analytic_is_grounded` set), and exactly 5 `DetailKeyPoint`. The segment→`analytic_kind` map is a pure function SP3 owns; the enum is `AnalyticKind`.
- **SP4 (persist/orchestrator/fetch/seed)** → writes `story_analytics` (validate each `analytic_rows[]` via `AnalyticRow` before insert — never raw dicts), `detail_key_points`, the new `story_trust` columns, `stories.story_key_figure_*`, `story_timeline`. `fetchStoryDetail` reads them into the extended `StoryDetail`; **populate the now-optional `detail_key_points`/`second_analytic`/coverage-reach fields** (divergence #1). TS shapes: `SecondAnalytic`, `DetailKeyPoint`, `AnalyticRow`, `CoverageMode`, `AnalyticKind` in `src/types/detail.ts`.

## Files touched
- `supabase/migrations/0004_detail_analytics.sql` (new)
- `agents/pipeline/models.py` (extend)
- `src/types/detail.ts` (extend)
- `tests/agents/pipeline/test_detail_analytics_models.py` (new)
- `reference/supabase-schema.md` — reviewed, already consistent, NOT edited (live apply gated)
