# Phase 5a — Sub-phase 4 Execution Report

**Live e2e + offline sim + spec updates + supersede 5e (+ the two SP3 hand-offs)**

**Status:** SUCCESS · **Validation:** PASS · **Definition of done:** PASS (5/5)
**Date:** 2026-06-05

---

## 1. What was implemented (deliverables a–d + 2 hand-offs)

**(a) Live e2e** — `tests/agents/pipeline/test_phase5a_live_e2e.py` (new, marked/skipped without creds):
creates a disposable `auth.users` (admin API; the `users` mirror row is auto-created
by the auth trigger), seeds the DoD `user_feed_allocation`
(`{breaking 2, world_politics 4, tech_science 5, markets 4, sport 3, culture 3, youtube 6, x 3}`)
+ a **custom-source Nvidia** `user_entity_follows` (the live `entities` row
`ai/ai-hardware-compute/companies-topics/nvidia`), hydrates both through the **real
loaders** (`_load_followed_entities ⋈ entities`, `_load_category_allocation`) against
the LIVE DB, runs `assemble_user_feed` over a deterministic twin-Nvidia story pool, and
asserts: feed totals 30 (source budgets rolled into topics), breaking == 2, markets ==
its exact 4-slot budget, the topic sequence matches `allocation_sort_order`, and the
**Nvidia story outranks its non-followed twin** within markets (score + position). Then
it deletes the disposable user — the FK cascade removes the seeded allocation + follow
rows. Verified twice: 0 orphan users, 0 leftover allocation/follow rows, `entities` still
248.

**(b) Offline sim** — extended `agents/pipeline/sim/world.py` + `ranking_sim.py`:
- `SimProfile` gains `followed_entities` + `category_allocation` (default empty — legacy
  profiles unaffected).
- New `build_entity_boost_scenario(interest_nodes)` — an **isolated** deterministic world
  (twin `markets.stocks` stories identical in importance/freshness, one titled "Nvidia",
  one not) + a profile with a custom Nvidia follow + the DoD allocation. Kept separate
  from `build_world` so the legacy strict/broad/niche invariants are untouched.
- `simulate_profile` forwards `profile.followed_entities` + `profile.category_allocation`
  into `assemble_user_feed`. `_component_lookup` absorbs the entity-aware buckets so the
  CLI report's Score breakdown matches the entity-aware Score (report/asserts in
  lock-step, Rule 9). `main()` renders the entity scenario after the 3 profiles.
- Two new CI-safe tests in `test_ranking_simulation.py`:
  `test_entity_follow_lifts_story_above_twin_within_category` (entity ordering) and
  `test_entity_scenario_honors_category_budgets_and_sequence` (budgets + sequence +
  source roll + total 30, no dupes). Both deterministic, no network.

