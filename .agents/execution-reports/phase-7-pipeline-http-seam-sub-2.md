# Phase 7 — Sub-phase 2 execution report

**Sub-phase:** `POST /pipeline/daily` background run
**Status:** SUCCESS

## What I implemented
Replaced the SP1 `/pipeline/daily` stub with a real, non-blocking background-run handler in `agents/worker/pipeline_routes.py`:

- **`post_pipeline_daily(request, background_tasks)`** — generates a `uuid4().hex` `run_id`, resolves `max_total_productions` / `lookback_days` from the request body (falling back to the run-script defaults `8` / `2` when omitted), enqueues `_run_daily(...)` via FastAPI `BackgroundTasks.add_task`, logs `pipeline_daily_run_scheduled`, and returns `DailyRunResponse(run_id=..., accepted=True)` with **HTTP 202** immediately. `target_date` comes from the body, never the worker clock.
- **`_run_daily(target_date, max_total_productions, lookback_days, run_id)`** — the async background body. Builds the SAME clients/args `scripts/run_live_batch.py` wires: service-role Supabase (`create_client`), `interest_nodes` from the `interests` table, `interest_segment_lookup`, `followed_ids` from `user_interest_profile`, `outlets_lookup`, `GdeltDocAdapter`, story-id resolver, `since_utc = now - lookback_days`, an `ingest_fn` over `ingest_active_interests`, `LLMClient`, `GeminiTTSClient`, the poster `genai.Client`, then `await run_daily_pipeline(... enable_detail_enrichment=True, enable_editorial_rewrite=True ...)`. Logs `pipeline_daily_run_started` / `_completed`; on any exception logs `pipeline_daily_run_failed` **with `fix_suggestion`** and **re-raises** (Rule 12 — never silently swallowed).
- **`_build_interest_segment_lookup(rows)`** — local helper mirroring `run_live_batch`'s nearest-ancestor segment walk (avoids importing the script).
- **All heavy imports (`google.genai`, `supabase`, pipeline/ingestion/voice modules) are LAZY inside `_run_daily`** — verified the module pulls zero heavy pipeline modules at import time (matters for cold-start + SP4's boot test).
- Auth dependency unchanged: still router-wide from SP1 (not duplicated).

## Files touched
- `agents/worker/pipeline_routes.py` (edited — replaced the daily stub + runner)
- `tests/agents/worker/test_pipeline_routes.py` (edited — added SP2 tests; adapted one SP1 daily auth test)

## Divergence note
SP1's `test_daily_with_correct_token_reaches_stub_202` invoked the (now-real) handler with no runner patch, so its background task ran the real `_run_daily` and failed on the missing `SUPABASE_URL` env (TestClient runs background tasks and re-raises). I renamed it `test_daily_with_correct_token_reaches_handler_202` and gave it the `patched_runner` fixture so it asserts ONLY the auth contract (guard → handler → 202), unchanged in intent. All four 401 auth tests are untouched and green.

## Tests added (SP2)
- `test_daily_returns_202_with_run_id` — 202 + non-empty `run_id` (≠ `"stub"`), `accepted=True`.
- `test_daily_schedules_runner_once_with_parsed_target_date` — runner scheduled **exactly once** with `target_date == date(2026,6,16)`, plus parsed limits + matching `run_id`.
- `test_daily_uses_defaults_when_limits_omitted` — omitted limits fall back to `_DEFAULT_MAX_TOTAL_PRODUCTIONS` / `_DEFAULT_LOOKBACK_DAYS`.
- `test_daily_response_returns_before_runner_completes` — 202 returned independently of the (slow) runner (background, not blocking).
- `test_daily_with_missing_target_date_returns_422` / `test_daily_with_malformed_target_date_returns_422` — invalid body → 422, runner never scheduled.

Patch point: `agents.worker.pipeline_routes._run_daily` (AsyncMock via monkeypatch) — no live clients built, mock records the call.

## Validation (D) — PASS

```
$ .venv/bin/ruff check agents/worker/pipeline_routes.py tests/agents/worker/test_pipeline_routes.py
All checks passed!
$ .venv/bin/ruff format --check agents/worker/pipeline_routes.py tests/agents/worker/test_pipeline_routes.py
2 files already formatted
```

```
$ .venv/bin/python -m pytest tests/agents/worker/test_pipeline_routes.py -q
............                                                             [100%]
12 passed, 1 warning in 0.20s
```
(The 1 warning is the pre-existing StarletteDeprecationWarning about `TestClient`/`httpx`, shared by all worker tests — not from this code.)

Lazy-import check:
```
$ .venv/bin/python -c "import sys; before=set(sys.modules); from agents.worker import pipeline_routes; ..."
heavy pipeline modules pulled at import: []
handler present: True runner present: True
```

## Definition of done — PASS
"POST /pipeline/daily returns 202; the mock is scheduled exactly once with the parsed target_date; the HTTP response returns before the mock completes (asserts background, not blocking)." — all three verified by the SP2 tests above.

## Concerns for the orchestrator
- **Runner arg fidelity is asserted only at the `_run_daily` boundary**, not inside it. The body wires the exact clients/args from `run_live_batch.py` (verified by reading both), but a unit test of the live-client construction would need real env/mocked SDKs — out of scope for SP2's mock-the-runner contract. SP4's boot test + a future live smoke should exercise the real path.
- **Env requirements** for an actual run: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY` must be set on the worker (failure is logged with `fix_suggestion`, not swallowed). Same service-role open question SP1 flagged.
- **Defaults `8` / `2`** mirror `run_live_batch.py`'s `MAX_PRODUCE` / `LOOKBACK_DAYS` defaults; `INGEST_SOURCE` is fixed to DOC here (the script's BigQuery branch was not ported — flag if 7c needs BigQuery via the HTTP seam).
- Did NOT touch `/feed/assemble-for-user` (SP3), `main.py`, or `settings.py`. No commit.
