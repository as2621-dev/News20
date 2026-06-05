# Phase 5a — Sub-phase 2 Execution Report

**Entity hydration + entity-aware Score + story→category classifier**

**Status:** SUCCESS · **Validation:** PASS · **Definition of done:** PASS (4/4 test classes)
**Date:** 2026-06-05

---

## 1. What was implemented (deliverables a–e)

**(a) `FollowedEntity` model + loader hydration + source weighting**
- `FollowedEntity` (in `ranking.py`): `entity_id`, `entity_label`, `entity_ticker` (nullable), `entity_kind`, `follow_weight`, `follow_path`.
- `daily_batch._load_followed_entities` — ONE batched `.in_()` join `user_entity_follows ⋈ entities` (PostgREST embed `entities(entity_label,entity_ticker,entity_kind)`), grouped per user.
- `daily_batch._load_category_allocation` — ONE batched `.in_()` over `user_feed_allocation`, grouped per user → `CategoryAllocation` rows.
- **custom>more>seed encoded in the loader** (`FOLLOW_SOURCE_WEIGHT = {seed:1.0, more:2.0, custom:3.0}` in `ranking.py`): each follow's flat DB `follow_weight` (1.0 for all sources per 0007) is multiplied by its source multiplier at hydration time. The DB does NOT pre-weight — the loader does.
- Both wired into `load_active_user_inputs`, which now populates `ActiveUserFeedInputs.followed_entities` + `.category_allocation`.

**(b) `entity_title_match(story, followed_entities)`** — whole-word `entity_label` match in `story.canonical_title` via cached `\b…\b` regex (`re.IGNORECASE`), PLUS word-bounded `entity_ticker` match **only when `entity_kind == 'company'`**. Dedupes matched entities by identity `(label.casefold(), ticker, kind)`, keeping the highest-`follow_weight` representative so one logical entity (Nvidia across 3 paths) bonuses once.

**(c) additive EntityBonus** — `ENTITY_BONUS_WEIGHT = 0.3` (named constant, alongside α/β/γ). `normalize_entity_follow_weights` max-normalizes per user (mirrors `normalize_affinities`). `compute_entity_bonus(story, followed_entities, normalized_weights) → (bonus, matched_entity_id)`: `bonus = normalized_follow_weight × ENTITY_BONUS_WEIGHT` for the **strongest** matching entity (max, NOT sum — a story is one lift). The base α/β/γ terms are untouched; the bonus is added in `score_and_classify_for_user`.

**(d) `assign_category(story_id, tags_by_story, interest_nodes) → FeedCategory`** — single best-fit: the category of the story's **lowest-`match_depth`** interest tag (leaf 0 < parent 1 < grandparent 2); tiebreak documented below. Maps via `categories.category_for_slug`. Exactly one category per story; untagged/unresolvable → `culture` default (never drops a story).

**(e) `score_and_classify_for_user(...) → {FeedCategory: [ScoredCandidate]}`** — runs the existing per-interest fallback scorer, collapses to one best candidate per story, folds in the entity bonus, classifies, and buckets into the 8 categories. Always returns all 8 keys; topic buckets descending by entity-aware score; `youtube`/`x`/`breaking` present-but-empty.

**`categories.py` (NEW, pure)** — `FeedCategory` Literal (8-key enum mirror, in 0008 order), `TOPIC_CATEGORIES`/`SOURCE_CATEGORIES`, the locked `SLUG_TO_CATEGORY` map, `DEFAULT_CATEGORY = "culture"`, `category_for_slug` (resolves any dotted slug by its **root** segment), `empty_category_buckets`, and the `CategoryAllocation` Pydantic model (one `user_feed_allocation` row).

`ScoredCandidate` gained 3 additive fields with defaults (no break to existing callers): `entity_bonus: float = 0.0`, `matched_entity_id: str | None = None`, `feed_category: FeedCategory | None = None`.

## 2. Files created / modified

