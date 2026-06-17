# Phase 7c — Sub-phase 3 execution report: Safety-net readiness cron (05:00 ET)

**Status:** SUCCESS
**Worktree:** `/Users/asheshsrivastava/News20/News20-7c` (uncommitted — orchestrator merges/commits at phase end)

## What shipped
A Trigger.dev v4 `schedules.task` (`feed-readiness-check`) at
`{ pattern: "0 5 * * *", timezone: "America/New_York" }`. At 05:00 ET it reads
Supabase directly with the service-role key and:

1. Counts distinct **active users** (≥1 `user_interest_profile` row — same definition
   as `agents/pipeline/daily_batch.py::_load_active_user_ids`).
2. Counts **today's `daily_feeds`** rows for the ET `feed_date` (head-only exact count).
3. Counts **current stories missing a poster** — `stories` ⋈ `digests!inner`
   (`digest_is_current = true`) where `story_ambient_poster_url IS NULL`.
4. If `activeUserCount > 0 && todaysFeedCount === 0` → re-invokes
   `runDailyPipelineSchedule()` (SP2's authenticated `POST /pipeline/daily`,
   bearer `PIPELINE_TRIGGER_SECRET`, ET `target_date`) exactly once.
5. Logs the missing-poster count in **both** branches via a structured JSON event
   (`feed_readiness_evaluated`). On a re-run it also emits
   `feed_readiness_rerun_triggered` (the plan's "log, don't page" choice) and
   `feed_readiness_rerun_accepted`.

## Reuse of SP2 (no duplication)
- Imported `easternCalendarDate` (ET-date helper) and `runDailyPipelineSchedule`
  (authenticated-POST) directly from `trigger/dailyPipeline.ts`. **No edit to
  dailyPipeline.ts was needed** — both were already exported. The re-run path is
  the *same* POST contract as the midnight schedule.
- New task auto-registers via the existing `dirs: ["./trigger"]` in
  `trigger.config.ts` — **no config change needed**.

## Files touched
- `trigger/feedReadinessCheck.ts` (new)
- `tests/lib/trigger/feedReadinessCheck.test.ts` (new — matches the vitest
  `tests/lib/**` include glob; SP2 used the same dir)
- `trigger.config.ts` — **NOT** touched (auto-registration suffices)
- `trigger/dailyPipeline.ts` — **NOT** touched

## Decisions
- **"zero/short"** = re-run on **zero** `daily_feeds` rows for today (the documented
  default). No "short"/threshold heuristic added — that would need a per-user
  expected-count and is not in the DoD. Additionally gated on `activeUserCount > 0`
  so an empty-user environment never triggers a pointless paid re-run.
- **Secrets:** `SUPABASE_SERVICE_ROLE_KEY` (+ `SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_URL`
  fallback), `WORKER_BASE_URL`, `PIPELINE_TRIGGER_SECRET` all from env. Only counts/
  flags/dates are logged — **no keys or PII**.
- **Missing-poster column:** confirmed `story_ambient_poster_url` exists on `stories`
  (`supabase/migrations/0001_content_schema.sql:54`). "Current" defined via current
  digest, matching the reel feed's own definition.

## Validation — PASS
- `npx vitest run tests/lib/trigger/feedReadinessCheck.test.ts` → **4 passed**.
- Full trigger dir (`tests/lib/trigger/`) → **11 passed** (SP2's 7 + SP3's 4; no
  cross-contamination).
- `npx tsc --noEmit` → **no errors in any `trigger/` file** (pre-existing unrelated
  `remotion/*` module-resolution errors remain — outside this phase).
- `npx @biomejs/biome check trigger/feedReadinessCheck.ts <test>` → **clean** (had to
  rebuild the test's Supabase mock on a real resolved Promise to satisfy the
  `noThenProperty` rule rather than a hand-written `then`).

## Definition of done — PASS
- **"no feeds for today" → exactly one POST:** asserted (`fetchMock` called once,
  correct URL/bearer/`target_date`, `reranPipeline: true`).
- **"feeds present" → zero POSTs:** asserted (`fetchMock` not called,
  `reranPipeline: false`).
- **Missing-poster count logged in BOTH branches:** asserted via the parsed
  `feed_readiness_evaluated` event in both the missing-feeds and feeds-present tests.
- Bonus branch: **no active users → no re-run** (avoids paid re-run in an empty env).

## Concerns / notes for the orchestrator
- **`trigger/produceStory.ts`:** confirmed **zero importers** anywhere in the repo
  (`grep` over `*.ts`/`*.json`, excluding the file itself, returns nothing). It is a
  dead SP2-era fan-out artifact. I **left it in place** (deletion is optional, not in
  the DoD, and I kept the diff surgical). Safe for the orchestrator to delete.
- **dailyPipeline.ts export-touch:** **none required** — the two helpers I reuse were
  already exported by SP2.
- **`npm install` was run** in this worktree (node_modules was absent) so tests/tsc/
  biome could run. That only populates `node_modules` (gitignored); no manifest change.
- **Open question deferred (per plan):** the cron logs + emits structured events on a
  still-missing feed; it does **not** page/alert. Paging is Phase 7d.
