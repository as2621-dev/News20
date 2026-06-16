# Phase 7 — Sub-phase 4 execution report: Mount pipeline router + boot/deploy smoke

**Status:** SUCCESS

## What shipped
Mounted `pipeline_routes.router` on the real worker app (`agents/worker/main.py`)
and proved the worker still boots cleanly with both new routes present and the
router's auth guard live.

### `agents/worker/main.py`
- Added `from agents.worker.pipeline_routes import router as pipeline_router` (a
  light import — the router module's heavy pipeline imports are all lazy inside its
  handlers, confirmed by SP1–SP3 and re-verified here).
- `app.include_router(pipeline_router)` — included AS-IS; auth is NOT re-applied
  (the router carries its own router-wide `require_pipeline_token` guard).
- Added a dependency-free `GET /healthz` liveness route returning `{"status":"ok"}`.
  **Note / conflict surfaced (Rule 7/12):** the phase DoD names `GET /healthz`, but
  no health route existed on the worker (only `/api/story/...`, `/api/voice/...`,
  `/api/sources/search`). Rather than re-point the smoke at an unrelated route, I
  added the named `/healthz` to the allowed file (`main.py`). This is a real,
  load-free liveness probe Railway can also use; it touches only an allowed file.

### `tests/agents/worker/test_pipeline_routes.py`
Added a `real_app_client` fixture (TestClient over `agents.worker.main:app`) and 4
SP4 tests, keeping all 17 prior tests untouched and green:
- `test_real_app_boots_and_serves_health` — real app constructs; `GET /healthz` → 200.
- `test_real_app_openapi_exposes_both_pipeline_paths` — `/pipeline/daily` and
  `/feed/assemble-for-user` both in `app.openapi()["paths"]`.
- `test_real_app_pipeline_daily_requires_auth` — `POST /pipeline/daily` no token →
  401 through the REAL app (guard live once mounted).
- `test_importing_worker_main_does_not_eager_import_pipeline` — fresh-subprocess
  probe asserts `daily_batch` / `feed_assembly` / `interest_keyed_pipeline` are
  absent from `sys.modules` after importing `main` (cold-start stays cheap).

### `requirements.txt`
Not touched — mounting surfaced no missing dependency (imports are lazy).

## Validation (D)

ruff check + format --check on changed files:
```
All checks passed!
---FORMAT---
2 files already formatted
```

Full worker test suite (`.venv/bin/python -m pytest tests/agents/worker/ -q`):
```
.....................                                                    [100%]
21 passed, 1 warning in 0.64s
```
(17 prior + 4 new; the lone warning is the pre-existing Starlette/httpx
TestClient deprecation, unrelated to this change.)

Import smoke (`.venv/bin/python -c "from agents.worker.main import app; print(len(app.routes))"`):
```
11
```

Eager-import check (`...print('HEAVY:', [...])`):
```
HEAVY: []
```

## Definition of done (E) — PASS
- TestClient(app) import-and-boot test passes ✓ (`test_real_app_boots_and_serves_health`).
- `GET /healthz` returns 200 ✓.
- `/pipeline/daily` and `/feed/assemble-for-user` present in `app.openapi()["paths"]` ✓.
- `ruff check` clean ✓.

## Concerns
- **`/healthz` was added, not pre-existing.** The DoD assumed a health route already
  existed; it did not. I added a minimal one in the allowed `main.py`. If a health
  route is later wanted elsewhere or named differently, reconcile then.
- No commit made (orchestrator commits the whole phase). Only the three allowed
  files were touched.
- Open question from the phase file (worker has the Supabase service-role key in
  env) is unchanged by SP4 — it remains a deploy-env concern for SP3's runtime path.
