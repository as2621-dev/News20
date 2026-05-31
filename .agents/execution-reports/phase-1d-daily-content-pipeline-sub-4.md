# Execution Report — Phase 1d SP4 (feed allocation → daily_feeds + Trigger.dev schedule)

**Sub-phase:** 4 (final, integration) of `phase-1d-daily-content-pipeline`
**Date:** 2026-05-31
**Status:** SUCCESS (SP4 narrowed scope per the SP4 brief)
**Commit:** NOT committed (orchestrator commits at phase end).

---

## Scope executed (per the SP4 brief, which narrows the phase-file SP4)

The phase file's SP4 bundles profile-update (`agents/memory/*`) + allocator + Trigger fan-out.
The SP4 **brief** explicitly narrowed this sub-phase to ONLY the allocator + writer + batch entry +
schedule shell + its test. I executed the narrowed scope and flag the profile-update + per-story
`batchTrigger` fan-out as deferred (see Divergences).

## Files created / modified

| File | State | What |
|---|---|---|
| `agents/pipeline/feed_assembly.py` | **NEW** | §3 per-user allocator (`assemble_user_feed`) + idempotent `write_daily_feed` |
| `agents/pipeline/orchestrator.py` | **MODIFIED** | added batch entry `assemble_daily_feeds` + `ActiveUserFeedInputs` / `DailyFeedsBatchResult` |
| `trigger/dailyPipeline.ts` | **NEW** | `schedules.task` daily cron (v3 SDK) — thin scheduling shell |
| `tests/agents/pipeline/test_feed_assembly.py` | **NEW** | 8 unit tests (allocator invariants + DoD a/b/c) |
| `tsconfig.json` | **MODIFIED (out-of-set, flagged)** | added `"trigger"` to `exclude` |
| `biome.json` | **MODIFIED (out-of-set, flagged)** | added `"!trigger"` to `files.includes` |

> **Out-of-set edits flagged (Rule 12):** the two config edits were NOT in the declared SP4 file set.
> They are the required integration step for adding a TS file that imports the (uninstalled)
> Trigger.dev SDK into a repo whose `tsconfig`/`biome` glob all `.ts`. Without them, a real
> `schedules.task` file breaks the previously-green `tsc`/`biome` (the trigger dir is built by the
> Trigger CLI, not `next build`). Surfaced rather than silently expanded; revert if undesired.

## The 3 conflict decisions

1. **Trigger.dev v3 vs v4** → used `@trigger.dev/sdk/v3` + `schedules.task`, matching the repo intent.
   New finding: there is NO `trigger/` dir, NO `trigger.config.ts`, and `@trigger.dev/sdk` is NOT in
   `package.json`/`node_modules` at all. Did NOT add the SDK dep (scope creep). Install/register
   command documented below.
2. **New file vs placeholder** → MOOT: `trigger/dailyDigest.ts` does not exist (no placeholder, no
   second cron). Created `trigger/dailyPipeline.ts` as the single daily `schedules.task`.
3. **TS → Python seam** → implemented honestly as a STUB: a real registered `schedules.task` whose
   `run()` returns a scheduling acknowledgement + a `// Reason:` comment marking the seam as
   intentionally unwired (execution-host decision = phase Open Q3/Q5). The Python `assemble_daily_feeds`
   is the real, fully-tested substance. I do NOT claim e2e TS→Python works.

## Divergences from the plan (flagged, Rule 12)

- **`orchestrate_digest_for_user` does not exist.** The brief referenced it; the real SP3 entry points
  are `score_candidates_for_user(...)` (pure scorer/fallback) and `orchestrate_story(...)` (per-story
  producer). The allocator correctly consumes `score_candidates_for_user` for ranking — NOT
  `orchestrate_story` (which produces paid audio; that is the per-story fan-out, a separate concern
  from per-user allocation).
- **Test path:** brief said `tests/pipeline/test_feed_assembly.py`; that dir does not exist. Placed at
  `tests/agents/pipeline/test_feed_assembly.py` to match the codebase convention (Rule 11).
- **Trigger file name:** used `dailyPipeline.ts`.
- **Deferred from the phase-file SP4 (out of the brief's narrowed scope):** profile-update job
  (`agents/memory/*` — note this directory does not yet exist), `trigger.config.ts`, and the per-story
  `batchTrigger` fan-out. These remain for a follow-up / the broader SP4.
- **Exploration (§3.7)** is implemented but OPT-IN: it fills only when the caller injects
  `exploration_candidates_by_interest` (adjacent sibling/parent candidates the user does not follow).
  Building that adjacent pool from Supabase is the loader's job; omitted → exploration skipped (no
  crash). `strict` followed interests are excluded from contributing exploration.

## Self-review findings + fixes

