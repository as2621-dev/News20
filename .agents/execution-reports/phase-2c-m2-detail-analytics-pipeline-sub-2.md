# Phase 2c — Sub-phase 2 execution report

**Sub-phase:** GDELT coverage stage (deterministic)
**Status:** SUCCESS
**Tree:** main working tree (no worktree; siblings building SP3 + phase-2b concurrently on disjoint files)
**Date:** 2026-05-31

## What was implemented

1. **`agents/pipeline/stages/coverage_gdelt.py` (NEW, ~470 LoC, under the 500 limit)** — the GDELT coverage census stage.
   - **`build_coverage_report(story, story_segment_slug, outlets_lookup, adapter) -> CoverageReport`** (async). Flow: `adapter.search(headline, since_utc=now-2d)` → normalize + de-dup covering domains → resolve each against the injected `outlets_lookup` (domain→`BiasLean`) → counts → mode-correct `CoverageReport`.
   - **Mode is deterministic by segment** (`coverage_mode_for_segment`, Rule 5): `geopolitics` → `partisan`, every other segment (incl. unknown) → `reach`. Default `partisan` only for the contested geopolitics segment.
   - **Partisan mode:** L/C/R counts from rated outlets + `blindspot_lean` via the REUSED `derive_blindspot_lean` (`persist_helpers`) on the **rated subset** (unrated outlets count toward `coverage_outlet_count` but never invent a lean and never dilute the blindspot share denominator).
   - **Reach mode:** `coverage_outlet_count` (distinct) + `coverage_momentum` (from the seendate spread: ≤6h breaking / ≤36h developing / else settled) + `coverage_originating_outlet_name` (earliest seendate) + up to 5 `coverage_notable_outlet_names`.
   - **Normalization** (`normalize_coverage_domain`): strips `www.`, drops aggregator/social/PR-wire **noise** (`news.google.com`, `youtube.com`, `prnewswire.com`, …), drops **foreign-edition ccTLDs** (`.ru/.cn/.ir/…`), and **collapses affiliate subdomains** to their apex (`finance.yahoo.com` + `sports.yahoo.com` → one `yahoo.com`).
   - **GDELT failure is NON-FATAL** (Decision #3): `AdapterFetchError` is caught → falls back to the story's own `covering_outlets`-derived counts, logging a `fix_suggestion`. An empty-but-valid census also degrades to the fallback (never a silent zero for a story we know is covered). The function **never raises**.
   - **Purity:** no live network/DB inside the function — the injected `adapter` does the (throttled) GDELT I/O; `outlets_lookup` is a plain dict (NOT a live `outlets` read). **REUSES** the existing `GdeltDocAdapter` (no new GDELT client) and `derive_blindspot_lean` (no re-implemented coverage math).

2. **`tests/agents/pipeline/test_coverage_gdelt.py` (NEW, 14 tests, all pass)** — GDELT mocked at the **adapter boundary** (`adapter.search` via `AsyncMock`); no network/key/throttle. Covers the five DoD assertions plus pure-function + edge tests:
   - **(a)** affiliate/foreign/aggregator noise filtered (11 raw articles → 8 distinct outlets; two yahoo editions collapse to one apex; `rt.ru` + `news.google.com` dropped).
   - **(b)** partisan counts match exactly the lookup-rated domains (L=6, C=1, R=0).
   - **(c)** the >70% blindspot branch fires (`blindspot_lean == "right"`), plus a balanced-set test asserting NO false-positive blindspot (tie → None).
   - **(d)** a `sport` story → `coverage_mode == "reach"` with `coverage_momentum == "developing"` + `coverage_originating_outlet_name == "Reuters"` (earliest seendate); a `markets` tight-burst test asserts `"breaking"`.
   - **(e)** `AdapterFetchError` → graceful `covering_outlets` fallback in BOTH partisan and reach modes (asserts non-zero count + correct mode), plus an empty-census fallback test (Rule 9/12 — fails if a fabricated count, wrong mode, or silent-empty-on-error slips through).
   - Pure-function tests: `coverage_mode_for_segment` (parametrized) + `normalize_coverage_domain` (foreign drop / noise drop / affiliate collapse / www strip).

3. **`reference/integrations.md` (MODIFIED, additive/surgical, +9/−5)** — reconciled the GDELT-not-NewsAPI reality: added a callout that the **live v1 source is keyless GDELT DOC 2.0** (`GdeltDocAdapter`, ≤1-req/5s, `hybridrel`, `maxrecords ≤ 250`, 1–3d) and that the Phase 2c Coverage census **reuses** it; marked the existing NewsAPI/MediaStack/Alpha Vantage/HN/Product Hunt rows as *planned* (not wired), and added a GDELT *(LIVE)* row.

## Divergences / conflicts surfaced (Rule 7/12)

1. **Signature adds `story_segment_slug` as an explicit 2nd positional arg** — the brief framed it `build_coverage_report(story, outlets_lookup, adapter)`, but `CanonicalStory` carries **no segment field** (verified: `agents/ingestion/models.py`; segment is resolved separately at persist time and defaults to `wildcard` — `persist.py:_resolve_segment_slug`). Picking mode "by `story_segment_slug`" therefore requires the slug to be passed in. Final signature: **`build_coverage_report(story, story_segment_slug, outlets_lookup, adapter)`**. SP4 must pass the resolved slug (from `_resolve_segment_slug`, which today returns `"wildcard"` → reach mode until SP4 backfills the real segment from the matched interest's `interest_segment_slug`).
2. **`outlets_lookup` is INJECTED, not the module's dead static map.** The brief mandates resolving against an injected domain→lean dict (the seeded `outlets` table), so I did NOT reuse `derive_coverage_counts` (which is hardwired to `persist_helpers._OUTLET_BIAS_BY_DOMAIN`, the stop-gap static map). I reuse `derive_blindspot_lean` (it operates on a counts dict, lookup-agnostic) and do only the trivial rated-bucket counting against the injected lookup. SP4 supplies the lookup (load the seeded `outlets` rows into `{outlet_domain: outlet_bias_lean}` once per batch).
3. **Blindspot needs a UNIQUE minimum lean** — `derive_blindspot_lean` (correctly) names no blindspot on a tie (two equally-minimal leans). The fallback test fixture was adjusted to 6L/2C/1R (unique right minimum) after the first draft used 6L/1C/1R (a center/right tie → correctly None). This is the existing helper's contract, not new behavior — surfaced so SP4 knows sparse/tied coverage legitimately yields `blindspot_lean = None`.
4. **`coverage_notable_outlet_names` is ordered by GDELT's `hybridrel` ranking**, not earliest-seendate. "Who broke it" is reported separately via `coverage_originating_outlet_name` (earliest seendate). Docstring corrected to not overclaim ordering.

## Validation results

| Check | Command | Result |
|---|---|---|
| Ruff lint | `.venv/bin/ruff check agents/pipeline/stages/coverage_gdelt.py tests/agents/pipeline/test_coverage_gdelt.py` | **PASS** ("All checks passed!") |
| Ruff format | `.venv/bin/ruff format --check agents/pipeline/stages/coverage_gdelt.py` | **PASS** ("1 file already formatted") |
| Pytest | `.venv/bin/python -m pytest tests/agents/pipeline/test_coverage_gdelt.py -q` | **PASS** (14 passed) |
| Import smoke | `python -c "from agents.pipeline.stages.coverage_gdelt import ..."` | **PASS** (import OK) |

`next build` / `npm test` intentionally NOT run (sibling shares the tree). No live GDELT calls (mocked at the adapter boundary).

## Definition of done (per item)

| DoD item | Status |
|---|---|
| One function turns a story into a populated, mode-correct `CoverageReport` from a (mocked) GDELT call | **PASS** |
| (a) affiliate/foreign noise filtered | **PASS** |
| (b) partisan counts match the rated domains | **PASS** |
| (c) >70% blindspot branch fires correctly (+ no false-positive on balance) | **PASS** |
| (d) markets/sport → `coverage_mode='reach'` with momentum + originating outlet | **PASS** |
| (e) GDELT error → graceful `covering_outlets` fallback (no raise, no silent zero) | **PASS** |

**VALIDATION: PASS. DoD: PASS.**

## Files touched
- `agents/pipeline/stages/coverage_gdelt.py` (new)
- `tests/agents/pipeline/test_coverage_gdelt.py` (new)
- `reference/integrations.md` (modified, additive)

## Contract SP4 wires into the orchestrator

```python
from agents.pipeline.stages.coverage_gdelt import build_coverage_report

# SP4: in orchestrate_story, after verification, before persist —
coverage_report = await build_coverage_report(
    story=story,                       # CanonicalStory
    story_segment_slug=segment_slug,   # from persist._resolve_segment_slug (today "wildcard" → reach)
    outlets_lookup=outlets_lookup,     # {outlet_domain: bias_lean} loaded ONCE from the seeded `outlets` table
    adapter=gdelt_adapter,             # the SHARED GdeltDocAdapter instance (honors the ≤1-req/5s throttle)
)
# -> CoverageReport: persist coverage_mode + (partisan L/C/R + blindspot_lean) OR
#    (coverage_outlet_count + coverage_momentum + coverage_originating_outlet_name +
#     coverage_notable_outlet_names) into the story_trust reach columns (SP1's 0004 ALTER).
```

### Concerns for SP4 (Rule 12)
- **Segment plumbing:** until SP4 backfills the real segment, `_resolve_segment_slug` returns `"wildcard"` → every story gets `reach` mode. Geopolitics stories will NOT get partisan coverage until the segment is resolved from the matched interest. Flag/fix in SP4.
- **`outlets_lookup` source:** load it ONCE per batch from the **seeded** `outlets` table (`{outlet_domain: outlet_bias_lean}`). The seed (`supabase/seed/outlets.sql`) must be applied (gated in SP1) or every domain is unrated → partisan counts are all-zero and blindspot is always None. The function does NOT read `outlets` live.
- **Throttle budget (OQ#1):** pass the SAME `GdeltDocAdapter` instance ingestion uses so the ≤1-req/5s shared lock is honored — a per-story coverage call competes with per-interest ingestion for the 5s budget (≥5s × N stories). Consider a separate rate-budgeted pass if the daily batch is large.
- **Partisan blindspot is null on sparse/tied coverage** (by design — `derive_blindspot_lean`'s contract). Not a bug; render accordingly.
```
