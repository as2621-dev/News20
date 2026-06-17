# Phase 7c — Sub-phase 1 execution report: Catalog window 48h → 24h

## Status: SUCCESS

## What was implemented
Changed the default ingest lookback window from 2 days to 1 day (24h), preserving
the explicit override paths.

### Files modified
- `agents/ingestion/interest_keyed_pipeline.py`
  - `_DEFAULT_LOOKBACK_DAYS = 2` → `_DEFAULT_LOOKBACK_DAYS = 1`.
  - Updated the `# Reason:` comment to reflect the midnight-ET daily run + the
    05:00 ET readiness cron (SP3) as the self-heal mechanism replacing the old
    48h margin.
  - Updated the `since_utc` docstring line ("~2 days ago" → "~1 day ago").
- `scripts/run_live_batch.py`
  - `LOOKBACK_DAYS` env default `"2"` → `"1"` (line 241).
  - Updated the module docstring "LOOKBACK_DAYS (default 2)" → "(default 1)".
- `tests/agents/ingestion/test_interest_keyed_pipeline.py`
  - Imported `_DEFAULT_LOOKBACK_DAYS` and `timedelta`.
  - Added class `TestCatalogWindowDefaultLookback` with 3 tests + a small
    `_SinceRecordingAdapter` that captures the `since_utc` the pipeline passes
    to `search`.

### Override path preserved
- Pipeline: the `since_utc=...` parameter still overrides the default (untouched).
- Script: `int(os.environ.get("LOOKBACK_DAYS", "1"))` — an explicit
  `LOOKBACK_DAYS=2` still produces `now − 2 days`; only the *fallback* changed.

## Divergences
- The plan lists "the matching ingestion test"; a suitable file already existed
  (`tests/agents/ingestion/test_interest_keyed_pipeline.py`), so it was extended
  rather than creating a new one (CLAUDE.md Rule 2/3).
- The DoD phrase "the override path still honours `LOOKBACK_DAYS=2`": the env var
  itself is consumed in `run_live_batch.py`, which computes `now − LOOKBACK_DAYS`
  and passes it as `since_utc`. The pipeline's behavioural override is the
  `since_utc` parameter, so the test asserts that an explicit 2-day `since_utc`
  is honoured verbatim (the exact value `LOOKBACK_DAYS=2` drives). This proves
  the override path at the unit boundary without spinning up the full script
  (which needs live Supabase/Gemini env). Stated explicitly per Rule 1/12.

## Self code-review findings
- Logic: the default `since` is computed at call time as
  `datetime.now(timezone.utc) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)` (line
  176–178), so changing the constant is the only edit needed for the window.
  No regression to the override branch (`since_utc or (...)`). Severity: none.
- The `test_default_since_is_now_minus_one_day` test bounds the captured `since`
  by the wall-clock window around the call (`before`/`after`) to avoid a flaky
  exact-equality assertion against `datetime.now()`. Severity: none (intentional).
- Style: matches the file's existing WHY-first docstrings and adapter-fixture
  pattern. No `any`-equivalent / untyped surfaces introduced.

No critical/high/medium issues found; no fixes required.

## Validation results
- `pytest tests/agents/ingestion/test_interest_keyed_pipeline.py -q` →
  **10 passed in 0.06s** (3 new tests included).
- `ruff check` on all three changed Python files → **All checks passed!**
- Env used: `/Users/asheshsrivastava/News20/News20/.venv` (ruff 0.15.15; no
  pyproject/pytest config in repo — relied on the project venv).

## Definition of done: PASS
- Constant `== 1`: asserted by `test_default_lookback_constant_is_one_day`. PASS.
- Computed `since` is `now − 1 day` with no override: asserted by
  `test_default_since_is_now_minus_one_day`. PASS.
- `LOOKBACK_DAYS=2` override still honoured: asserted at the `since_utc`
  parameter boundary by `test_explicit_since_override_is_honoured` (and the
  script's `LOOKBACK_DAYS` env read is unchanged except its fallback). PASS.

## Concerns
- The 24h default removes the old 48h self-healing margin. This is by design and
  is mitigated by SP3's 05:00 ET readiness cron — that mitigation does not exist
  yet, so until SP3 ships, a missed midnight run leaves a thinner catalog. Flagged
  for the orchestrator's sequencing (the plan's Risk lens already notes this).
