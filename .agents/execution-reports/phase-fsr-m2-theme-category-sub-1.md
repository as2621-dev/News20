# Phase FSR-M2 — Sub-phase 1 execution report

**Sub-phase:** 1 — Pure theme→category whitelist module + tests
**Branch:** `claude/feed-source-revamp-plan-388edf` (not switched; no git state changed)
**Status:** SUCCESS

## What I implemented
A pure, deterministic, fail-loud GDELT `V2Themes` → `FeedCategory` whitelist plus its
resolver, and a WHY-asserting test suite. Imports `FeedCategory`, `TOPIC_CATEGORIES`,
`DEFAULT_CATEGORY` from `agents/pipeline/categories.py` (read-only; not edited).

## Files created (only these two)
- `/home/user/News20/agents/pipeline/theme_category.py`
- `/home/user/News20/tests/agents/pipeline/test_theme_category.py`

No other file was touched. (`git status` also shows pre-existing modifications to
`agents/ingestion/adapters/gdelt_bigquery.py`, `agents/ingestion/models.py`,
`tests/agents/ingestion/conftest.py` and a `plans/*progress.md` — these are NOT mine;
they predate this sub-agent run.)

## Design decisions
- **Matching convention:** exact theme-code match only (set-membership dict lookup).
  Pinned + commented why; prefix matching deliberately NOT implemented (Rule 2 — it
  would need its own ambiguity rules/tests and the offline DoD only needs representative
  coverage; broadening is the LIVE-E2E tuning follow-up per Open Question 2).
- **Coverage:** representative — 4 codes for each of ai/geopolitics/business/
  environment, 4 politics, 4 tech, 3 sport, 4 arts. All 8 roots covered.
- **Invariant safety:** a module-load `assert set(_TIEBREAK_PRIORITY) == set(TOPIC_CATEGORIES)`
  trips at import if `categories.py` drifts (fail loud, Rule 12).

## Pinned tiebreak rule (verbatim)
1. Highest whitelist hit-count wins (the category the most of the story's themes map to).
2. Ties broken by the fixed `_TIEBREAK_PRIORITY` order (editorial salience; lower index wins):
   `("geopolitics", "politics", "business", "ai", "tech", "environment", "sport", "arts")`.

No-whitelisted-theme list OR empty list → `DEFAULT_CATEGORY` (`"arts"`) AND a structured
`logger.warning("theme_category_no_whitelisted_theme", ..., fix_suggestion=...)`. Never
raises, never silently drops.

**Function signature:** `category_for_themes(themes: list[str]) -> FeedCategory`

## Self-review findings + fixes
- Reviewed for fail-loud: confirmed both no-hit and empty-list paths warn and fall back
  (not raise); the only `raise` is a defensive unreachable-state `AssertionError` guarded
  by the import-time invariant. No fix needed.
- Rule 2 (simplicity): single-pass hit count + one priority loop; no prefix engine, no
  speculative config. No fix needed.
- Rule 3 (surgical): two new files only; no adjacent edits.
- Warning assertion approach: chose monkeypatching the module `logger` over `caplog`
  because structlog→stdlib caplog routing is environment-fragile; patching the boundary
  is deterministic and mirrors the repo's logger-patching convention.

## Validation
`pytest tests/agents/pipeline/test_theme_category.py -q`
Summary line: **`16 passed in 0.05s`**
No missing test deps (structlog already present in the pytest env).

## Definition of done (per case)
- (a) representative theme per each of the 8 categories → expected category — **PASS**
- (b) multi-theme list resolves to documented tiebreak winner; fails if rule changes — **PASS**
  (3 tests: hit-count-wins, count-tie→priority(geopolitics over sport), not-lexical)
- (c) no-whitelisted-theme list → DEFAULT_CATEGORY AND warning emitted (incl. fix_suggestion) — **PASS**
- (d) empty list → DEFAULT_CATEGORY + warning — **PASS**
- (e) every whitelist value ∈ TOPIC_CATEGORIES (drift guard) — **PASS**
  (plus an extra: whitelist covers all 8 categories)

## Concerns
- Whitelist codes are **plausible representative** GKG codes, not verified against a live
  GKG dump (sandbox egress is 403). Exhaustive/curated theme coverage is the explicit
  LIVE-E2E tuning follow-up (Open Question 2) — not a blocker for SP1.
- Rule 7 divergence (already flagged in the phase Context): the live 8-root model in
  `categories.py` is used, not the stale "5 folded categories" in PRD/ranking-spec. This
  module intentionally maps to the 8 roots.
