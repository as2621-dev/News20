# Phase 2c — Sub-phase 3 execution report

**Sub-phase:** LLM detail-enrichment stage (grounded)
**Status:** SUCCESS
**Tree:** main working tree (no worktree)
**Date:** 2026-05-31

## What was implemented

1. **`agents/pipeline/stages/detail_enrichment.py` (NEW)** — `run_detail_enrichment(story, script, llm_client, *, segment_slug="wildcard") -> DetailEnrichment`. Single mocked Gemini call → parses → **grounds every number in code (Rule 5, not the LLM)** → returns the aggregate. Helpers: `select_analytic_kind` (pure), `_source_digit_stream`, `_is_number_grounded`, `_ground_analytic_rows`, `_ground_key_figure`, `_build_timeline`, `_build_key_points`, `_build_system_prompt`.
2. **`DetailEnrichment` aggregate defined LOCALLY** in the stage file (not in `models.py`) — SP1 shipped only the leaf models and this SP must not edit `models.py`. Imports `KeyFigure`/`DetailTimelineEvent`/`SecondAnalytic`/`DetailKeyPoint`/`AnalyticRow`/`AnalyticKind` read-only from `models.py`. **SP4 imports this aggregate to persist.** (Flagged per the task's local-model instruction.)
3. **`agents/pipeline/prompts.py` (APPEND-ONLY, +101 lines, 0 deletions)** — `DETAIL_ENRICHMENT_PROMPT` (single-source + numbers-are-trust-critical rules, explicit JSON contract) + `DETAIL_ANALYTIC_INSTRUCTIONS` dict (per-`analytic_kind` instruction strings). Existing prompts untouched (verified via `git diff`).
4. **`tests/agents/pipeline/test_detail_enrichment.py` (NEW, 14 tests)** — mocks the LLM via the existing `make_llm_client` conftest fixture.

## Design decisions / divergences surfaced (Rule 7/12)

1. **`segment_slug` is a PARAMETER, not read off the story.** `CanonicalStory` carries no segment; segment is resolved at persist time (`persist.py:_resolve_segment_slug`, SP4 owns it). So `run_detail_enrichment` takes `segment_slug` (default `"wildcard"`) and `select_analytic_kind` maps it. **SP4 must pass the resolved `segment_slug`** when wiring the orchestrator (today `_resolve_segment_slug` always returns `wildcard` — flagged in SP1/persist for SP4 to backfill from the matched interest).
2. **Grounding is a deterministic CODE gate (Rule 5), not the LLM's self-report.** Every numeric value (analytic rows + hero key figure) is checked against the source body's **digit stream** (`re.sub(r"\D","",body)`): a value grounds iff every numeric token's digits appear in that stream — robust to `+4%`/`4 percent`, `$81.6B`/`81.6 billion`, and thousands separators. An ungrounded number is **dropped to `None` (direction-only)** and `analytic_is_grounded` flips to **False**. The hero key figure value is dropped the same way. Pure-text values (`"record high"`) carry no number → kept, no effect on grounded.
3. **Exactly-5 key points: fail loud (Rule 12).** >5 → take first 5; **<5 → `PipelineStageError`** (never silently pad). Timeline indices are assigned by position in code (model not trusted to number them) so order is guaranteed contiguous 0-based.
4. **Tab label fixed by kind in code, not the model** (`_ANALYTIC_TAB_LABELS`) — keeps the UI label trustworthy.

## Validation results

| Check | Command | Result |
|---|---|---|
| Ruff lint | `ruff check detail_enrichment.py prompts.py test_detail_enrichment.py` | **PASS** ("All checks passed!") |
| Ruff format | `ruff format --check detail_enrichment.py` | **PASS** ("1 file already formatted") |
| Pytest | `python -m pytest tests/agents/pipeline/test_detail_enrichment.py -q` | **PASS** (14 passed in 0.04s) |
| Import sanity | stage + prompts import; placeholders present | **PASS** |
| Broader collect | `pytest tests/agents/pipeline/ --collect-only` | **PASS** (120 collected, no import break from the append) |

`next build` / `npm test` NOT run (per task). No live LLM calls (mocked at the `LLMClient.call_gemini` boundary). No `git add`/`commit`.

## DoD (phase file SP3) — per item

| DoD item | Status |
|---|---|
| Clean grounded payload → 5 key points | **PASS** |
| Clean payload → ≥1 timeline event IN ORDER | **PASS** (contiguous 0-based indices asserted) |
| Clean payload → `SecondAnalytic` kind matches segment map | **PASS** (geopolitics→market_impact, markets→ripple asserted) |
| Source-unsupported "+4%" → value dropped/de-numbered | **PASS** (value→None, direction "up" kept) |
| Source-unsupported "+4%" → `analytic_is_grounded=false` | **PASS** |
| No ungrounded number publishes | **PASS** (asserted no surviving row carries "+4%"; key-figure drop test too) |
| segment→kind selection is a pure function w/ happy+edge tests | **PASS** (5-param happy + unknown/empty edge) |

**DoD: PASS** — both grounded/ungrounded branches + segment→kind selection pass.

## Files touched
- `agents/pipeline/stages/detail_enrichment.py` (new, ~480 LoC incl. docstrings — under 500)
- `agents/pipeline/prompts.py` (append-only, +101)
- `tests/agents/pipeline/test_detail_enrichment.py` (new, 14 tests)

(Sibling-owned files `models.py`, `coverage_gdelt.py`, `sp4_e2e_fixture_run.py` show in `git status` but were NOT touched by me.)

## SP4 wiring contract

```python
from agents.pipeline.stages.detail_enrichment import run_detail_enrichment, DetailEnrichment

enrichment: DetailEnrichment = await run_detail_enrichment(
    story=canonical_story,          # CanonicalStory (must carry canonical_body_text)
    script=digest_script,           # DigestScript (provenance / story id)
    llm_client=llm_client,          # LLMClient
    segment_slug=resolved_segment,  # the SP4-resolved story_segment_slug (default "wildcard")
)
```

`DetailEnrichment` shape SP4 persists:
- `enrichment_story_id: str`
- `key_figure: KeyFigure` → `stories.story_key_figure_value` / `_label` (value already grounded-or-None)
- `timeline: list[DetailTimelineEvent]` → `story_timeline` rows (0-based contiguous `timeline_event_index`)
- `second_analytic: SecondAnalytic` → the `story_analytics` row. **`analytic_rows` are already `AnalyticRow` models** (validate again before insert per SP1's "never raw dict at the DB boundary"). `analytic_is_grounded` is the gate verdict.
- `key_points: list[DetailKeyPoint]` → `detail_key_points` rows (exactly 5, 0-based ordered)

## Concerns for SP4 / later

1. **`segment_slug` is always `wildcard` today.** `persist.py:_resolve_segment_slug` stubs to `wildcard`, so every story currently gets `why_it_matters`. SP4 must resolve the real segment (from the matched interest's `interest_segment_slug`) and pass it in, or the segment-skinned tab is a no-op.
2. **Digit-stream substring grounding can false-POSITIVE on coincidental single/short digits** (e.g. a fabricated "12" matches if "120" is in the source). It never false-negatives a real number, and the trust-critical case (a fabricated figure with novel digits like "+4%"/"$500B") is correctly rejected. Acceptable for v1; a tighter token-boundary check is a later enhancement.
3. **No semantic check that the grounded number means what the row says.** The gate proves the digits exist in the source, not that "20%" there refers to "seaborne oil via Hormuz". The single-source prompt constraint + the existing claim-verification stage (on the script) cover the narrative; row-label↔number semantic binding is out of scope for this gate.
