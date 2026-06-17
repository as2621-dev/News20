# Phase 7c — Sub-phase 2 execution report

**Sub-phase:** Wire `dailyPipeline.ts` → authenticated HTTP, midnight-ET cron
**Status:** SUCCESS
**Worktree:** `/Users/asheshsrivastava/News20/News20-7c` (NOT committed — orchestrator merges at phase end)

## What shipped

Replaced the dead Phase 1d scheduling shell in `trigger/dailyPipeline.ts` with a real
Trigger.dev v4 `schedules.task` that drives the Phase 7 worker over authenticated HTTP.

- Deleted the `loadGatedStoryIds()` stub, the `batchTrigger` fan-out, and the
  `produceStory` import (the dead seam).
- Cron is now `{ pattern: "0 0 * * *", timezone: "America/New_York" }` — midnight US
  Eastern, DST handled by zone name (was `"0 6 * * *"` UTC).
- New pure helper `easternCalendarDate(instant: Date): string` computes the ET calendar
  date (`YYYY-MM-DD`) from the run's `payload.timestamp` via `Intl.DateTimeFormat`
  (`en-CA` + `timeZone: America/New_York`). Deterministic — no `Date.now()` — so it is
  unit-testable with a pinned instant and DST-correct.
- New exported `runDailyPipelineSchedule(instant)` reads `WORKER_BASE_URL` +
  `PIPELINE_TRIGGER_SECRET` from env (throws loudly if unset), then
  `POST ${WORKER_BASE_URL}/pipeline/daily` with `Authorization: Bearer <secret>` and
  body `{ target_date }`. A non-2xx (`!response.ok`) throws so Trigger.dev surfaces the
  failure (Rule 12). The task's `run` delegates to it.
- `DAILY_FEED_CRON` is exported so the cron contract is unit-testable (the v4 `Task`
  object does not re-expose its cron config).

## Endpoint correctness (verified against `agents/worker/pipeline_routes.py`)

- Route: `POST /pipeline/daily` ✓
- Auth: `Authorization: Bearer <PIPELINE_TRIGGER_SECRET>` (server compares via
  `hmac.compare_digest`) ✓
- Body: `DailyRunRequest{ target_date: date }` — Pydantic `date` parses the
  `YYYY-MM-DD` string ✓; `max_total_productions` / `lookback_days` are optional and
  omitted (worker defaults apply) ✓
- Success: `202 Accepted` → passes `response.ok` (2xx) ✓

## Files touched

- `trigger/dailyPipeline.ts` (rewritten)
- `tests/lib/trigger/dailyPipeline.test.ts` (new — placed under `tests/lib/**` so the
  existing Vitest `include` globs pick it up without a config change)

## Validation

- **Vitest:** `npx vitest run tests/lib/trigger/dailyPipeline.test.ts` → **7 passed**.
- **tsc:** `npx tsc --noEmit` → **0 errors in touched files**; the only errors are
  pre-existing in the unrelated `remotion/` dir (missing `remotion` package) and exist
  on base.
- **Biome:** `npx biome check trigger/dailyPipeline.ts tests/lib/trigger/dailyPipeline.test.ts`
  → clean (format applied).

(node_modules was temporarily symlinked from the main worktree to run tooling, then
removed; it is gitignored regardless.)

## Definition of done — PASS

- (a) cron declared with `timezone: "America/New_York"` → asserted on `DAILY_FEED_CRON`. ✓
- (b) `target_date` is the ET calendar date for a given trigger timestamp → tested with
  `2026-03-09T03:30:00Z` (22:30 prev ET day) → `"2026-03-08"`; plus EST 00:00 and EDT
  00:00 cases. ✓
- (c) POST carries the bearer header and a non-2xx makes the task throw → asserted
  header + body + URL on success; `401` → rejects; missing secret → rejects without
  calling fetch. ✓

## Concerns / notes (medium/low)

- **`trigger/produceStory.ts` is now orphaned** — no longer imported by
  `dailyPipeline.ts`, only self-referenced. It is out of SP2's file scope so left in
  place; it is still a registered (no-op) task in the `./trigger` dir. SP3/SP4 should
  decide whether to delete it. (low)
- **`tests/trigger/` does not exist; placed the test under `tests/lib/trigger/`** to
  match the existing Vitest `include` (`tests/lib/**`, `tests/seed/**`) without touching
  `vitest.config.ts`. If a dedicated trigger test tree is wanted later, move it and add
  the glob. (low)
- SP4 (deploy/enable) and SP3 (readiness cron) consume this task's shape; the
  authenticated-POST pattern here is the one SP3 should reuse.