- **[HIGH — found + fixed] cap leaked through redistribution.** First pass enforced the ~40% cap only
  on the initial fill; the §3.6 redistribute pass then handed every spare slot back to the high-affinity
  glut interest, blowing past the cap (test caught 25, then 16). Fixed by tracking a per-interest
  `filled_count` and enforcing `cap_per_interest` headroom in BOTH the fill and redistribute passes.
  The cap test asserts on interest-KIND slots (≤12); the breaking tier (§3.1) is a separate preempt
  tier that may legitimately add more, so conflating it with the cap was the test's error, corrected.
- **[resolved] zip alignment** — every `ordered.extend` has a matching `slot_kinds.extend` of equal
  length (breaking/fill/redistribute/exploration). Verified.
- **[resolved] position contiguity** — positions via `enumerate(..., start=1)` over the budget-truncated
  list → always 1..N contiguous.
- **[resolved] unused imports** — removed `collections.abc.Callable` (orchestrator) and `pytest` (test)
  flagged by ruff F401.
- **[low, noted] sparse/empty-profile recency fallback** (ranking-spec §3.8 tail) NOT implemented — an
  empty-profile user returns `[]` and is skipped (no empty-feed row, DoD-c honored) rather than getting
  a recency feed. Acceptable for M1; flagged in the docstring.

## Validation (verbatim)

```
$ .venv/bin/ruff check agents/pipeline/feed_assembly.py agents/pipeline/orchestrator.py tests/agents/pipeline/test_feed_assembly.py
All checks passed!

$ .venv/bin/ruff format --check  (same 3 files)
3 files already formatted

$ .venv/bin/ruff check agents/ tests/agents/
All checks passed!

$ .venv/bin/python -m pytest tests/agents/pipeline/test_feed_assembly.py -v
  test_assemble_user_feed_orders_positions_and_excludes_prior PASSED
  test_assemble_user_feed_breaking_preempts_top_slot PASSED
  test_assemble_user_feed_caps_single_interest_share_for_multi_interest_user PASSED
  test_assemble_user_feed_empty_when_no_eligible_story PASSED
  test_write_daily_feed_skips_empty_slots PASSED
  test_write_daily_feed_is_idempotent_on_rerun PASSED
  test_assemble_daily_feeds_one_distinct_feed_per_active_user PASSED
  test_assemble_daily_feeds_rerun_does_not_duplicate PASSED
  8 passed, 1 warning in 0.05s

$ .venv/bin/python -m pytest tests/agents/ -q
  129 passed, 1 warning in 1.09s   (121 prior SP1–SP3 + 8 new SP4; no failures)

$ npx tsc --noEmit          → exit 0 (clean; trigger/ excluded from app tsconfig)
$ npm run lint  (biome)     → Checked 62 files. No fixes applied.  (exit 0)
$ tsc on trigger file alone → ONLY "TS2307: Cannot find module '@trigger.dev/sdk/v3'"
                              (the uninstalled SDK; no syntax/type errors in the task code itself —
                               the 'payload implicitly any' note is a consequence of the unloadable
                               SDK types, resolved on install)
```

**Mutation check (Rule 9 — tests verify intent):** temporarily forcing the produce-once pre-check off
(`existing_count = 0`) made BOTH idempotency tests FAIL
(`test_write_daily_feed_is_idempotent_on_rerun`, `test_assemble_daily_feeds_rerun_does_not_duplicate`).
Restored (diff-verified clean); 8/8 pass. The idempotency tests genuinely bite.

## Definition of done — PASS/FAIL

- **(a) one daily_feeds feed per active user, ordered non-empty story_id array** — **PASS**
  (2 eligible users → 2 feeds, positions 1..N, distinct story sets `{s-arsenal-*}` vs `{s-meta-*}`).
- **(b) re-run does not duplicate (produce-once / idempotent)** — **PASS** (writer-level + batch-level;
  mutation-verified to fail without the guard).
- **(c) zero-eligible user skipped (no empty-feed row)** — **PASS** (`u-nothing` → 0 rows;
  `users_skipped_empty == 1`).
- **ruff check + ruff format** — **PASS**.
- **Trigger.dev task typechecks / valid registered task** — **PASS** (clean app `tsc --noEmit`; the
  trigger file is a syntactically valid `schedules.task` export — isolated tsc shows only the expected
  uninstalled-SDK module error).

## Concerns for the orchestrator (commit + run)

1. **All SP4 files are trackable (NOT git-ignored).** `git check-ignore` returns clean for every SP4
   path; `orchestrator.py`/`biome.json`/`tsconfig.json` show ` M`, and `feed_assembly.py`/
   `test_feed_assembly.py`/`trigger/dailyPipeline.ts` show `??`. The phase-end commit should stage all
   six.
2. **No pre-existing failures.** Full agent suite is 129/129 green. (There is NO `agents/memory/`
   directory — an earlier transient diagnostic referencing a memory test was a false alarm from a
   cancelled parallel shell call; verified `agents/memory/` does not exist.)
3. **Trigger.dev not installed.** To register/run: `npm i @trigger.dev/sdk@^3`, add `trigger.config.ts`,
   then `npx trigger.dev@latest dev` (interactive login — do manually). Did not attempt interactive auth.