**(c) `reference/ranking-spec.md`** — new **§3a** documents both mechanisms as source of
truth: the EntityBonus formula (`EntityBonus = normalized_follow_weight ×
ENTITY_BONUS_WEIGHT`, whole-word title match + company-only ticker gate, custom>more>seed
loader weighting, max-normalization, strongest-match-not-sum, identity dedup,
`ENTITY_BONUS_WEIGHT = 0.3`) AND the category-budget + manual-sequence allocator (8
categories, breaking-as-budgeted-tier, source soft-roll, balanced default, §3.8
don't-repeat preserved). A `⚠ Superseded` banner was added to the top of the old §3, and
§3a explicitly states it **supersedes §3's affinity-proportional split** + **retires
§3.7 exploration** (Rule 7 conflict surfaced, not silently dropped).

**(d) Supersede banners** — a `⚠ SUPERSEDED by phase-5a (2026-06-05) — see
plans/phase-5a-build-your-30-and-entity-ranker.md` banner at the TOP of both
`reference/control-surface-spec.md` and `plans/phase-5e-control-surface.md`.

**Hand-off #1 (orchestrator forward):** `agents/pipeline/orchestrator.py` — the
`assemble_daily_feeds` call site now forwards `followed_entities=user_inputs.followed_entities`
+ `category_allocation=user_inputs.category_allocation` into `assemble_user_feed` (the
two-line forward; `ActiveUserFeedInputs` already carried both fields from SP2). Minimal,
surgical, no other call-site change.

**Hand-off #2 (retired-exploration test):** `test_ranking_simulation.py::test_niche_profile_surfaces_depth_and_explores`
→ renamed `test_niche_profile_surfaces_depth_without_exploration_tier`; the obsolete
`assert any(feed_slot_kind == "exploration")` is replaced with the now-correct invariant
(`all(feed_slot_kind != "exploration")` — the tier is retired) plus the deep-niche-surfaces
assertion. The `_profile_checks` C-branch was updated to match (and a new D-branch added
for the entity scenario), so the CLI report stays in lock-step.

## 2. Files touched

- **New:** `tests/agents/pipeline/test_phase5a_live_e2e.py`
- **Modified:** `agents/pipeline/sim/world.py`, `agents/pipeline/sim/ranking_sim.py`,
  `agents/pipeline/orchestrator.py` (call-site forward only),
  `tests/agents/pipeline/test_ranking_simulation.py`, `reference/ranking-spec.md`,
  `reference/control-surface-spec.md`, `plans/phase-5e-control-surface.md`

## 3. Divergences from the plan (surfaced — Rule 7/12)

1. **The live e2e does NOT write `daily_feeds`.** It asserts on the `assemble_user_feed`
   output, not a persisted `daily_feeds` row. WHY: `daily_feeds.feed_story_id` → `stories`
   and `feed_matched_interest_id` → `interests` are FK-constrained, so writing a feed would
   require seeding real `stories` + `interests` rows and risk polluting the shared live
   story pool. The `write_daily_feed` persistence path is already covered by the
   produce-once idempotency tests in `test_feed_assembly.py` (mocked client). The
   **load-bearing phase-5a behavior** — live-hydrated allocation + entity-aware ranking —
   IS exercised against the real DB (the loaders + allocator). Documented in the test
   module docstring.

2. **Deterministic in-memory story pool for the live e2e.** The live `stories` table has
   only 5 rows with unknown tagging — relying on it would be flaky and could not isolate
   the entity bonus. The follows + allocation (the contracts phase-5a actually added) are
   hydrated LIVE; the story pool is the deterministic twin-Nvidia scenario so the ordering
   invariant is provable. This is the faithful seam: the live read is the new contract, the
   pool is the controlled fixture.

3. **Legacy `exploration_interest_ids` / `build_exploration_candidates` left in place but
   INERT.** The category-budget allocator ignores the exploration param. Removing the
   machinery is a larger refactor outside SP4's surgical scope (Rule 3); the stale
   docstrings that claimed "exploration fills ~10%" were corrected to say the tier is
   retired. The inert seed is harmless (its output flows to the ignored param).

4. **`ENTITY_BONUS_WEIGHT = 0.3` confirmed, NOT tuned.** At 0.3 the sim + live e2e show a
   clean reorder: the twin's base Score `0.808` → Nvidia `1.108` (a +0.3 lift that places
   it above its twin) WITHOUT drowning the Affinity×Depth base (the non-Nvidia equities
   fillers still sit between them by base score). 0.3 is meaningful and not overpowering —
   left at the accepted default (open-Q2 closed).

## 4. Code-review findings + fixes (Step B/C)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `world.py` + `ranking_sim.py` module/`build_profiles` docstrings still claimed "niche+exploration" / "exploration fills ~10%" — false after the tier retired. | Corrected all three docstrings to state the tier is retired + point at `build_entity_boost_scenario`. |
| 2 | Low | `_profile_checks` C-branch still asserted `feed_slot_kind == "exploration"` → would print a FAIL line in the CLI report. | Replaced with the "no auto-exploration slot (tier retired)" invariant; added a D-branch for the entity scenario. |
| 3 | Low (verified, no fix) | Live e2e could leave orphan users/rows on failure. | The seed + delete are wrapped in `try/finally`; cleanup runs even on assertion failure, and the `finally` block ASSERTS 0 leftover rows. Verified twice: 0 orphans, entities still 248. |
| 4 | Low (verified, no fix) | CI must stay network-free. | The live test loads `.env` only if the file exists (gitignored → absent in CI) and `skipif`s on `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`. Gate proven to evaluate `has_live_creds=False` → SKIP when env is unset. |

No secrets printed/logged (the password is never read by this sub-phase; the service-role
key is read via `os.environ` and never echoed). The sim invariant uses the fixed
`SIM_NOW` clock — no `datetime.now`/random nondeterminism.

## 5. Validation — verbatim

