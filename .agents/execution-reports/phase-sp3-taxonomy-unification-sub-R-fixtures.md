# Phase SP3 — Sub-phase R (fixture remediation) execution report

**Status:** SUCCESS (with one cross-phase blocker surfaced — see §4)
**Date:** 2026-06-19
**Scope:** migrate old-taxonomy fixtures/sim to the new 10-key taxonomy so the Python pipeline suite is green. NOT a feature change (Rule 3 surgical, Rule 9 intent-preserving).

## Old → new mapping applied
`world_politics → geopolitics`, `tech_science → tech`, `markets → business`, `culture → arts`, `sport`/`youtube`/`x` unchanged. Interest **slugs** (`markets.crypto`, `markets.stocks`, `world`, `world.health`, etc.) were left intact — they are retained legacy aliases that still resolve to the new roots via `SLUG_TO_CATEGORY` (the Step-B exception). Only **category keys** passed into `CategoryAllocation` / cap dicts / cell tuples / id-prefixes were migrated.

## Per-file changes

### 1. `agents/pipeline/sim/world.py`
- `_ENTITY_SCENARIO_ALLOCATION` (was `world_politics 5 / tech_science 5 / markets 4 / sport 3 / culture 4 / youtube 6 / x 3`) → `geopolitics 5 / tech 5 / business 4 / sport 3 / arts 4 / youtube 6 / x 3` (sum 30 preserved; `business` kept late in sequence so the roll-over fills earlier categories first and it holds exactly its budget).
- Corrected two stale comments: `world.health` filler is documented as resolving to **geopolitics** via its `world` root (the OLD comment claimed `tech_science` via a health→ map, but `category_for_slug` splits on the first dot, so `world.health` → `world` → geopolitics — same as before; the comment was wrong pre-SP3 too). Updated the scenario docstring "within ``markets``" → "within ``business``".
- The `_TAXONOMY_SPEC` / `_STORY_COUNTS` / `_HEADLINE_BY_NODE` `markets.*`/`world.*` **slugs** were left as-is (valid aliases; renaming them would break the out-of-scope `ranking_sim.py`, which hardcodes those slugs in `build_profiles`/`_profile_checks`).

### 2. `tests/agents/pipeline/test_demand.py`
- All `_allocation("markets", …)` → `_allocation("business", …)`; all `_allocation("world_politics", …)` → `_allocation("geopolitics", …)`.
- Asserted cells re-keyed: `("markets", "markets.crypto")` → `("business", "markets.crypto")`, `("world_politics", "geopolitics.elections")` → `("geopolitics", "geopolitics.elections")`, `("culture", "_all")` → `("arts", "_all")`, etc. The subcategory half (`markets.crypto`, `geopolitics.elections`) is unchanged — it is derived from the follow slug's first-two segments, which are retained aliases.
- `DEFAULT_FEED_ALLOCATION["markets"]` reference → `["business"]`. Docstrings/comments updated to match.

### 3. `tests/agents/pipeline/test_produce_caps.py`
- `_DEFAULT = {"markets":4,"sport":3,"culture":2}` → `{"business":4,"sport":3,"arts":2}`.
- All `_alloc("markets", …)` → `_alloc("business", …)`; all cap dicts `{"markets": N}` → `{"business": N}` and cap assertions `caps["markets"]` → `caps["business"]` (the stories are tagged to `business.semis`, which now buckets into `business`, so the cap key MUST be `business` for the assertion to remain meaningful).
- The interest-node **id** `i-markets` (slug `business.semis`) left unchanged — an arbitrary key, not a taxonomy value. Corrected a stale docstring ("4 categories / 20 stories" → the actual 3-category / 15-story case).

### 4. `tests/agents/pipeline/test_feed_assembly.py`
- `_CATEGORY_INTEREST` keys remapped (`world_politics→geopolitics`, `tech_science→tech`, `markets→business`, `culture→arts`) — this also changes the story-id prefixes (`markets-0` → `business-0`), and `_category_of` parses the same prefix, so it stays internally consistent.
- `_dod_allocation()` spec, the inline allocations in `test_allocation_honors_per_category_budgets_in_sequence` and `test_empty_category_yields_slots_to_next_in_sequence`, the `budget_by_category` dict, the sequence list, and every per-category count assertion remapped (counts kept identical: 5/5/4/3/4 topic + 6/3 source = 30).
- `test_no_slot_is_ever_breaking_kind`: spike story ids `world_politics-big`/`tech_science-big` → `geopolitics-big`/`tech-big`.
- `test_followed_entity_lifts_story_within_its_category`: `markets-nvidia`/`markets-twin` → `business-nvidia`/`business-twin`, allocation `markets` → `business`.
- `test_dont_repeat_excludes_prior_feed_stories`: `excluded_id "markets-0"` → `"business-0"` (the pool now emits `business-*` ids).

