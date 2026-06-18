# Phase SP1 — Sub-phase 4: Update sims + tests, verify end-to-end

**Status:** SUCCESS (phase-gate green for the breaking removal; one pre-existing,
unrelated TS test failure surfaced — not introduced here)
**Date:** 2026-06-18

## Mission
Close the phase: make production build + ALL breaking-related tests green with the
breaking feed-category fully gone, the feed still totaling 30 across 7 categories,
and the CoverageMomentum / story_is_breaking velocity signal still computed.

## Files modified

### Group A — Python (sims + stale call site + stale tests)
- `scripts/run_live_batch.py` — call site updated to the new single-return
  `compute_category_produce_caps` signature; dropped the `breaking_headroom` unpack
  + print.
- `agents/pipeline/sim/world.py` — `_ENTITY_SCENARIO_ALLOCATION` dropped the
  `("breaking", 2, 0)` row; its 2 slots absorbed by world_politics +1 / culture +1
  (mirrors `DEFAULT_FEED_ALLOCATION`); reworded 2 breaking-tier comments.
- `agents/pipeline/sim/ranking_sim.py` — removed the breaking-tier observation
  block + the Profile-D `breaking_count == 2` check; Profile-D now asserts only
  `{interest, source}` slot kinds; reworded the §3.1-exempt cap label/comments.
- `agents/pipeline/stages/ranking.py` — fixed the stale `score_and_classify_for_user`
  docstring (8 keys + "breaking left empty" → 7 keys, no breaking). (Out-of-scope
  file for SP1 but the doc was now factually wrong about the function SP4 depends on;
  flagged by SP1 report §3.)
- `tests/agents/pipeline/test_feed_assembly.py` — removed `SLOT_KIND_BREAKING`
  import; updated `_dod_allocation` (5/5/4/3/4 topics + 6/3 source = 30); stripped
  breaking from `test_allocation_honors_per_category_budgets_in_sequence`,
  `test_source_budget_rolls_into_topics`, `test_empty_category_yields_slots`,
  `test_no_allocation_user_gets_balanced_default` (now even split of 30 → 6/topic);
  REPLACED `test_breaking_tier_filled_by_importance_and_not_double_placed` with
  `test_no_slot_is_ever_breaking_kind` (the phase-SP1 regression guard).
- `tests/agents/pipeline/test_produce_caps.py` — `_DEFAULT` dropped breaking; all
  `compute_category_produce_caps` calls use single return; all
  `cap_stories_per_category` calls dropped the `breaking_headroom` positional;
  REPLACED `test_cap_breaking_headroom_keeps_top_importance_beyond_cap` with
  `test_cap_keeps_only_the_category_cap_no_headroom_union`.
- `tests/agents/pipeline/test_detail_templates.py` — dropped the breaking row from
  `_EXPECTED`; `test_all_nine_categories_present` → `..._eight_...` (asserts 8, no
  breaking); `test_coverage_only_on_breaking_and_world` → `..._on_world`; the two
  `breaking_wins` resolver tests rewritten to assert `is_breaking` is now
  accepted-but-IGNORED (resolves to the topic category).
- `tests/agents/pipeline/test_persist_detail_analytics.py` — the breaking-coverage
  test rewritten as `test_breaking_coverage_still_sets_velocity_flag_but_keeps_topic_category`:
  asserts `story_is_breaking is True` (KEEP — velocity signal) AND
  `story_detail_category == "world"` (no breaking template).
- `tests/agents/pipeline/test_ranking.py` — `test_returns_all_eight_category_keys`
  → `..._seven_...` (7 keys, no breaking); the breaking allocation-row fixture in
  `test_hydrates_category_allocation_rows` swapped to `world_politics`.