**ruff check** (all touched files):
```
All checks passed!
```
**ruff format --check** (all touched files):
```
5 files already formatted
```
**Offline sim tests** (`tests/agents/pipeline/test_ranking_simulation.py`):
```
9 passed in 0.05s
```
**Full pipeline suite** (network-free default; live test runs locally because `.env`
present, SKIPS in CI):
```
166 passed, 3 warnings in 2.89s
```
**Full agents suite (regression)**:
```
277 passed, 4 warnings in 2.52s
```
**Live e2e** (`tests/agents/pipeline/test_phase5a_live_e2e.py`) — captured output (secrets
redacted; the password is never touched):
```
{"followed_interest_count": 5, "followed_entity_count": 1, "classified_story_count": 44,
 "entity_boosted_count": 1, "event": "score_and_classify_for_user_completed", ...}
{"followed_interest_count": 5, "followed_entity_count": 1, "allocation_row_count": 8,
 "breaking_slots": 2, "source_roll_slots": 9, "total_slots": 30, "excluded_prior_count": 0,
 "event": "assemble_user_feed_completed", ...}
1 passed, 3 warnings in 1.38s
```
Live e2e proof values: live-hydrated follow = `Nvidia / company / follow_weight 3.0`
(custom-source weighting applied by the loader); 8 allocation rows; **30** total slots,
**2** breaking, **9** source-roll slots, **4** markets slots; Nvidia `pos 24 / score 1.108`
vs twin `pos 26 / score 0.808`. Cleanup: alloc 0 / follows 0 / users 0; entities 248.

**Network-free CI proof:** with `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` unset and no
`.env` loaded, the skip gate evaluates `has_live_creds=False` → the test SKIPS. `.env` is
gitignored (`git check-ignore .env` → ignored), so CI never has it.

## 6. Definition of done — PASS (5/5)

| DoD item | Result |
|---|---|
| Live e2e produced a feed for the seeded user whose per-category counts + order match the seeded allocation AND the Nvidia story outranks its non-followed twin (captured output) | PASS — 30 slots, breaking 2, markets 4, sequence honored; Nvidia pos 24 (1.108) > twin pos 26 (0.808) |
| Live e2e cleans up (no orphan users/rows; entities intact) | PASS — 0 orphans, entities 248 (verified after 2 runs) |
| Offline sim asserts the SAME invariants with NO network | PASS — 2 new deterministic tests (entity ordering + budgets/sequence/total-30) |
| `ranking-spec.md` has the entity-bonus formula + category-budget algorithm | PASS — new §3a + supersede banner on §3 |
| Both superseded docs carry a banner linking here | PASS — `control-surface-spec.md` + `phase-5e-control-surface.md` |
| Whole `tests/agents/pipeline` suite green (no leftover failures) | PASS — 166 passed (the SP3 retired-exploration failure fixed) |

No checks skipped or masked (Rules 9 & 12).

## 7. Concerns for the orchestrator's phase-level checks (slop scan / CSO)

1. **Live e2e is gated, not run in CI.** The CI-safe proof of the same invariants is the
   2 new deterministic sim tests. The live e2e is the local-only confirmation that the
   live loaders + schema match the contract. Slop scan should NOT flag the skip — it is the
   required network-free guarantee.

2. **Inert exploration machinery remains** (`exploration_interest_ids`,
   `build_exploration_candidates`, the `exploration_candidates_by_interest` param on
   `assemble_user_feed`). It is dead-but-harmless under the category-budget model. A future
   cleanup phase could remove it once no caller references it; out of SP4's surgical scope.

3. **The diff stat shows large changes in `daily_batch.py` / `feed_assembly.py` /
   `stages/ranking.py` / `test_feed_assembly.py` / `test_ranking.py`.** Those are SP1–SP3's
   uncommitted work in this shared worktree (the orchestrator commits once at phase end) —
   SP4 did NOT touch them. SP4's footprint is the 8 files in §2.

4. **`plans/master-plan.md` shows as modified** in the worktree (pre-existing
   uncommitted M5 planning edit, not from SP4).

5. **No daily_feeds write in the live e2e** (divergence #1). If the orchestrator wants a
   persisted-feed live proof, it would need a `stories`/`interests` seed harness — a larger
   task than SP4's scope and a live-pool pollution risk; recommend keeping the mocked
   `write_daily_feed` idempotency tests as the persistence proof.

---

**Return to orchestrator:** STATUS SUCCESS · files: `tests/agents/pipeline/test_phase5a_live_e2e.py`
(new), `agents/pipeline/sim/world.py`, `agents/pipeline/sim/ranking_sim.py`,
`agents/pipeline/orchestrator.py`, `tests/agents/pipeline/test_ranking_simulation.py`,
`reference/ranking-spec.md`, `reference/control-surface-spec.md`,
`plans/phase-5e-control-surface.md` · Validation PASS (ruff clean; pipeline 166 passed;
agents 277 passed; live e2e 1 passed + cleaned up) · DoD PASS (5/5) · Both hand-offs
applied (orchestrator forward + retired-exploration test fixed).