- **Created:** `agents/pipeline/categories.py`
- **Modified:** `agents/pipeline/stages/ranking.py`, `agents/pipeline/daily_batch.py`, `agents/pipeline/orchestrator.py`, `tests/agents/pipeline/test_ranking.py`
- **Untouched (as mandated):** `feed_assembly.py` (SP3's), the migration.

## 3. Divergences from the plan (surfaced per Rule 7/12)

1. **`assign_category` tiebreak = interest *slug*, not *sort order*.** The locked rule says "tiebreak by interest sort order", but `InterestNode` (the only taxonomy struct passed to the classifier) carries no `interest_sort_order` field. Rather than thread a new field through the loader + ingestion models (out of SP2's 5-file scope, Rule 3), I tiebreak deterministically by `interest_slug`. The common case has a single lowest-`match_depth` tag, so the tiebreak rarely fires; it is stable and deterministic. **Documented in the `assign_category` docstring.** If exact sort-order tiebreak matters, SP3/SP4 can add `interest_sort_order` to `InterestNode` and swap the key.

2. **Seed-slug reality vs. the phase's "13 slugs".** The phase named 13 slugs including `geopolitics`, `markets`, `wildcard`. The live `interests.sql` seed has 10 **depth-0** slugs: `world, business, tech, sport, health, entertainment, climate, lifestyle, crypto, science` (the names `geopolitics`/`markets`/`wildcard` are `interest_segment_slug` *accents*, not interest slugs). `SLUG_TO_CATEGORY` includes BOTH the real slugs AND the accent aliases (harmless extra keys) so it matches the locked map verbatim and is forward-compatible. Real data resolves via the real slugs (verified: all 10 depth-0 slugs + the 3 ambiguous defaults map correctly).

3. **`compute_entity_bonus` is `max`, not `sum`, across multiple matching follows.** The plan formula is per-matching-entity; when several *distinct* follows match one story I take the strongest (max normalized weight), not the sum, so a story can't leapfrog the feed by matching many follows. Documented + tested (`test_strongest_matching_entity_wins_not_the_sum`).

## 4. Code-review findings + fixes (Step B/C)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | **High** | `assign_category` used `DEFAULT_CATEGORY` but it wasn't imported (ruff F821). | Added to the `categories` import. |
| 2 | Medium | Unused `best_interest_id` binding from the `min(...)` unpack (ruff F841 risk). | Renamed to `_best_interest_id`. |
| 3 | Medium | Non-company entity ticker could boost a generic headline (the `AI`/`ON`/`ALL` risk). | Ticker match is **gated on `entity_kind == 'company'`** AND word-bounded. Tested both directions (`test_noncompany_ticker_is_ignored`, `test_company_ticker_ai_still_word_bounded`). |
| 4 | Medium | Substring false positives ("Meta" ⊂ "metabolism"). | `\b…\b` whole-word regex with `re.escape`. Tested (`test_label_does_not_match_substring`). |
| 5 | Low | Same entity via N follow paths double-bonusing. | Identity dedup `(label.casefold, ticker, kind)`, keep highest weight. Tested. |
| 6 | Low | Orphan `user_entity_follows` row (missing joined `entities`) → labelless entity. | Loader skips it with a `fix_suggestion` warning. Tested (`test_orphan_follow_without_joined_entity_is_skipped`). |
| 7 | Low | Regex recompilation per (entity, story) at batch scale. | `_WORD_BOUNDARY_PATTERN_CACHE` compiles once per distinct term. |

No `any`-equivalent untyped dicts at the model boundary — DB rows are converted to `FollowedEntity` / `CategoryAllocation` Pydantic models in the loader.

## 5. Validation — verbatim

**ruff check** (`agents/pipeline/{categories,stages/ranking,orchestrator,daily_batch}.py` + `tests/agents/pipeline/test_ranking.py`):
```
All checks passed!
```
**ruff format**:
```
1 file reformatted, 4 files left unchanged
```
**pytest** (`tests/agents/pipeline/test_ranking.py`):
```
27 passed, 1 warning in 0.37s
```
**Full pipeline + full agents suite (regression)**:
```
tests/agents/pipeline   → 160 passed
tests/agents (all)      → 271 passed, 2 warnings
```
No typechecker (mypy/pyright) is configured in the repo (not in `.venv` or `requirements.txt`); ruff is the configured linter and passes.

## 6. Definition of done — PASS (4/4)

| DoD class | Test(s) | Result |
|---|---|---|
| **happy** — Nvidia follower's "Nvidia Q3 earnings beat" scores **strictly higher** than the no-entity baseline AND lands in `markets` | `test_nvidia_follower_scores_strictly_higher_and_lands_in_markets` (asserts `score == plain.score + ENTITY_BONUS_WEIGHT`, `feed_category == "markets"`, base α/β/γ unchanged) | PASS |
| **false-positive guard** — ticker `AI` on a non-company entity does NOT boost a generic "AI" headline; "Meta" ∉ "metabolism" | `test_noncompany_ticker_is_ignored`, `test_label_does_not_match_substring`, `test_company_ticker_ai_still_word_bounded` | PASS |
| **edge** — follower whose entities match no story gets byte-identical scores | `test_no_matching_entity_is_byte_identical_to_baseline` (asserts `model_dump()` equality vs baseline) | PASS |
| **custom > seed** — custom-source follow yields a LARGER bonus than seed | `test_custom_source_follow_beats_seed_source_follow` (custom bonus `== 0.3`, seed `== 0.1`) | PASS |
| **DB hydration vs MOCKED client** | `TestLoaderHydratesEntitiesAndAllocation` (3 tests — join shape, source weighting, one-query batching, allocation rows, orphan skip) | PASS |

Each test encodes WHY (Rule 9): the false-positive tests fail the moment word boundaries or the company-ticker gate are dropped; the edge test fails if the bonus ever perturbs a non-match; the custom>seed test fails if the loader stops applying `FOLLOW_SOURCE_WEIGHT`.

## 7. Concerns + contract for SP3

### 7.1 EXACT return shape of `score_and_classify_for_user` (the SP3 input contract)
```python
score_and_classify_for_user(
    profile_interests: list[UserProfileInterest],
    followed_entities: list[FollowedEntity],
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    now_utc: datetime | None = None,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> dict[FeedCategory, list[ScoredCandidate]]
```
- Returns **all 8 `FeedCategory` keys, always** (use `categories.empty_category_buckets()` as the canonical key set — no `KeyError` risk).
- Each topic bucket is a list of `ScoredCandidate`, **already sorted descending by entity-aware `score`**.
- **One candidate per story** (collapsed to its strongest interest before classification) — no duplicate story across buckets, since each story classifies into exactly one category. Clean for 30-slot accounting.

### 7.2 `ScoredCandidate` fields SP3 will read
- `story_id` — the story to place.
- `score` — **entity-aware** Score (base + `entity_bonus`); use this for in-category ranking.
- `importance` — the 0–1 importance term; **SP3 fills `breaking` by top-`importance` across ALL categories** (see 7.3).
- `matched_interest_id` — for `daily_feeds.feed_matched_interest_id`.
- `matched_entity_id` / `entity_bonus` — audit / "why is this here" (0.0 / None when no entity matched).
- `feed_category` — the bucket key the candidate is in (redundant with the dict key, carried for convenience).
- `affinity`, `depth_match`, `freshness`, `fallback_depth` — unchanged from SP3.

### 7.3 How `breaking` is meant to be filled — **SP3 owns it**
`breaking` is a **tier**, not a slug bucket. `score_and_classify_for_user` returns `breaking: []`. SP3 fills the user's `breaking` budget by selecting the **top-`importance`** candidates **across all topic buckets** (mirrors the existing `feed_assembly._select_breaking`, which sorts by `compute_importance_score(story_outlet_count)`). A story chosen for `breaking` should be **removed from its topic bucket** so it isn't double-placed (the old allocator's `used_story_ids` dedup pattern).

### 7.4 Source categories (`youtube`/`x`) + roll-over
Both are returned **empty** (no slug maps to them; phase-5d). SP3's allocator must **soft-roll their budgeted slots into the topic categories by sequence** so the feed still totals 30 (per the phase decision). `category_allocation` for these categories will have a non-zero `allocation_slot_count` but zero candidates — SP3 redistributes.

### 7.5 `category_allocation` shape SP3 reads
`ActiveUserFeedInputs.category_allocation: list[CategoryAllocation]` — each row: `allocation_category` (one of the 8), `allocation_slot_count` (0..30), `allocation_sort_order` (the manual sequence; lower = earlier; NOT unique). The cross-category `SUM(slot_count) == 30` invariant is **NOT** DB-enforced (SP1 report §7.4) — SP3's roll-over logic owns totalling to 30. A user with **no** allocation rows → `category_allocation == []` → SP3 applies the balanced default (`breaking 4` + even split per the phase).

### 7.6 Residual short-ticker false-positive risk (for SP4 tuning)
The company-ticker gate + `\b…\b` bound the risk but do not eliminate it: a **company** entity whose ticker is a common English word (`AI`, `ON`=ON Semi, `ALL`=Allstate) still matches that word as a standalone token (e.g. "Shares of AI jump" fires for a company with ticker `AI`). This is the **owner-accepted** residual risk (phase decision: "documented + tested"). If SP4's sim shows noise, the cheap fix is a short-common-word ticker stoplist applied in `_entity_matches_title` — left out now to avoid speculative scope (Rule 2).

### 7.7 `ENTITY_BONUS_WEIGHT = 0.3` is a first draft
Tune at the SP4 sim/e2e (open question 2). At 0.3 the bonus is comparable to the full Importance term (`0.3`) — a strong-but-not-dominating lift. The constant lives with α/β/γ in `ranking.py`.

---

**Return to orchestrator:** STATUS SUCCESS · files: `agents/pipeline/categories.py` (new), `agents/pipeline/stages/ranking.py`, `agents/pipeline/daily_batch.py`, `agents/pipeline/orchestrator.py`, `tests/agents/pipeline/test_ranking.py` · Validation PASS (ruff clean; 27 ranking tests, 271 agents tests pass) · DoD PASS (4/4 classes + mocked-client hydration) · Key SP3 contract: `score_and_classify_for_user → {FeedCategory: [ScoredCandidate]}` all-8-keys, sorted desc, one-per-story; `breaking` filled by SP3 as top-`importance` across topics; source budgets roll into topics.
