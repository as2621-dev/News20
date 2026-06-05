# Phase 5a — Sub-phase 3 Execution Report

**Category-budget + sequence allocator (feed_assembly rewrite)**

**Status:** SUCCESS · **Validation:** PASS (ruff clean; 11/11 feed_assembly; broader regression scoped) · **Definition of done:** PASS (4/4 invariants)
**Date:** 2026-06-05

---

## 1. What was implemented

Rewrote `agents/pipeline/feed_assembly.py` from the old **affinity-proportional**
allocator (`ranking-spec.md` §3 — proportional split / floor-1 / ~40% cap /
exploration tier) to the owner's **"Build your 30, in order"** two-layer model:

- **Layer 1 (allocation, this module):** reads the user's `category_allocation`
  (`CategoryAllocation` rows: per-category `allocation_slot_count` +
  `allocation_sort_order`) and honors those counts + manual sequence.
- **Layer 2 (scoring, consumed from SP2):** `score_and_classify_for_user(...)`
  returns the 8 entity-aware category buckets; the allocator fills each category's
  slots from its top-Score qualifying (`≥ T`) candidates.

The new `assemble_user_feed` pipeline (pure function, no DB/network):

1. **Resolve target** — `total_target = min(Σ allocation_slot_count, 30)`. A user
   whose budgets sum to < 30 gets a shorter feed (the allocator never invents
   slots); a mis-summed allocation (the cross-category `SUM==30` is NOT
   DB-enforced) can never overshoot 30.
2. **Breaking tier** (`_select_breaking`) — fills the user's `breaking` budget by
   **top-`importance` across ALL topic buckets**, and REMOVES each chosen story
   from its topic bucket so it is never double-placed. Excludes prior-feed stories.
3. **Pass 3 — topic budgets** — fill each TOPIC category to its OWN budget, in the
   user's sequence, from its top-Score qualifying candidates.
4. **Pass 4 — source soft-roll** — `youtube`/`x` are budgeted-but-empty (phase-5d);
   their banked budget (plus any breaking/topic shortfall) is distributed by
   sequence across the topic categories with surplus, until the feed reaches
   `total_target` (or stories are exhausted). This is what keeps `len(feed) == 30`.
5. **Order + materialize** — breaking first, then topic categories by sequence;
   1-based contiguous `feed_position`; breaking slots carry `feed_matched_interest_id
   = None`, topic slots carry the candidate's matched interest.

**Default allocation** (`_default_allocation`) — a user with NO `user_feed_allocation`
rows gets the balanced fallback: `breaking 4` + an even split of the remaining 26
across the TOPIC categories that have available stories (empty categories not
budgeted; largest-remainder so it sums to 30).

**Preserved verbatim:** `write_daily_feed` / `_existing_feed_count` /
`FeedWriteResult` produce-once idempotency, the `AllocatedSlot` output-row contract
(`feed_story_id`, `feed_position`, `feed_score`, `feed_matched_interest_id`,
`feed_slot_kind`), §3.8 don't-repeat (prior-feed exclusion) + within-feed dedup, and
the `ScoredCandidate` re-export that `orchestrator.py` imports from this module.

## 2. Files changed

- **Rewritten:** `agents/pipeline/feed_assembly.py`
- **Rewritten:** `tests/agents/pipeline/test_feed_assembly.py`
- **Untouched (as mandated):** `ranking.py`, `categories.py`, `orchestrator.py`,
  `daily_batch.py`, the migration, and the SP4-owned sim (`sim/world.py`,
  `sim/ranking_sim.py`, `test_ranking_simulation.py`).

## 3. Divergences from the plan (surfaced — Rule 7/12)

1. **Allocator signature is ADDITIVE, not replaced.** `assemble_user_feed` gains
   `followed_entities` and `category_allocation` as new keyword params (defaulting
   to `None`), and keeps the legacy `exploration_candidates_by_interest` param
   **accepted-and-ignored**. WHY: `orchestrator.assemble_daily_feeds` and
   `sim/ranking_sim.simulate_profile` are OUT of my 2-file scope but still call this
   entry point with the old kwargs — a backward-compatible signature is the only way
   to rewrite the body without editing those callers. The new logic activates when
   the new kwargs are supplied; the no-kwarg call falls through to the balanced
   default. Covered by `test_backward_compat_call_without_allocation_or_entities`.

2. **Exploration tier RETIRED.** The category-budget model has no exploration
   reserve (the user reserves slots by category), so the old `~10% exploration` pass
   is gone. `SLOT_KIND_EXPLORATION` is kept as a constant for the `daily_feeds.feed_slot_kind`
   vocabulary, but no slot is ever emitted with it. This is the source of the one
   broader-suite regression (see §5).

3. **`total_target` semantics.** The plan says "feed totals 30"; I implemented
   `min(Σ budgets, 30)` so a user who dials budgets below 30 gets a shorter feed
   rather than the allocator padding to 30. The DoD allocation (sums to 30) yields
   exactly 30; the source soft-roll redistributes WITHIN that sum.

