# Phase 7 — Sub-phase 1 execution report

**Sub-phase:** Auth dependency + request/response models for the pipeline HTTP seam
**Status:** SUCCESS

## What I implemented
- A new `APIRouter` (`agents/worker/pipeline_routes.py`) that is **not** mounted on the app (SP4 mounts it).
- A bearer-token auth dependency `require_pipeline_token` reading `PIPELINE_TRIGGER_SECRET` from the shared `Settings` object (`SecretStr`, never logged). Applied **router-wide** via `APIRouter(dependencies=[Depends(require_pipeline_token)])` so every current/future route is fail-closed by default.
  - Missing header / non-`Bearer` scheme / wrong token → **HTTP 401**.
  - Empty/unconfigured secret → **HTTP 500** (fail loud — an empty secret must not silently disable the guard).
  - Token comparison uses `hmac.compare_digest` (timing-safe).
- Pydantic v2 models with typed fields + `Field` descriptions: `DailyRunRequest`, `DailyRunResponse`, `AssembleFeedRequest`, `AssembleFeedResponse`.
- Two stub endpoints behind the guard:
  - `POST /pipeline/daily` → `DailyRunResponse(run_id="stub", accepted=True)`, status **202**.
  - `POST /feed/assemble-for-user` → `AssembleFeedResponse(allocated_count=0, feed_total=30)`, status **200**.
  - Both carry `# Reason:` comments noting the bodies are stubbed for SP2/SP3.
- Structured logging on each request (`pipeline_daily_requested`, `feed_assemble_requested`) and on auth rejection / missing secret (with `fix_suggestion`).

## Files created / modified
- NEW `agents/worker/pipeline_routes.py`
- NEW `tests/agents/worker/test_pipeline_routes.py`
- NEW `tests/agents/worker/__init__.py` (test package init — other `tests/agents/*` dirs have one)
- MODIFIED `agents/shared/settings.py` — added `pipeline_trigger_secret: SecretStr` field (see divergence)

## Divergences from plan + why
1. **Edited `agents/shared/settings.py`** (explicitly permitted by the prompt). The worker's centralized secret mechanism is the `Settings` model (other keys live there as `SecretStr`). Per Rule 11 I matched that pattern rather than reading `os.environ` ad hoc. Field defaults to `SecretStr("")` so existing envs still load; the guard returns 500 until it is set.
2. **Added `ge=0` validators** on `max_total_productions` and `lookback_days` (plan specified bare `int | None`). Low-severity hardening — a negative production cap / lookback is nonsensical. Defaults remain `None`. Flagging in case SP2 expects unconstrained ints.
3. **Empty-secret → 500** behavior is mine (not spelled out in the plan, which only specified the 401 path). Rationale: an empty configured secret would otherwise make `compare_digest("", "")`-style logic ambiguous and risk an open door; failing loud is safer (Rule 12). The DoD's 401 cases are unaffected.

## Self code-review findings + fixes
- **Security (resolved):** no hardcoded production secret; test uses an obvious placeholder. Token compared with `hmac.compare_digest`. Secret is `SecretStr` and never logged (only `fix_suggestion` strings are logged).
- **Wiring bug (fixed during dev):** the dependency was initially defined but not attached to the router; fixed by constructing the router with a router-wide `dependencies=[Depends(...)]`. Verified by the 401 tests (which would pass-through without it).
- **Types/style:** full type hints, Pydantic v2, Google-style docstrings, structured logging — all present. No `any`.

## Validation results
Ruff (no project ruff config → default line-length 88, matching existing `main.py`):
```
$ .venv/bin/ruff check agents/worker/pipeline_routes.py tests/agents/worker/test_pipeline_routes.py agents/shared/settings.py
All checks passed!
$ .venv/bin/ruff format --check <same files>
3 files already formatted
```

Tests:
```
$ .venv/bin/python -m pytest tests/agents/worker/test_pipeline_routes.py -q
......                                                                   [100%]
6 passed, 1 warning in 0.25s
```
(The 1 warning is a pre-existing StarletteDeprecationWarning about `TestClient`/`httpx`, shared by all worker tests — not from this code.)

Tests cover: no-header→401, wrong-token→401, correct-token→202 for `/pipeline/daily`; same three (correct→200) for `/feed/assemble-for-user`. Secret injected via monkeypatching `pipeline_routes.Settings` (no real env/secret).

## Definition of done: PASS
"A request with no/incorrect bearer token returns 401; a request with the correct token passes auth and reaches the stub. Ruff clean." — verified by the 6 passing tests + clean ruff check/format.

## Concerns for the orchestrator
- **SP4 must add `Depends` import awareness:** the router already carries its own auth dependency, so mounting via `app.include_router(pipeline_routes.router)` is sufficient — SP4 does NOT need to re-apply auth.
- **Service-role key open question (plan §Open questions) still unanswered** — relevant to SP3 (reading any user's profile + writing `daily_feeds`). Not touched here.
- **`PIPELINE_TRIGGER_SECRET` must be set in the Railway worker env** before deploy or both endpoints return 500. Add to `.env.example` / Railway env (out of scope for this file-boundary; flag for the phase-end deploy step).
- **Stub bodies** (`run_id="stub"`, `allocated_count=0`) are placeholders — SP2/SP3 replace them; the auth tests assert status codes (not stub bodies) so they remain valid.
