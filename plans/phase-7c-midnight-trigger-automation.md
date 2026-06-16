# Phase 7c: Midnight-ET daily trigger + 24h window + safety cron

**Milestone:** M7 — Production feed automation & first-run onboarding feed
**Status:** Not started
**Estimated effort:** M

## Goal
A **deployed** Trigger.dev v4 schedule runs the daily pipeline at **midnight America/New_York** by calling the Railway worker over HTTP, the ingest catalog window is **24 hours**, and a **05:00 ET** readiness cron re-runs the pipeline if that day's `daily_feeds` are missing.

## Context
- `trigger/dailyPipeline.ts` today has cron `0 6 * * *` (06:00 UTC) and a dead `loadGatedStoryIds() → []` stub; the TS→Python seam is unwired and the schedule was never deployed. `@trigger.dev/sdk@^4.4.6`.
- Decision: TS→Python seam = **HTTP to the Railway worker** (`POST /pipeline/daily`, built in Phase 7).
- Decision: "midnight" = **US Eastern**; v4 cron supports `{ pattern, timezone }` so DST is handled by name (`America/New_York`).
- Today's lookback default is 2 days (`_DEFAULT_LOOKBACK_DAYS = 2` in `agents/ingestion/interest_keyed_pipeline.py`; `LOOKBACK_DAYS` default `"2"` in `scripts/run_live_batch.py`).
- At 00:00 ET the pipeline produces for **that day** (`date.today()`), so the trigger passes an explicit ET `target_date` to the worker rather than relying on the worker's clock.

## Sub-phases

### Sub-phase 1: Catalog window 48h → 24h
- **Files touched:** `agents/ingestion/interest_keyed_pipeline.py`, `scripts/run_live_batch.py`, the matching ingestion test
- **What ships:** default lookback becomes **1 day** (`_DEFAULT_LOOKBACK_DAYS = 1`; `LOOKBACK_DAYS` env default `"1"`). The `LOOKBACK_DAYS` override path is preserved.
- **Definition of done:** a pytest asserts the default ingest window is 24h (constant `== 1` and the computed `since` is `now - 1 day` when no override); the override path still honours `LOOKBACK_DAYS=2`.
- **Dependencies:** none

### Sub-phase 2: Wire dailyPipeline.ts → HTTP, midnight-ET cron
- **Files touched:** `trigger/dailyPipeline.ts`
- **What ships:** delete the `loadGatedStoryIds` stub and the dead fan-out; the scheduled task computes `target_date` in `America/New_York`, then does an authenticated `POST ${WORKER_BASE_URL}/pipeline/daily` (bearer `PIPELINE_TRIGGER_SECRET`) with `{ target_date }`; cron set to `{ pattern: "0 0 * * *", timezone: "America/New_York" }`. Non-2xx response throws so Trigger.dev surfaces the failure.
- **Definition of done:** `tsc`/build passes; a unit test (fetch mocked) asserts (a) the cron is declared with `timezone: "America/New_York"`, (b) `target_date` is the ET calendar date for a given trigger timestamp, (c) the POST carries the bearer header and a non-2xx makes the task throw.
- **Dependencies:** Phase 7 Sub-phase 2 _(cross-phase: endpoint must exist)_

### Sub-phase 3: Safety-net readiness cron (05:00 ET)
- **Files touched:** new `trigger/feedReadinessCheck.ts`, `trigger.config.ts` (only if `dirs`/registration needs it)
- **What ships:** a `schedules.task` at `{ pattern: "0 5 * * *", timezone: "America/New_York" }` that counts today's `daily_feeds` rows for active users via `@supabase/supabase-js` (service-role key from env). If zero/short → it re-invokes `POST /pipeline/daily` for today; it also logs how many current stories are missing a poster (`story_ambient_poster_url IS NULL`) as a readiness signal (the full instant-regeneration gate is Phase 7d).
- **Definition of done:** unit tests (supabase client + fetch mocked) — "no feeds for today" → triggers the re-run POST once; "feeds present" → no-ops (no POST); the missing-poster count is logged in both branches.
- **Dependencies:** Sub-phase 2 _(reuses the authenticated-POST helper/pattern)_

