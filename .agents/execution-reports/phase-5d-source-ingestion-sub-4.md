# Phase 5d SP4 — Trigger.dev source-ingestion cron + the two wiring gaps SP3 flagged

**Status:** SUCCESS · **Date:** 2026-06-17

## Implemented
1. **`trigger/sourceIngestion.ts` (new)** — a Trigger.dev **v4 `schedules.task`**
   (`id: "source-ingestion"`, `cron: "0 */2 * * *"`). It lists every user with ≥1
   active followed source (a `user_content_sources` row, via a service-role Supabase
   read) and fans `run_source_ingestion` out **per user** by an authenticated
   `POST ${WORKER_BASE_URL}/ingestion/sources` to the Railway worker. Per-user counts
   (fetched / promoted / dropped) are logged (`source_ingestion_user_completed`) plus
   an aggregate summary — no silent caps. One user's dispatch failure is logged
   (`source_ingestion_user_failed`) and skipped, never aborting the batch.
   Gated behind the shared `PIPELINE_CRON_ENABLED` kill-switch (reused from
   `dailyPipeline.ts`): OFF by default, so the cron is provably frozen (no Supabase
   read, no worker POST) until the M5 deploy enables it.
2. **`agents/pipeline/orchestrator.py` — source-origin poster pass-through (SP3 gap #1
   closed):** in `generate_poster_bytes`, when the story's outlet domain is
   source-origin (`is_source_origin_domain(story.canonical_primary_outlet_domain)` —
   imported from `agents/ingestion/dedup.py`, NOT duplicated) it passes
   `supplied_poster_image_url = story.canonical_social_image_url` to the M0 builder,
   so SP3's poster-skip branch actually fires end-to-end. The news path is byte-for-byte
   unchanged: the kwarg is only added when a supplied image exists, so 2-arg builder
   stubs are unaffected. Source stories now also produce a poster when no genai client
   is injected (generation is skipped, so the client is irrelevant); news stories with
   no client still return None as before. A source story missing its image logs a
   loud warning (Rule 12).
3. **`agents/ingestion/source_pipeline.py` — `last_fetched_at` write-back (SP3 gap #3
   closed):** added an optional injected `mark_source_polled: Callable[[str, datetime],
   Awaitable[None]]` callback, invoked as `await mark_source_polled(source_id, now)`
   immediately after a source is **successfully** polled. The SP4 orchestrator injects
   a callback that stamps `content_sources.last_fetched_at = now` so the
   `CadenceScheduler` throttle engages in production. It is NOT called for a
   cadence-skipped source or a failed fetch (so a transient failure is retried next
   run). Default None keeps the pipeline pure (no DB) for unit tests — matching the
   module's existing injection convention (scheduler / clusterer / adapters all
   injected).

## How I matched the existing trigger/worker seam
- The worker seam is **HTTP, not a Python bridge**: `dailyPipeline.ts` reaches the
  Railway worker via an authenticated `POST ${WORKER_BASE_URL}/pipeline/daily` with a
  `Bearer ${PIPELINE_TRIGGER_SECRET}` header, throwing on non-2xx. I copied that exact
  shape for `dispatchUserSourceIngestion` (`POST .../ingestion/sources`, same bearer
  auth, same throw-on-non-2xx).
- The per-user fan-out + service-role Supabase read mirrors `feedReadinessCheck.ts`
  (`createServiceRoleClient`, `SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_URL` fallback,
  `persistSession:false`). The donor's `trigger/ingestion-cron.ts` 2h-cron → list users
  → per-user dispatch shape (`reference/sources-reuse-map.md` §PORT) is preserved; the
  donor's in-process `triggerAndWait` becomes an HTTP POST because News20's worker is a
  separate Python service (the existing seam).
- `cronEnabled()` is imported from `dailyPipeline.ts` (one shared kill-switch, not a new
  one). No `trigger.config.ts` edit needed: it registers tasks via `dirs: ["./trigger"]`,
  so the new task is auto-discovered.

## Divergences + why
- **Cron is a plain UTC 5-field string `"0 */2 * * *"`**, not a `{ pattern, timezone }`
  object like the daily/readiness crons. Reason: ingestion cadence is a fixed *interval*,
  not a wall-clock calendar moment, so DST handling is irrelevant — and the plan SP4
  spec gives the literal `"0 */2 * * *"`.
- **Worker path `/ingestion/sources` does not yet exist** on the worker
  (`agents/worker/`). That endpoint is out of SP4's allowed files (the brief scopes SP4
  to the trigger + the two Python wiring gaps). Because the cron is gated OFF, no live
  call is ever made; the path is the contract the worker must implement at M5 deploy.
  Flagged below.