### 5. `tests/agents/pipeline/test_ranking_simulation.py`
- `test_entity_follow_lifts_story_above_twin_within_category`: the twin's `category_for_slug(...) == "markets"` assertions → `== "business"` (the twins are `markets.stocks`, which now resolves to `business`). **Intent re-interpreted (Rule 9):** the test asserts "the lift happens WITHIN one category"; the category is now `business`, not `markets`. The lift-within-category guarantee is fully preserved.
- `test_entity_scenario_honors_category_budgets_and_sequence`: `markets_slots`/`== "markets"`/budget-of-4 → `business_slots`/`== "business"`; `expected_order = ["world_politics","tech_science","markets","sport"]` → `["geopolitics","tech","business","sport"]`. Docstring updated.

### 6. `tests/agents/pipeline/test_phase5a_live_e2e.py`
- `_DOD_ALLOCATION` tuple keys remapped to the new roots (counts identical, sum 30).
- `markets_slots`/`== "markets"` budget assertion → `business`; `expected_order` sequence remapped; entity-within-category assertions `== "markets"` → `== "business"`; docstring/comment text updated.

## Tests whose intent was re-interpreted
Only the two entity tests (#5 and the live e2e #6) needed semantic re-reading: the "lift within the markets category" invariant is now "lift within the **business** category" because `markets.stocks` resolves to `business` under SP3. The behavioral guarantee (entity bonus reorders WITHIN one category, business holds exactly its 4-slot budget, sequence order preserved) is unchanged — no assertion was weakened or deleted to go green.

## Validation — pytest `tests/agents/pipeline/`
- **Before:** 30 failed, 279 passed.
- **After:** **8 failed, 301 passed.**
- All 5 of my fixture-only test files (`test_demand`, `test_produce_caps`, `test_feed_assembly`, `test_ranking_simulation`) + the offline-sim path are **GREEN** (44/44 in those four; full sim/ranking_sim straggler run: 51 passed, 1 failed).
- `ruff check` on all 6 files: **All checks passed.**
- `python -m agents.pipeline.sim.ranking_sim` CLI runs clean (all PASS checks).

## Remaining red (8) — none are fixture bugs in my scope

### Mine, but BLOCKED on a missing migration (1) — Rule 12, surfaced not skipped
- `test_phase5a_live_e2e.py::test_live_allocator_honors_budgets_and_lifts_followed_entity`
  - My fixture is now **correct** (uses the new canonical keys). It fails one step later, on the **live DB insert**: `invalid input value for enum feed_category: "geopolitics"`.
  - **Root cause:** the live Postgres `feed_category` enum does not yet contain the 8 new roots. SP1's report references a "SP3 migration 0020" that adds them additively, **but that migration does not exist in `supabase/migrations/` (highest is 0019)** and is certainly not applied to the live DB.
  - This is NOT a taxonomy-fixture problem — it is a hard cross-phase dependency. The test runs locally because `.env` supplies live creds (it SKIPS in network-free CI). It will go green only once the enum-add migration lands AND is applied to the live DB. I did **not** xfail/skip it (Rule 12).

### NOT mine — out of scope (7), pre-existing from SP1
All 7 in `tests/agents/pipeline/test_ranking.py` (NOT one of my 6 files; untouched by me):
`test_nvidia_follower_scores_strictly_higher_and_lands_in_markets`, `test_no_matching_entity_is_byte_identical_to_baseline`, `test_custom_source_follow_beats_seed_source_follow`, `test_unknown_root_slug_falls_back_to_culture`, `test_no_tags_falls_back_to_culture_not_crash`, `test_returns_all_seven_category_keys`, `test_hydrates_category_allocation_rows`.
- These assert the OLD taxonomy (`falls_back_to_culture`, `lands_in_markets`, "seven category keys"). They were red in the 30-failure baseline and remain red. `test_ranking.py` was explicitly NOT in my assignment — the orchestrator must assign its fixture migration to a sub-phase (same treatment as the 5 I fixed).

## Concerns for the orchestrator (esp. SP4 parity smoke)
1. **Missing enum migration is the top blocker.** SP1 assumed a "migration 0020" adds the 8 roots to the Postgres `feed_category` enum; it does not exist. Until someone writes + applies it, every live path that writes a new-taxonomy category (the live e2e here, and prod feed allocation writes) will 22P02. SP4's parity smoke MUST NOT rely on the live DB accepting `geopolitics`/`business`/`arts`/etc. until that migration is applied.
2. **`test_ranking.py` (7 reds) is unassigned.** It needs the identical old→new fixture migration I did on the other 5. Assign it, or it keeps the suite red.
3. **`agents/pipeline/sim/ranking_sim.py` was left untouched** (out of scope, and it only references valid retained interest slugs like `tech.ai`, `world`, `markets.stocks`). It still works (CLI verified). No action needed unless a later phase renames the sim interest slugs themselves — at which point `ranking_sim.py` hardcoded slugs must move in lockstep.
4. **Slug aliases are load-bearing.** I deliberately kept `markets.*`/`world.*` slugs in the sim and tests because they remain valid aliases. If a future phase removes those aliases from `SLUG_TO_CATEGORY`, these fixtures (and the sim) break again.
