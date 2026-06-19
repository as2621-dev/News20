# Phase SP3 — Sub-phase 1 execution report: Canonical taxonomy in the Python pipeline

**Status:** SUCCESS
**Date:** 2026-06-19
**Files touched:** `agents/pipeline/categories.py` (rewritten taxonomy data + docs), `tests/agents/pipeline/test_categories.py` (CREATED)

---

## 1. The new taxonomy

### The 10 `FeedCategory` keys (8 picker roots + 2 source axes)
`ai, geopolitics, business, environment, politics, tech, sport, arts, youtube, x`

- `TOPIC_CATEGORIES` = the 8 roots (`ai, geopolitics, business, environment, politics, tech, sport, arts`)
- `SOURCE_CATEGORIES` = `("youtube", "x")` (unchanged)
- `DEFAULT_CATEGORY` = `"arts"` (replaces old `culture` long-tail catch-all)

### `SLUG_TO_CATEGORY` (full map)
| slug | → root | note |
|---|---|---|
| `ai` | `ai` | identity |
| `geopolitics` | `geopolitics` | identity |
| `business` | `business` | identity |
| `environment` | `environment` | identity |
| `politics` | `politics` | identity |
| `tech` | `tech` | identity |
| `sport` | `sport` | identity |
| `arts` | `arts` | identity |
| `world` | `geopolitics` | legacy world_politics root → nearest split root |
| `climate` | `environment` | was world_politics; environment now first-class |
| `science` | `tech` | no science root; tech is nearest |
| `crypto` | `business` | picker business → "Crypto & fintech" |
| `markets` | `business` | old markets fold collapses into business |
| `entertainment` | `arts` | old culture → arts |
| `lifestyle` | `arts` | long-tail catch-all |
| `wildcard` | `arts` | old culture accent inherited by arts |
| `health` | `tech` | no health root; tech is nearest (was tech_science, now un-folded) |

Subcategory roots (e.g. `sport.cricket`, `business.equities.semis`) are **not** listed individually — they arrive as dotted slugs and resolve via `category_for_slug` on the depth-0 root segment.

### `DEFAULT_FEED_ALLOCATION` (owner-locked, sums to 30)
`ai 4, tech 4, geopolitics 4, business 4, politics 2, environment 2, sport 3, arts 3, youtube 2, x 2` = **30**

### `CATEGORY_FLOOR`
8 topic roots → `3` each; `youtube`/`x` → `0` (follow-gated). Rationale unchanged from prior taxonomy.

### `empty_category_buckets()`
Returns all 10 keys mapped to fresh empty lists, in enum order.

---

## 2. Divergences

- **CREATED `tests/agents/pipeline/test_categories.py`** — the phase listed only `categories.py` under "Files touched", but the DoD requires this pytest file and it did not exist. Documented divergence; required by DoD + Rule 9.

---

## 3. Cross-file breakage — HANDOFF for SP2 (TS twin) and SP4 (scripts)

The taxonomy change is correct and isolated, but **other files hardcode the OLD keys/split** and now break. I did NOT edit them (out of scope for SP1). Precise list:

### Scripts (SP4 owns)
- `scripts/e2e/allocate_test_feeds.py:226` — `return category_for_slug(...) if node else "culture"`. `"culture"` is no longer a valid `FeedCategory`. **Fix:** change fallback to `"arts"` (or `DEFAULT_CATEGORY`).
- `scripts/e2e/profiles.json:61` — `"boost_bucket": "culture"`. Stale bucket name; remap to a new root (likely `arts`).

### Simulation harness (NOT in any listed sub-phase — flag for orchestrator)
- `agents/pipeline/sim/world.py:60-62,83,108` — interest fixtures rooted at `markets` (`markets.crypto`, `markets.stocks`). These now classify to `business` (via the `markets→business` alias), so the sim still runs but its category labels/budget dict at `:83`/`:108` and the default-split tuple at `:349-353` (`world_politics 5, tech_science 5, markets 4, ..., culture 4`) reference retired keys. **This file is not assigned to SP2/SP3/SP4** — orchestrator should assign it (or it will desync the offline ranking sim).