- `tests/agents/pipeline/test_ranking_simulation.py` — `test_entity_scenario_...`
  drops the `breaking == 2` check for an `only {interest, source}` assertion + a
  None-interest-aware sequence check; the cap-label string + a stale comment fixed;
  `test_engagement_raises_weight_without_collapsing_feed` UPDATED (see "behavioral
  change" below).
- `tests/agents/pipeline/test_phase5a_live_e2e.py` — `_DOD_ALLOCATION` rebalanced
  (no breaking, totals 30); breaking-tier assertions replaced with
  `{interest, source}` + topic-sequence checks (live test, runs only with DB creds).

### Group B — TS (out-of-scope production consumers + stale tests)
Production consumers (build was RED on these — now fixed):
- `src/components/blip/reel/ArticleLayer.tsx` — removed the `feed_slot_kind ===
  "breaking"` "BREAKING" chip branch (+ now-unused `DESIGN_BUCKETS` import).
- `src/components/blip/reel/ReelStage.tsx` — same "Breaking" chip branch + unused
  `DESIGN_BUCKETS` import removed.
- `src/lib/feed/fixtureFeed.ts` — `digest-1` slot kind `"breaking"` → `"source"`
  (so dev-mode QAs the surviving non-interest chip).
- `src/lib/feed/supabaseFeed.ts` — row→slot mapping now `=== "source" ? "source" :
  "interest"` (never emits "breaking"; a legacy DB "breaking" enum row degrades to
  interest); comments updated.
- `src/components/onboarding/BuildYour30.tsx` — JSDoc updated (removed the
  "always-on breaking block" reference; 8 → 7 blocks).
- `src/lib/feedAllocation.ts` — the 2 JSDoc examples with a `breaking` bucketId
  updated to valid buckets.
Stale tests:
- `tests/lib/feedBuckets.test.ts` — dropped the `ALWAYS_INCLUDED_CATEGORY_BUCKET`
  import; bijection now 8 buckets; identity-map list now 6; `allowedBucketsForSelections`
  / `buildSegmentsForSelections` assert NO forced bucket (empty backing → empty seed),
  narrow-pick total 20 → 18, order without breaking; added a "no breaking bucket"
  guard test.
- `tests/lib/feedAllocation.test.ts` — `breaking` bucketIds in fixtures/assertions
  swapped to `sport`/`world` (incl. the prune-exclusion list string + the podcasts
  graceful-degrade retry).
- `tests/lib/detailTemplates.test.ts` — dropped the breaking row from `EXPECTED`;
  "9 categories" → "8 (no breaking)"; "coverage only on breaking and world" →
  "only on world".

Also auto-formatted `src/lib/feedBuckets.ts` (biome collapsed the now-8-member
`DesignBucketId` union to one line) so `biome check` is clean.

## KEEP sites preserved (velocity signal — NOT touched functionally)
`CoverageMomentum = breaking|developing|settled` (models.py), `_derive_momentum` +
`coverage_is_breaking` (coverage_gdelt.py), `stories.story_is_breaking` persistence
(persist.py / persist_helpers.py / orchestrator.py), `CoverageMode` (TS
types/detail.ts), the GDELT recall keyword (gdelt_bigquery.py). The
`detail_category_for*` `is_breaking` param is retained as accepted-but-IGNORED
(SP1's surgical backward-compat choice).

## Tests updated vs deleted
- **Deleted (replaced, not just removed):** none deleted outright. Two tests of the
  removed breaking tier were REPLACED with regression guards for the new contract
  (`test_no_slot_is_ever_breaking_kind`,
  `test_cap_keeps_only_the_category_cap_no_headroom_union`) — preferring update over
  deletion (Rule 9): they now fail if the breaking tier / headroom union is ever
  reintroduced.
- **Behavioral change (genuine, not a weakened assertion):**
  `test_engagement_raises_weight_without_collapsing_feed` previously asserted the
  engaged interest's feed SHARE rises across days. Under the new even-split default
  (breaking tier gone) a default-allocation user's per-category budgets are FIXED,
  so the engaged share is bounded by its category budget and no longer grows the way
  the old breaking-tier importance spike did. The test now asserts the invariants
  that still hold (and still encode WHY): the engagement loop still moves WEIGHTS
  (engine alive), an un-engaged interest decays but never dies, and on EVERY day the
  engaged share stays well under a majority (no collapse). This is a real semantic
  consequence of removing breaking, surfaced — not hidden.

## Velocity-signal-preserved tests (the KEEP-site guard, Rule 9)
- CONFIRMED existing: `tests/agents/pipeline/test_coverage_gdelt.py::test_reach_breaking_momentum_for_tight_seendate_burst`
  — asserts `coverage_momentum == "breaking"` is still computed from the GDELT
  seendate burst (derivation side).
- ADDED/STRENGTHENED:
  `tests/agents/pipeline/test_persist_detail_analytics.py::test_breaking_coverage_still_sets_velocity_flag_but_keeps_topic_category`
  — asserts `story_is_breaking is True` (signal still persisted) AND the detail
  category resolves to the topic, not a removed breaking template (persistence side).

## Validation
- **pytest** `tests/agents/pipeline/` → **262 passed** (PASS).
- **pytest** `tests/agents/` (full) → **556 passed, 4 warnings** (PASS).
- **ruff** `agents/pipeline/ scripts/run_live_batch.py tests/agents/pipeline/` →
  **All checks passed!** (PASS)
- **sim CLI** `python -m agents.pipeline.sim.ranking_sim` → all profile checks PASS;
  Profile D = 30 slots, `kinds={'interest': 30}`, "only {interest, source} slot
  kinds (no breaking)" PASS.
- **npm run build** → **✓ Compiled successfully**, 6/6 static pages, exported (PASS).
- **biome check** (changed TS files) → clean after format (PASS).
- **npm test** → **466 passed | 1 failed (55 files: 54 passed | 1 failed)**.
  The single failure is `tests/lib/app/tabBar.test.tsx > renders all four tabs` —
  it expects `[Today, Archive, Sources, Settings]` but the TabBar now renders a
  `Thirty` tab (added by commit 324dc6a, "dedicated 'Thirty' tab"). Verified
  PRE-EXISTING and UNRELATED: `git stash && npm test -- tabBar` fails identically
  on the pre-SP1 tree. NOT introduced by this phase; left untouched per Rule 3
  (out of scope — a stale test for an unrelated nav feature).

## Definition of done (Step E)
- `grep -rin "breaking" agents/pipeline/ src/lib src/types src/components
  scripts/run_live_batch.py` → every remaining hit is a KEEP site
  (CoverageMomentum / coverage_* / story_is_breaking / is_breaking velocity
  plumbing), a comment/docstring documenting the removal, or unrelated
  ("Breaking news" acoustic-alignment word fixtures, a `_archive/` file). NO
  functional feed-category / tier / slot / template / bucket / allocation usage of
  `breaking` remains. **PASS**
- A test asserts: feed has no breaking slots, 7 categories, totals 30, only
  `{interest, source}` slot kinds — `test_no_slot_is_ever_breaking_kind`,
  `test_source_budget_rolls_into_topics_so_feed_totals_30`,
  `test_no_allocation_user_gets_balanced_default`,
  `test_returns_all_seven_category_keys`,
  `test_entity_scenario_honors_category_budgets_and_sequence`. **PASS**
- A test asserts the CoverageMomentum / story_is_breaking velocity signal is STILL
  computed — `test_reach_breaking_momentum_for_tight_seendate_burst` +
  `test_breaking_coverage_still_sets_velocity_flag_but_keeps_topic_category`. **PASS**

**DoD: PASS** for the breaking-removal phase gate. The literal "npm test fully
green" line has one pre-existing, unrelated red (tabBar/Thirty) that this phase did
not cause and (per Rule 3) did not touch.

## Residual concerns
1. **Pre-existing red:** `tests/lib/app/tabBar.test.tsx` is stale vs the live
   5-tab TabBar (Thirty tab, commit 324dc6a). One-line fix (add "Thirty" to the
   expected array) — out of this phase's scope; recommend folding into the
   Thirty-tab owner's cleanup or a follow-up.
2. **SP3 migration NOT applied** (per brief): `0017_drop_breaking_allocation.sql`
   is authored but not run. The 7-value `CategoryAllocation` Literal now rejects a
   `"breaking"` row at the DB boundary, so SP3 must land before the next live batch
   reads `user_feed_allocation`.
3. **Stale prose in out-of-scope files (left per Rule 3):** `models.py:501,551` and
   `orchestrator.py:379` still say `is_breaking` "selects/uses the Breaking
   template" — functionally the param is now ignored. Non-functional; flag for a
   docs pass with the persist/orchestrator owner.