- **Write-back is a callback, not a direct DB write** in `source_pipeline.py`. The module
  is explicitly designed pure (SP3's report + its docstring: "no DB, no network"). A
  callback keeps it unit-testable while giving production the seam to stamp
  `last_fetched_at`. SP3 said write-back was "the orchestrator's job"; the brief asked
  for it here — the callback satisfies both (the pure pipeline fires it; the orchestrator
  supplies the DB-writing implementation).

## Review findings + fixes (self-review of the diff)
- First pytest run: my new `TestLastFetchedWriteBack` class failed (4 tests) because the
  async test class lacked `@pytest.mark.asyncio` (the existing `TestRunSourceIngestion`
  carries it at class level). Added the decorator → all green. (No production-code bug;
  test-harness wiring only.)
- Confirmed `is_source_origin_domain` import into `orchestrator.py` introduces no circular
  import (pytest collects + the orchestrator module imports cleanly).
- Confirmed the news poster path is unchanged: the supplied kwarg is only added when an
  image exists, so the existing `test_orchestrator.py` 2-arg builder stubs still pass
  (verified — full pipeline suite green, no regression).
- Biome flagged 2 formatting nits in the new TS files (long throw line, mock chaining);
  applied `biome check --write`; re-verified clean.

## Validation
- **Python ruff:** `ruff check agents/pipeline/orchestrator.py agents/ingestion/source_pipeline.py <2 test files>` → **All checks passed!**  `ruff format` → 4 files left unchanged.
- **Python pytest:** `pytest tests/agents/ingestion/ tests/agents/pipeline/ -q` →
  **408 passed, 3 warnings in 3.37s** (SP3 left 400; +8 = 4 write-back + 4
  orchestrator source-poster; **no regressions**). All externals mocked — no network,
  no yt-dlp, no xAI, no Playwright, no DB.
- **TS typecheck:** `npx tsc --noEmit` → exit **0** (no errors).
- **TS biome:** `npx biome check trigger/sourceIngestion.ts tests/lib/trigger/sourceIngestion.test.ts` → **No fixes applied** (clean).
- **TS vitest:** `npx vitest run tests/lib/trigger/sourceIngestion.test.ts` → **7 passed**.
- **Cron validity:** `"0 */2 * * *"` is a valid 5-field cron (minute=0, hour=*/2,
  dom=*, month=*, dow=*) → fires at the top of every even hour. Asserted in the cron
  test (5 fields, field[0]=="0", field[1]=="*/2").
- ⚠ Did NOT deploy or trigger live (the live cron makes outward API calls). Kept gated.

## Definition of done: PASS
- ✅ Valid **v4 `schedules.task`** (uses `schedules.task`, **never** `client.defineJob`).
- ✅ A dev/local fan-out lists per-user and would call `run_source_ingestion` (worker call
  mocked: `runSourceIngestionFanOut` test dispatches once per distinct user; the real
  `dispatchUserSourceIngestion` test proves the bearer-auth POST seam with `fetch` mocked).
- ✅ Cron expression validated (5-field every-2-hours).
- ✅ Run logs counts (per-user fetched/promoted/dropped + aggregate summary; failures
  surfaced, not swallowed).
- ✅ **SP3 gap #1 closed** — orchestrator passes the supplied image for source-origin
  stories: `test_orchestrator_source_poster.py` proves youtube.com → kwarg passed,
  bbc.com → kwarg absent, source story posters even without a genai client.
- ✅ **SP3 gap #3 closed** — `last_fetched_at` write-back: `TestLastFetchedWriteBack`
  proves the callback fires once with `(source_id, now)` on success, and NOT for a
  not-due or failed source.

## Concerns
1. **Worker endpoint `/ingestion/sources` is unbuilt (deploy-time gap).** The trigger
   POSTs to it, but `agents/worker/` has no such route yet (out of SP4's allowed files).
   The cron is gated OFF so nothing breaks, but **before M5 enable** the worker must add
   `POST /ingestion/sources` that (a) bearer-auth-checks `PIPELINE_TRIGGER_SECRET`,
   (b) loads the user's `user_content_sources ⋈ content_sources` into `FollowedSource`s,
   (c) runs `run_source_ingestion(user_id, ..., mark_source_polled=<stamps last_fetched_at>)`,
   (d) persists the promoted pool + `content_source_items`, and (e) echoes
   `{items_fetched, items_promoted, items_dropped}` so the cron's count-logging is real.
2. **SP2's X screenshot is a local path (carried forward from SP3 concern #2).** If the
   worker ever splits ingestion and produce into separate containers, the tweet
   screenshot must be uploaded to shared storage before persist; same-container is fine.
3. **Did not commit** (per the brief — the phase orchestrator commits at phase end).
4. New runtime deps (`yt-dlp`, Playwright/chromium) from SP1/SP2 are operational weight on
   the Railway worker image — already flagged in the plan's deploy notes.