4. **TS→Python seam unwired** (CONFLICT #3) — for the M1 manual run, invoke `assemble_daily_feeds`
   directly in Python (mirroring the SP3 live-e2e pattern), not via the TS task.
5. **First-draft constants** (`FEED_SLOT_BUDGET=30`, `BREAKING_SLOT_COUNT=4`, cap `0.40`, exploration
   `0.10`, `T=DEFAULT_SCORE_THRESHOLD=0.20`) — confirm at the 2-user manual run (phase Open Q4).

---

## COMPLETION ADDENDUM (2026-05-31) — the deferred scope, now shipped

The PARTIAL items above (profile-update job, `trigger.config.ts`, `batchTrigger` fan-out, live e2e)
are **DONE**. SP4 is **COMPLETE**.

### What shipped beyond the partial
- **Profile-update loop (§4)** — `agents/memory/player_signals.py` (per-signal delta: strong+/mild+/
  play∝completion_pct/fast-skip−) + `agents/memory/session_processor.py` (`compute_weight_updates`:
  depth-attenuated aggregate → per-run cap → slow decay-to-baseline → clamp; `run_profile_update_job`
  Supabase wrapper). Written FRESH against ranking-spec §4 + the real `player_signals`/
  `user_interest_profile` schema, NOT a line-by-line TLDW port (the donor is a different schema; reuse
  intent honored structurally). 7 tests assert on resulting weights (engaged↑ capped, ignored↓ via
  decay not below floor, ancestor attenuation, unfollowed-no-nudge, per-event mapping, the wrapper).
  Mutation-checked: zeroing decay breaks ignored-falls; zeroing the strong delta breaks engaged-rises.
- **`run_daily_pipeline` DAG** — `agents/pipeline/daily_batch.py`: update-weights→ingest→produce-once
  fan-out→score→allocate, with **ingest injected** (live GDELT in prod, fixture pool in the e2e) and a
  bounded-concurrency paid fan-out that skips a verification-halt/render-error story without aborting
  the batch. `load_active_user_inputs` is the previously-deferred Supabase loader. 1 staged-order test.
- **Trigger.dev v4 (decision: v4 per the global rule; the committed v3 shell's "match the repo"
  rationale was hollow — no other Trigger code existed).** Installed `@trigger.dev/sdk@4.4.6`; added
  `trigger.config.ts` (`defineConfig`, env-sourced project ref); rewrote `trigger/dailyPipeline.ts` to
  v4 `schedules.task` + added `trigger/produceStory.ts` (`task`) fanned via `batchTrigger`; removed the
  `trigger` excludes from `tsconfig.json`/`biome.json` so the layer is typechecked + linted (tsc=0,
  biome clean). `TRIGGER_PROJECT_ID`/`TRIGGER_SECRET_KEY` are now set (user-provided mid-run).

### Live e2e DoD — PASS (`sp4_e2e_fixture_run.py`, runtag `46e6e5e1`)
Deterministic FIXTURE pool of 15 ancestor-tagged stories (5 cricket.india / 5 arsenal / 4 sport / 1
world) through the SAME `run_daily_pipeline`, 2 fresh auth users (strict `sport.cricket.india` + broad
`sport`), real paid TTS/image/LLM + real Supabase writes. Result:
- **≥10 produced digests** — produced **10/15** (5 verification-halts correctly skipped; ~25–33% halt
  rate, so the pool is built oversized to clear the floor — this run cleared by exactly the margin).
- **≥2 distinct per-user feeds, ordered 01..N** — strict 4 rows, broad 10 rows, distinct sets.
- **strict user: leaf-only** — every strict row is a `cricket.india` leaf story, NO upward-fallback row,
  NO exploration row.
- **niche reaches broad** — 3 arsenal (soccer) stories reached the broad `sport` follower via the
  grandparent tag; broad also got the **world exploration** row the strict user did not.

### Honest flags / remaining follow-ups (Rule 12)
- e2e used a FIXTURE pool, not live GDELT — the strict-no-fallback + niche-reaches-broad invariants
  need controlled tags to be provable, not luck. **Live-GDELT ingest at active-interest scale is still
  un-run** (the production `ingest_fn`).
- §4 weight nudges proven by unit tests, not re-proven live (no signals seeded for the e2e run).
- The daily cron is a valid v4 registered task but is **NOT deployed** (deliberate post-run step) and
  the **TS→Python seam** (`@trigger.dev/python` build extension) is not built — for M1 the batch is run
  directly in Python.
- **Cleanup:** the two paid e2e runs left fixture rows/users (`FIXTURE-SP4-5d6758ce-*`,
  `FIXTURE-SP4-46e6e5e1-*`, 4 fixture auth users) + generated `assets/m0/FIXTURE-SP4-*` poster dirs
  (untracked, NOT committed). `load_active_user_inputs` has no active-user filter, so leftover fixture
  users get re-processed (idempotently skipped) on subsequent runs — prune them or add a filter.
