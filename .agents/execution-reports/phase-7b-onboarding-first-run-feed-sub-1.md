# Phase 7b — Sub-phase 1 execution report: JWT-scoped `/feed/assemble-mine`

**Status:** SUCCESS

## What was implemented
Added `POST /feed/assemble-mine` to the worker pipeline router — a single-user feed
assembly endpoint authenticated by the **caller's own Supabase access token**, not the
shared `PIPELINE_TRIGGER_SECRET`. It reuses the existing `_assemble_for_user(user_id,
feed_date)` helper and returns the same `AssembleFeedResponse`.

### `agents/worker/pipeline_routes.py`
- **`_ASSEMBLE_MINE_PATH = "/feed/assemble-mine"`** — path constant used by both the
  route and the guard exemption.
- **`_build_supabase_for_auth()`** — builds the Supabase client used only for
  `auth.get_user(token)`. Built the same way as `_build_service_role_supabase` (lazy
  import, same env vars) so tests patch ONE clean seam. Verification identity comes
  from the explicitly-passed JWT, never the client key.
- **`verify_supabase_user(authorization)`** — FastAPI dependency. Parses the
  `Authorization: Bearer <jwt>` header, validates via `supabase.auth.get_user(token)`,
  and returns the verified `user.id`. Returns **401** on: missing/non-Bearer header,
  empty token, `get_user` raising (invalid/expired), or a response with no user. All
  failures logged (warning) with `fix_suggestion`; the underlying auth error is never
  leaked to the caller.
- **`require_pipeline_token(request, authorization)`** — now takes `Request` and
  **short-circuits (returns) for `_ASSEMBLE_MINE_PATH`** so the shared-secret guard
  does NOT apply to the JWT route. Every other path is unchanged.
- **`AssembleMineRequest`** — body model with ONLY `feed_date: date | None`
  (default `None` → today UTC in the handler). **No `user_id` field** by design.
- **`post_feed_assemble_mine(...)`** — the route. `feed_date = request.feed_date or
  today-UTC`; `user_id` is the injected `verified_user_id` (token only). Calls
  `_assemble_for_user`, maps `LookupError → 404`, any other error → 500 (logged).

### `tests/agents/worker/test_pipeline_routes.py`
Added 9 tests under a new section, mocking at the boundary (`_build_supabase_for_auth`
returns a fake client whose `auth.get_user` returns a fake user; `_assemble_for_user`
is an `AsyncMock`):
- no header → 401; invalid token (`get_user` raises) → 401; token resolves to no user
  → 401 (all assert `_assemble_for_user` never called).
- shared secret sent as the bearer → reaches the JWT dependency (200), proving the
  shared-secret guard does NOT gate this route.
- valid JWT → 200 + count; asserts `_assemble_for_user` called with the **token's**
  user_id and parsed feed_date.
- **body `user_id` is ignored** — token id wins, `!= "someone-elses-id"`.
- omitted `feed_date` defaults to today UTC.
- `LookupError` → 404.
- real-app test: `/feed/assemble-mine` with no token → 401 through the mounted app
  (its own guard, exempt from the shared-secret guard).

## Divergences (+ why)
- **Guard exemption by path in `require_pipeline_token`** rather than a second
  un-guarded router. Reason: `main.py` is out of scope (phase file restricts touched
  files to `pipeline_routes.py` + the test). The router-wide dependency on the exported
  `router` cannot be excluded per-route in FastAPI (verified empirically — nested
  child routers still inherit parent router-level `dependencies`). Exact-path
  short-circuit is the minimal single-file solution that keeps `main.py` untouched and
  the route mounted. It is path-exact and documented inline.
- **`_build_supabase_for_auth` duplicates `_build_service_role_supabase`'s body.**
  Kept as a separate, named, documented helper so the JWT seam is patchable
  independently and the intent (token-only verification) is explicit. Minor, justified.

## Review findings + fixes
No critical/high issues found. Notes (all accepted, no fix needed):
- Broad `except Exception` in `verify_supabase_user` is intentional (supabase-py raises
  varied auth errors → all map to 401), logged not swallowed (`noqa: BLE001`).
- Service-role key reused for `get_user(jwt)` is safe — identity comes from the token.

## Validation results
```
$ python -m pytest tests/agents/worker/test_pipeline_routes.py -q
30 passed, 1 warning in 0.66s
```
(21 existing worker tests + 9 new JWT tests; full `tests/agents/worker/` dir also 30 passed.)
```
$ ruff check agents/worker/pipeline_routes.py tests/agents/worker/test_pipeline_routes.py
All checks passed!
```
Real-app OpenAPI verified: `/feed/assemble-mine`, `/feed/assemble-for-user`,
`/pipeline/daily` all present; new route mounted, existing routes intact.

Venv used: `/Users/asheshsrivastava/News20/News20/.venv` (the worktree had no local
venv; the main worktree's venv has fastapi/pytest/supabase installed).

## Definition of done — PASS
- no/invalid JWT → 401 ✔ (3 tests: missing, invalid, no-user)
- valid JWT assembles THAT user's feed; `_assemble_for_user` called with the token's
  user_id (not body) → 200 with count ✔
- body passing a different `user_id` is ignored (no such field honoured) ✔
- ruff clean ✔; existing 21 worker tests stay green ✔

## Concerns for the orchestrator
- **Guard exemption is path-exact.** If a future route is added under a shared prefix
  that overlaps `/feed/assemble-mine`, the exact-match exemption would need revisiting.
  Today there is exactly one exempt path.
- **SP2 contract:** the client calls `POST /feed/assemble-mine` with
  `Authorization: Bearer <supabase session access_token>` and an optional
  `{"feed_date": "YYYY-MM-DD"}` body. Response is `AssembleFeedResponse`
  (`allocated_count`, `feed_total`). 401 = bad/expired session; 404 = onboarded-but-no-
  profile; 500 = worker error — SP2's failure path should treat any non-200 as
  non-fatal and fall back to the global feed.
- No `main.py` change was needed (route is on the already-mounted `router`).
