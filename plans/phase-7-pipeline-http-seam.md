# Phase 7: Pipeline HTTP seam on the Railway worker

**Milestone:** M7 — Production feed automation & first-run onboarding feed
**Status:** Not started
**Estimated effort:** M

## Goal
The Python daily pipeline and single-user feed assembly are callable over authenticated HTTP from the existing Railway FastAPI worker — the shared seam that both the onboarding first-run feed (Phase 7b) and the midnight trigger (Phase 7c) depend on.

## Context
- `agents/worker/main.py` is the deployed FastAPI app on Railway (Dockerfile → `uvicorn agents.worker.main:app`). Today it serves Q&A / voice / sources only — no pipeline endpoint.
- `run_daily_pipeline(target_date, ...)` (`agents/pipeline/daily_batch.py`) is the batch entry, invoked today only by `scripts/run_live_batch.py`.
- `assemble_user_feed(...)` + `write_daily_feed(...)` (`agents/pipeline/feed_assembly.py`) are **pure single-user functions** and can build one user's feed on demand from a story pool; `write_daily_feed` is already idempotent on `(feed_user_id, feed_date)`.
- A full daily run is long (minutes) → the daily endpoint MUST run as a background task and return `202`, not block the HTTP response.

## Sub-phases

### Sub-phase 1: Auth dependency + request/response models
- **Files touched:** new `agents/worker/pipeline_routes.py`, new `tests/agents/worker/test_pipeline_routes.py`
- **What ships:** an `APIRouter` skeleton in `pipeline_routes.py` with a bearer-token dependency reading `PIPELINE_TRIGGER_SECRET` from settings, plus Pydantic models — `DailyRunRequest{ target_date: date, max_total_productions: int|None, lookback_days: int|None }`, `DailyRunResponse{ run_id: str, accepted: bool }`, `AssembleFeedRequest{ user_id: str, feed_date: date }`, `AssembleFeedResponse{ allocated_count: int, feed_total: int }`. No business logic yet (stub `200`s behind the auth guard).
- **Definition of done:** `pytest tests/agents/worker/test_pipeline_routes.py` — a request with no/incorrect bearer token returns `401`; a request with the correct token passes auth and reaches the stub. Ruff clean.
- **Dependencies:** none

### Sub-phase 2: `POST /pipeline/daily` (background run)
- **Files touched:** `agents/worker/pipeline_routes.py`, `tests/agents/worker/test_pipeline_routes.py`
- **What ships:** the daily endpoint validates `DailyRunRequest`, enqueues `run_daily_pipeline` via FastAPI `BackgroundTasks` (constructing the same clients `run_live_batch.py` wires), and returns `202` + a `run_id` immediately. `target_date`, `max_total_productions`, and `lookback_days` come from the request (not the worker clock).
- **Definition of done:** `pytest` with `run_daily_pipeline` patched (`AsyncMock`) — `POST /pipeline/daily` returns `202`; the mock is scheduled exactly once with the parsed `target_date`; the HTTP response returns before the mock completes (asserts background, not blocking).
- **Dependencies:** Sub-phase 1

### Sub-phase 3: `POST /feed/assemble-for-user` (single-user, partial-friendly)
- **Files touched:** `agents/worker/pipeline_routes.py`, `tests/agents/worker/test_pipeline_routes.py`
- **What ships:** loads one user's interests / `user_feed_allocation` / entity follows + the **global ready-story pool** (`stories` with a current digest, audio + poster), calls `assemble_user_feed` then `write_daily_feed`, and returns `{ allocated_count, feed_total: 30 }`. Fewer than 30 ready stories → fewer slots (never invents). Idempotent (re-call writes no duplicate rows).
- **Definition of done:** `pytest` with a seeded fake pool — a pool of 24 ready stories returns `allocated_count == 24` and writes 24 `daily_feeds` rows; a second identical call writes 0 additional rows (idempotent); an empty pool returns `allocated_count == 0` without raising.
- **Dependencies:** Sub-phase 2 _(serialized: both edit `pipeline_routes.py`)_

### Sub-phase 4: Mount router on the worker + boot/deploy smoke
- **Files touched:** `agents/worker/main.py`, `requirements.txt` (only if a pipeline import pulls a missing dep), `tests/agents/worker/test_pipeline_routes.py`
- **What ships:** `pipeline_routes.router` registered on `app`; heavy pipeline imports done lazily inside handlers so worker cold-start/`/healthz` is unaffected; both routes appear in the OpenAPI schema.
- **Definition of done:** a `TestClient(app)` import-and-boot test passes; `GET /healthz` still `200`; `/pipeline/daily` and `/feed/assemble-for-user` are present in `app.openapi()["paths"]`; `ruff check` clean.
- **Dependencies:** Sub-phase 2, Sub-phase 3

## Phase-level definition of done
With the worker running locally, an authenticated `POST /feed/assemble-for-user` for a seeded user writes that user's `daily_feeds` rows from the ready pool and returns the count, and `POST /pipeline/daily` returns `202` and schedules the run — both reject unauthenticated calls with `401`, and the worker's existing endpoints/boot are unaffected.

## Out of scope
- Wiring Trigger.dev to these endpoints (Phase 7c).
- Onboarding client calls + partial UX (Phase 7b).
- Any change to ranking/allocation logic — this phase only *exposes* existing functions.
- Image batch work (Phase 7d).

## Open questions
- Does the worker process already have the Supabase **service-role** key in env (needed to read any user's profile + write their `daily_feeds` server-side)? If only the anon key is present, Sub-phase 3 needs the service-role key added to the Railway worker env. _(Assumption: service-role key is available to the worker; flag if not.)_

## Self-critique

**Product lens:** PASS. This phase ships no user-facing feature on its own, but it is the prerequisite for the M1 "a new user can sign in, pick interests, and listen to ~20–30 fresh stories chosen for them" promise (delivered in 7b) and the daily-habit automation (7c). No scope creep — it only exposes functions that already exist.

**Engineering lens:** PASS with notes. Stack-aligned (FastAPI worker + Pydantic, both in the master plan). DoDs are concretely checkable (status codes, row counts, mock call assertions). Sub-phase 4 only *mounts* the router — it does not lock in an API shape that 1–3 should keep flexible (the shape is set in SP1's models, deliberately first). SP2 and SP3 both edit `pipeline_routes.py`, so SP3 is serialized after SP2 to avoid a same-file conflict between fresh-context sub-agents.

**Risk lens:** PASS with flags. File-boundary: SP1/2/3 share `pipeline_routes.py` (handled by ordering); SP4 is the only one touching `main.py`. Tests: every sub-phase has a pytest DoD that fails on wrong behavior, not just on compile (Rule 9). Reversibility: no migrations, no data deletion — fully reversible. Long-run timeout risk explicitly mitigated by the background-task pattern in SP2. Env risk (service-role key) surfaced as an open question rather than assumed silently.

**Irreversible sub-phases:** none.
