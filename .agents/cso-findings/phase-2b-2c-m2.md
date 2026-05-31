# CSO findings — phase-2b + phase-2c (M2)

Date: 2026-05-31. Scope: the 2b/2c diff only (not a whole-repo audit).

## Verdict: PASS — no critical/high. Two MEDIUM deploy-time follow-ups logged below.

Checked and clean:
- **Secrets:** none hardcoded (grep clean across `agents/qa`, `agents/worker`, `agents/pipeline/stages`, `src/lib/qa`, `supabase/migrations`). Worker reads `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` from `os.environ` at request time, never logs the values.
- **Input validation:** `QuestionRequest` Pydantic model (`question_text` `min_length=1`); `story_id` path param used only as a parameterized Supabase `.eq()` filter.
- **Injection:** no string-interpolated SQL/shell. Migration 0004 is static DDL (additive, no DROP, RLS + public-read on both new tables). GDELT census query = the story headline passed as a free-text HTTP query param to the existing validated `GdeltDocAdapter`.
- **Auth:** M2 is auth-free by design; `story_qa`/`story_analytics`/`detail_key_points` are service-role **content** tables (no `auth.uid()` scoping, no PII).
- **Deps:** `fastapi 0.136.3` + `uvicorn 0.48.0` added — both actively maintained, not typosquats, minimal (required because the static SPA can't hold the LLM key).
- **Logging hygiene:** error logs truncate `str(exc)[:200]`; no secret/token/PII logged.
- **Error handling:** the broad `except Exception` blocks in the worker are the intentional HTTP-200 boundary contract (log typed `ErrorResponse` + return refusal) — not swallowed; infra-error refusals are deliberately NOT cached so a transient failure can't poison `story_qa`.

## MEDIUM-1 — public Q&A endpoint has no rate limiting
`POST /api/story/{story_id}/question` is public and triggers a paid Gemini call on a cache miss. The `story_qa` exact-match cache blunts repeat-question cost, but novel questions are unbounded → a cost-abuse vector once deployed.
**Fix (deploy-time):** add per-IP / per-session rate limiting (or front it with an authenticated proxy) before exposing the worker publicly. Out of scope for the M2 data-path DoD; required before public deploy.

## MEDIUM-2 — worker has no CORS middleware
The Capacitor app calls the worker cross-origin via `NEXT_PUBLIC_QA_API_BASE_URL`, but `agents/worker/main.py` adds no CORS middleware. Browser/WebView requests from the app origin will be blocked. (The *absence* of CORS is fail-safe/restrictive — not a vulnerability — but it's a functional gap.)
**Fix (deploy-time):** add `CORSMiddleware` scoped to the app origin via an env var. **Do NOT use `allow_origins=["*"]`** on a service-role-backed endpoint. Out of scope for the M2 code DoD; required before the cross-origin deploy.
</content>