### Sub-phase 4: Deploy + enable schedules + validate
- **Files touched:** `trigger.config.ts` (comment/deferral note removal), `package.json` (a `trigger:deploy` script if absent), a short deploy note in this file's changelog
- **What ships:** `trigger.dev deploy` registers and **enables** both schedules against the provisioned project; documents the required env (`WORKER_BASE_URL`, `PIPELINE_TRIGGER_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`); a manual test-invoke of `dailyPipeline` hits the live worker and returns `202`.
- **Definition of done:** `npx trigger.dev@latest deploy` succeeds; both schedules appear in the Trigger.dev dashboard with the ET timezone; a manual invoke of `dailyPipeline` returns the worker's `202` envelope (verified in the run log). ⚠ irreversible — see flags.
- **Dependencies:** Sub-phase 2, Sub-phase 3

## Phase-level definition of done
The Trigger.dev dashboard shows two enabled ET schedules (`dailyPipeline` at 00:00, `feedReadinessCheck` at 05:00); a manual invoke of `dailyPipeline` POSTs the ET `target_date` to the live worker and returns `202`; the ingest default window is 24h; and the readiness cron re-runs the pipeline when a day's feeds are missing.

## Out of scope
- The Phase 7 endpoints themselves (consumed here, built there).
- Gemini Batch image submission + the 5am instant-regeneration gate (Phase 7d) — this phase's readiness cron only *detects + logs* missing posters and re-runs on missing feeds.
- Onboarding first-run feed (Phase 7b).

## Open questions
- Confirm `SUPABASE_SERVICE_ROLE_KEY` and `WORKER_BASE_URL` are available in the **Trigger.dev** environment (separate from the worker/Vercel envs). If not, they must be added before Sub-phase 4's deploy.
- Should the readiness cron page/alert on a still-missing feed after its own re-run, or only log? _(Recommendation: log + a single structured `error` event for now; wire alerting with Phase 7d.)_

## Self-critique

**Product lens:** PASS. This is the operational backbone for the brief's daily-habit metric (3+ sessions/week needs fresh stories every morning, automatically). No new user-facing feature, no scope creep — it activates infrastructure that already exists but was deliberately deferred.

**Engineering lens:** PASS with notes. Stack-aligned (Trigger.dev v4 `schedules.task`, `@supabase/supabase-js`, both in the master plan). The 24h-window change (SP1) is independent and lands first so the trigger work doesn't depend on it. DoDs are checkable (timezone in the cron object, ET date computation, mocked POST/branch assertions, dashboard schedule presence). SP4 (deploy) deliberately locks in nothing about the task shape — that's set in SP2/SP3. The readiness cron reads Supabase directly rather than forcing a new worker endpoint, keeping Phase 7 unchanged.

**Risk lens:** PASS with flags. File boundaries disjoint (Python ingest vs `dailyPipeline.ts` vs new `feedReadinessCheck.ts` vs config/deploy). Tests gate behavior, not compile (Rule 9). ⚠ **Reversibility:** Sub-phase 4 deploys to the production Trigger.dev project and enables a recurring paid job that calls a paid pipeline — proceed only after Phase 7 is deployed and the worker endpoint is verified live; enable on a date the owner is watching. The 24h-window change (SP1) removes the old 48h self-healing margin — explicitly mitigated by the 05:00 readiness cron (SP3). Painting-into-corner: 1→2→3→4 is coherent; SP4 only runs once 2+3 exist.

**Irreversible sub-phases:** Sub-phase 4 (`⚠ irreversible` — enables a live recurring production schedule; reversible only by re-deploying with the schedule disabled, and any runs it triggers will have spent pipeline cost).