## 4. Code-review findings + fixes (Step B/C)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | **High** | First draft dumped ALL banked source slots onto the FIRST topic category in one pass → over-concentrated the roll-over (one category absorbed all 9). | Split into Pass 3 (each topic to its OWN budget) + Pass 4 (distribute leftover capacity by sequence). Roll-over now spreads across categories with surplus. |
| 2 | **High** | Used `feed_slot_budget` (30) as the topic capacity → a user whose budgets summed to 21 (no source budget) got a 30-slot feed (Pass 4 over-filled). | Introduced `total_target = min(Σ budgets, 30)`; topic capacity = `total_target − len(breaking)`. Caught by `test_allocation_honors_per_category_budgets_in_sequence`. |
| 3 | Low | `source_roll_slots` computed but unused after refactor (ruff F841 risk). | Surfaced in the completion log (`source_roll_slots=…`) as audit info. |
| 4 | Low | Dead `and source_roll_slots >= 0` guard on Pass 4. | Removed. |

No raw dicts at the model boundary — `CategoryAllocation` / `FollowedEntity` /
`ScoredCandidate` / `AllocatedSlot` Pydantic models throughout. `fix_suggestion` on
the empty-profile + empty-feed logs preserved.

## 5. Validation — verbatim

**ruff format + check** (`agents/pipeline/feed_assembly.py` +
`tests/agents/pipeline/test_feed_assembly.py`):
```
2 files left unchanged
--- check ---
All checks passed!
```

**pytest** (`tests/agents/pipeline/test_feed_assembly.py`):
```
11 passed in 0.03s
```

**Broader regression** (`tests/agents/pipeline`, then full `tests/agents`):
```
tests/agents/pipeline   → 1 failed, 162 passed
tests/agents (all)      → 1 failed, 273 passed, 2 warnings
```

**The single failure — SURFACED, NOT MASKED (Rule 12):**
```
FAILED tests/agents/pipeline/test_ranking_simulation.py::test_niche_profile_surfaces_depth_and_explores
  assert any(s.feed_slot_kind == "exploration" for s in slots)  → False
```
This is the **expected, correct** consequence of retiring the exploration tier
(§3 finding #2). `test_ranking_simulation.py` is an **SP4-owned file** (the offline
ranking sim, commit `14b923f`; the phase assigns `sim/world.py` + `sim/ranking_sim.py`
to SP4). It is OUT of my 2-file scope, so I did NOT edit it to make the assertion
pass — that would mask a real behavioral change. 6 of the 7 sim tests still pass; only
the obsolete exploration assertion fails. **SP4 must update this test** when it
extends the sim (drop/replace the `exploration` assertion; assert the category-budget
ordering invariant instead). The orchestrator-batch path (`test_daily_batch.py`,
22 tests) that drives this allocator through `assemble_daily_feeds` **passes** clean.

No typechecker (mypy/pyright) is configured in the repo; ruff is the linter and passes.

## 6. Definition of done — PASS (4/4)

| DoD invariant | Test | Result |
|---|---|---|
| Allocation `{breaking 2, world 4, tech 5, markets 4, sport 3, culture 3, youtube 6, x 3}` → exact 2/4/5/4/3/3 topic+breaking counts, 9 source slots rolled into topics so `len(feed) == 30`, sequence-ordered, no dupe, prior excluded | `test_allocation_honors_per_category_budgets_in_sequence` (exact per-category INTEREST-slot counts + sequence) + `test_source_budget_rolls_into_topics_so_feed_totals_30` (len==30, no dupes, contiguous, zero source items) + `test_dont_repeat_excludes_prior_feed_stories` | PASS |
| Nvidia-followed story appears within markets/tech_science, ranked ABOVE an equivalent non-followed story in that bucket | `test_followed_entity_lifts_story_within_its_category` (order + strict `feed_score` inequality) | PASS |
| No-allocation user gets the balanced default (breaking 4 + even split) | `test_no_allocation_user_gets_balanced_default` (4 breaking, all 5 topics represented, 30 slots) | PASS |
| A category with no eligible stories yields its slots to the NEXT category by sequence (not a gap) | `test_empty_category_yields_slots_to_next_in_sequence` (sport omitted from pool; its 8 slots roll forward; feed still 30; zero sport stories) | PASS |

Supporting: `test_breaking_tier_filled_by_importance_and_not_double_placed` (no
double-placement), `test_empty_profile_returns_no_slots`,
`test_backward_compat_call_without_allocation_or_entities`,
`test_write_daily_feed_skips_empty_slots`, `test_write_daily_feed_is_idempotent_on_rerun`.

Each test encodes WHY (Rule 9): the per-category count test fails if a budget is
mis-counted or the sequence is dropped; the source-roll test fails if youtube/x
budgets are not redistributed (feed would be 21); the entity test fails if
`followed_entities` is not threaded into the scorer; the default test fails if the
empty-allocation path does not synthesize a fallback; the yield-forward test fails
if a sparse category leaves a gap; the breaking test fails if a promoted story is
not removed from its topic bucket.