### Tests asserting the OLD taxonomy (will fail until fixtures updated)
Running the downstream pipeline suite after this change: **23 failed, 22 passed**. All 23 failures are fixtures/asserts pinned to the old keys or old 5/5/6/4/3/3/4 split, NOT logic regressions:
- `tests/agents/pipeline/test_demand.py` — uses `CATEGORY_FLOOR["culture"]`, asserts `("culture","_all")`, iterates old `DEFAULT_FEED_ALLOCATION` keys.
- `tests/agents/pipeline/test_produce_caps.py` — `_INTEREST_NODES` expect `business→markets`, `sport→sport`; default split mirror.
- `tests/agents/pipeline/test_feed_assembly.py` — comments + budgets assume `world_politics +1 / culture +1`, total-30 via old split.
- `tests/agents/pipeline/test_ranking_simulation.py`, `tests/agents/pipeline/test_phase5a_live_e2e.py` — assert `category_for_slug(nvidia) == "markets"` (now `business`).

**These test files are not in any sub-phase's "Files touched."** The orchestrator must either (a) assign their fixture updates to a sub-phase, or (b) accept they go red until the twin/scripts land. Flagging per Rule 12 — do not silently leave a red suite.

### Callers that are SAFE (no change needed)
- `agents/pipeline/demand.py:180` — guards on `SLUG_TO_CATEGORY.values()` (now the 8 roots) — correct, no old-key assumption.
- `agents/pipeline/feed_assembly.py:105` `_SOURCE_DOMAIN_TO_CATEGORY` — only youtube/x, unchanged.
- `agents/pipeline/daily_batch.py`, `produce_caps.py`, `stages/ranking.py`, `orchestrator.py` — import symbols by name (`FeedCategory`, `DEFAULT_FEED_ALLOCATION`, `category_for_slug`, `empty_category_buckets`) and do not hardcode old key strings; they pick up the new taxonomy transparently.
- `scripts/run_live_batch.py`, `scripts/produce_source_reels.py` — import `DEFAULT_FEED_ALLOCATION` / `category_for_slug` by name; no hardcoded old keys.

---

## 4. Code-review findings + fixes (Step B/C)

- **`FeedCategory` Literal re-listed in `empty_category_buckets()`** — confirmed all 10 keys present and match the Literal order. ✓
- **Doctest examples** — updated `category_for_slug` examples to new roots (`business.equities.semis → business`, added `ai.interpretability → ai`, `unknown.thing → arts`) and `CategoryAllocation` example to `business`. Verified via `python -m doctest` (6 passed). ✓
- **Module docstring / `CategoryAllocation` docstring** — updated "7 screen categories" → "10", "5 topic" → "8 roots". ✓
- No critical/high issues found in `categories.py` itself. Cross-file breakage (§3) is the load-bearing finding, deferred to SP2/SP4 per scope.

---

## 5. Validation (Step D)

```
$ .venv/bin/ruff check agents/pipeline/categories.py tests/agents/pipeline/test_categories.py
All checks passed!

$ .venv/bin/pytest tests/agents/pipeline/test_categories.py -q
....................                                                     [100%]
20 passed in 0.06s

$ .venv/bin/python -c "from agents.pipeline.categories import ...; print(sum(DEFAULT_FEED_ALLOCATION.values()))"
30

$ .venv/bin/python -m doctest agents/pipeline/categories.py
6 passed and 0 failed.
```

**Validation: PASS.**

---

## 6. Definition of done (Step E)

- `category_for_slug("sport.cricket.india") == "sport"` ✓
- `category_for_slug("ai.interpretability") == "ai"` (NOT tech_science) ✓
- `category_for_slug("politics.x") == "politics"` (NOT world_politics) ✓
- `category_for_slug("environment.climate") == "environment"` ✓
- `sum(DEFAULT_FEED_ALLOCATION.values()) == 30` ✓
- all 8 roots + `youtube` + `x` present in `FeedCategory` / `TOPIC_CATEGORIES` / `empty_category_buckets()` / `DEFAULT_FEED_ALLOCATION` ✓
- locked per-category counts pinned (ai=4, politics=2, youtube=2, …) ✓

**Definition of done: PASS.**

---

## 7. Concerns

1. **Downstream suite is red (23 tests).** Expected — they encode the old taxonomy. SP2/SP4 must update the TS twin + scripts, and SOMEONE must update the Python test fixtures (`test_demand.py`, `test_produce_caps.py`, `test_feed_assembly.py`, `test_ranking_simulation.py`, `test_phase5a_live_e2e.py`). These files are not in any sub-phase's scope — **orchestrator decision needed.**
2. **`agents/pipeline/sim/world.py` is unowned** by the 4 sub-phases yet hardcodes the old split + `markets` fixtures. Will desync the offline sim if left.
3. **TS twin (`DEFAULT_ALLOCATION_SEGMENTS`)** must mirror the exact locked split (Rule 7) — SP2 dependency satisfied: the split is now agreed in Python.
4. Did NOT commit; touched only the 2 permitted files.
