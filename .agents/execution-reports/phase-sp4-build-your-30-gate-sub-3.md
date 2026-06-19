# Phase SP4 — Sub-phase 3 execution report

**Mission:** Lock category-order persistence with a backend test. The reel feed's
category order must equal the Build-your-30 order (driven by `allocation_sort_order`).

## What the test asserts

Two tests in `tests/agents/pipeline/test_feed_assembly_order.py`:

1. `test_ordered_categories_from_allocation_sorts_by_sort_order_not_name` — unit test
   directly on `_ordered_categories_from_allocation`. Feeds rows in insertion order
   `[tech, business, sport]` with `allocation_sort_order` `[1, 2, 0]`; asserts the
   returned order is `[sport, tech, business]`.

2. `test_assembled_feed_category_order_follows_allocation_sort_order` — end-to-end via
   `assemble_user_feed`. Each root holds EXACTLY its budgeted count of equal-coverage
   stories (sport 5, tech 5, business 4 = 14 slots) so there is no shortfall and no
   leftover capacity to redistribute — the only thing that can decide cross-category
   order is `allocation_sort_order`. Asserts the emitted `category_run` is
   `["sport"]*5 + ["tech"]*5 + ["business"]*4`.

**Why the B/A/C order is non-alphabetical AND non-insertion (the real lock):**
- Chosen emit order (by `allocation_sort_order`): `sport → tech → business` (EXPECTED).
- Alphabetical: `business → sport → tech` (DIFFERENT — catches a name sort).
- Insertion order of the allocation list: `tech → business → sport` (DIFFERENT —
  catches "ignore sort_order, keep insertion").

A regression to either wrong order fails both assertions.

## Files created / changed

- **Created:** `tests/agents/pipeline/test_feed_assembly_order.py` (only new file).
- **No production change.** The order is already honored by
  `_ordered_categories_from_allocation` (sorts by `(allocation_sort_order,
  allocation_category)`) and the fill loop (~552–575) emits each category's slots at
  its row's sequence position. No gap found, so `feed_assembly.py` is untouched
  (confirmed `git diff` empty for it).

Mocking: same boundary style as the sibling `test_feed_assembly.py` — ranking +
allocation math runs for REAL over in-memory `CanonicalStory`/`StoryInterestTag`; no
supabase, no network (the writer is not exercised here, so no fake client needed).

## Rule-9 perturbation proof

Temporarily changed `_ordered_categories_from_allocation` to sort by
`(row.allocation_category,)` (alphabetical, ignoring `allocation_sort_order`).
Result: BOTH tests FAILED —

```
test_ordered_categories_from_allocation_sorts_by_sort_order_not_name  FAILED
test_assembled_feed_category_order_follows_allocation_sort_order      FAILED
  AssertionError: ... At index 0 diff: 'business' != 'sport'
```

Then restored the original sort key; both tests pass again. The tests cannot pass if
the function ignores `allocation_sort_order`.

## Validation results

- `.venv/bin/pytest tests/agents/pipeline/test_feed_assembly_order.py -q` → **2 passed**.
- `.venv/bin/ruff check tests/agents/pipeline/test_feed_assembly_order.py` → **All checks passed**.
- `git diff agents/pipeline/feed_assembly.py` → empty (no production change, restored).

## Definition of done

**PASS.**
- `pytest tests/agents/pipeline/test_feed_assembly_order.py` green ✓
- The test FAILS if `_ordered_categories_from_allocation` ignores
  `allocation_sort_order` (proven above) ✓

## Concerns

- None blocking. The end-to-end test relies on each category holding exactly its
  budget so Pass 4 (leftover/soft-roll redistribution) never fires and cannot reorder
  across categories — this is intentional and documented in the test, keeping the
  assertion purely about `allocation_sort_order`. No source (`youtube`/`x`) slots are
  used, matching the "category sequence" scope of this lock.