## 7. Concerns + contract for SP4

### 7.1 Allocator entry-point SP4's e2e + sim must call
```python
assemble_user_feed(
    profile_interests: list[UserProfileInterest],
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    followed_entities: list[FollowedEntity] | None = None,   # phase-5a entity bonus
    category_allocation: list[CategoryAllocation] | None = None,  # "Build your 30"
    prior_feed_story_ids: set[str] | None = None,
    exploration_candidates_by_interest: Any = None,  # LEGACY — accepted, IGNORED
    feed_slot_budget: int = FEED_SLOT_BUDGET,        # 30
    default_breaking_slots: int = DEFAULT_BREAKING_SLOTS,  # 4
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    now_utc: Any = None,
) -> list[AllocatedSlot]
```
- `AllocatedSlot` fields SP4 reads for the live e2e assertions: `feed_story_id`,
  `feed_position` (1..N), `feed_score`, `feed_matched_interest_id`, `feed_slot_kind`
  (`"breaking"` | `"interest"`).
- The **batch path** is unchanged: `orchestrator.assemble_daily_feeds(...)` calls
  `assemble_user_feed` per user. **NOTE:** the orchestrator currently does NOT
  forward `followed_entities` / `category_allocation` into `assemble_user_feed`
  (its call site predates this rewrite — see `orchestrator.py:583`). For the live
  e2e + the entity-boost to take effect **through the batch**, the orchestrator's
  `assemble_daily_feeds` call must pass `followed_entities=user_inputs.followed_entities`
  and `category_allocation=user_inputs.category_allocation`. This is a **one-line-each
  wiring change in `orchestrator.py`** — OUT of my 2-file scope, so flagged here for
  the orchestrator/SP4 to apply. `ActiveUserFeedInputs` already carries both fields
  (daily_batch hydrates them), so it is purely a forward at the call site.

### 7.2 Seeding the e2e
- **`category_allocation`:** insert `user_feed_allocation` rows for the seeded user
  (one per budgeted category): `(follow_user_id, allocation_category, allocation_slot_count,
  allocation_sort_order)`. Use the DoD allocation `{breaking 2, world_politics 4,
  tech_science 5, markets 4, sport 3, culture 3, youtube 6, x 3}` (sums to 30) so the
  e2e can assert the exact 2/4/5/4/3/3 topic counts + the 9 source slots rolled.
  `daily_batch._load_category_allocation` hydrates these into
  `ActiveUserFeedInputs.category_allocation`.
- **Nvidia `user_entity_follows`:** insert a `user_entity_follows` row joined to the
  Nvidia `entities` row (label `Nvidia`, ticker `NVDA`, kind `company`) — source
  `custom` for the strongest bonus. `daily_batch._load_followed_entities` hydrates it
  into `ActiveUserFeedInputs.followed_entities`. Seed two equivalent markets stories
  (one titled with "Nvidia", one without) so the e2e can assert the Nvidia story
  outranks its twin within the markets slots (strict `feed_score` inequality —
  the bonus is `normalized_follow_weight × ENTITY_BONUS_WEIGHT = 0.3`).

### 7.3 Deterministic ordering invariant the sim should assert
- **Per-category budget honored:** count `feed_slot_kind == "interest"` slots per
  category prefix → equals the seeded `allocation_slot_count` (subject to story
  availability), in `allocation_sort_order` sequence; `breaking` count equals its
  budget; `len(feed) == min(Σ budgets, 30)`; no duplicate `feed_story_id`.
- **Source soft-roll:** with youtube/x budgeted and a surplus topic pool,
  `len(feed) == 30` and zero source-category items appear.
- **Entity lift:** the Nvidia story's `feed_score` is strictly greater than its
  non-followed twin's, and it precedes the twin in `feed_position` within the same
  category.
- **Update the obsolete exploration assertion** in
  `test_ranking_simulation.py::test_niche_profile_surfaces_depth_and_explores`
  (§5) — the exploration tier no longer exists; replace with a category-budget
  ordering assertion.

### 7.4 ENTITY_BONUS_WEIGHT tuning (open)
`ENTITY_BONUS_WEIGHT = 0.3` (in `ranking.py`) is still a first draft (open-Q2). The
allocator is agnostic to its magnitude — it sorts each category by the entity-aware
Score. SP4's sim/e2e is the place to confirm/tune it.

---

**Return to orchestrator:** STATUS SUCCESS · files: `agents/pipeline/feed_assembly.py`,
`tests/agents/pipeline/test_feed_assembly.py` · Validation PASS (ruff clean; 11/11
feed_assembly; 273/274 agents — the 1 failure is the SP4-owned sim's retired
exploration assertion, surfaced not masked) · DoD PASS (4/4) · Key concerns:
(1) `orchestrator.assemble_daily_feeds` must forward `followed_entities` +
`category_allocation` into `assemble_user_feed` for the batch/e2e to use them
(one-line wiring, out of my scope); (2) SP4 must update the obsolete exploration
sim test.
