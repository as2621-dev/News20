# Phase SP2 — Sub-phase 1 execution report

**Sub-phase:** Preserve + merge existing source rows in `_rebuild_feed`
**Status:** SUCCESS

## What I implemented
The bug: `_rebuild_feed` dropped existing `feed_slot_kind == "source"` rows
(`if row.get("feed_slot_kind") == "source": continue`) and re-placed source
slots ONLY from this run's `produced_yt`/`produced_x`. An empty/short produce
run therefore evicted prior source reels and back-filled their slots with topic
reels.

The fix (surgical, single function):

1. **Carry-forward bucketing** (was the drop): existing source rows are now
   appended to a new `carried_source_rows: list[dict[str, Any]]` (preserving
   `feed_position` order from the `.order("feed_position")` query) instead of
   being `continue`-skipped. Topic-row bucketing into `existing_by_category` is
   unchanged.

2. **Structured log**: when `carried_source_rows` is non-empty, emits
   `logger.info("source_rows_carried_forward", carried_source_row_count=...,
   feed_date=...)`.

3. **Merge in the source-slot fill**: for each `youtube`/`x` allocation row,
   this run's freshly produced ids (`taken`) are appended first (precedence),
   then carried-forward source ids fill any **remaining** budget. Accounting
   uses a per-category `filled_in_category` counter that increments only on an
   actually-appended (post-dedup) row, so the carried fill respects the true
   remaining budget and never exceeds it.

Dedup is entirely via the existing `used_story_ids` set keyed on
`feed_story_id`. A produced id that collides with a carried id (the `sp3-<id>`
reuse case) is added once as the produced row; the carried duplicate is skipped.
The carried pool is shared across `youtube`/`x` (drawn in order); a carried id
consumed by `youtube` is in `used_story_ids` so `x` cannot re-consume it.

### Key code change (the bucketing)
```python
existing_by_category: dict[str, list[dict[str, Any]]] = {}
carried_source_rows: list[dict[str, Any]] = []
for row in existing:
    if row.get("feed_slot_kind") == "source":
        carried_source_rows.append(row)
        continue
    category = _category_of_existing_row(row, slug_by_id)
    existing_by_category.setdefault(category, []).append(row)
```

### Key code change (the source-slot fill)
```python
carried_source_ids = [r["feed_story_id"] for r in carried_source_rows]
...
if category in ("youtube", "x"):
    filled_in_category = 0
    taken = source_by_category.get(category, [])[:budget]
    for story_id in taken:
        if story_id in used_story_ids:
            continue
        new_rows.append(_source_row(story_id)); used_story_ids.add(story_id)
        filled_in_category += 1
    for story_id in carried_source_ids:
        if filled_in_category >= budget:
            break
        if story_id in used_story_ids:
            continue
        new_rows.append(_source_row(story_id)); used_story_ids.add(story_id)
        filled_in_category += 1
```

## Files modified
- `scripts/produce_source_reels.py` (only this file)

## Divergences from the plan
None material. The plan said "keyed by `feed_story_id`" — implemented as a
shared ordered pool deduped on `feed_story_id`, because a `daily_feeds` source
row records only `feed_slot_kind="source"` (NOT whether it was a youtube or x
slot). So carried source rows are NOT category-tagged and are drawn into
whichever youtube/x budget has room, in `feed_position` order. This satisfies
the DoD (prior source reels survive at source positions) without an extra
outlet-domain lookup. Documented in an inline comment.

## Code-review findings + fixes
- First edit attempt produced a tangled budget-accounting block (referenced an
  undefined helper and a stray var). **Fixed before validation** by rewriting to
  the clean `filled_in_category` counter. (self-caught; severity: would-have-been
  critical — syntax/logic — but never left the working tree in that state past
  the immediate follow-up edit.)
- Reviewed for off-by-one on budget: `filled_in_category >= budget` breaks
  before exceeding; counter only increments on appended rows. OK.
- Reviewed for key collision between carried and produced: both deduped via
  `used_story_ids`. OK.
- Reviewed topic backfill (`leftover` loop, unchanged): also guards on
  `used_story_ids`, so a carried source id can never be re-added as a topic row.
  OK.

No outstanding critical/high/medium/low.

## Validation results
- `.venv/bin/python -c "import ast; ast.parse(...)"` → `SYNTAX OK`
- `.venv/bin/ruff check scripts/produce_source_reels.py` → `All checks passed!`
- No existing pytest suite imports this module (sub-phase 4 owns the new test).
- **Throwaway inline DoD check** (mocked supabase, EMPTY produce lists, prior
  `sp3-yt1` + `sp3-x1` source rows): rebuilt feed contained both source reels at
  source positions `[(1,'sp3-yt1'),(2,'sp3-x1')]`, none replaced by topic reels,
  `source_rows_carried_forward` logged with `carried_source_row_count=2`.
- **Throwaway precedence check** (produced re-produces `sp3-yt1`, fresh
  `sp3-xNEW`, prior `sp3-xOLD`): result `['sp3-yt1','sp3-xNEW']` — no
  duplication, fresh reel wins the x slot, carried `sp3-xOLD` correctly NOT
  added (x budget=1 already full).

## Definition of done: PASS
Verified by the empty-produce inline check above: with produced lists EMPTY and
prior source rows present, the rebuilt `new_rows` still contains those prior
source rows at their source positions, none replaced by topic reels. Carry-
forward path confirmed correct; DoD is satisfiable by sub-phase 4's formal test.

## Concerns for the orchestrator (esp. SP2 / SP4)
- **Data shape SP4 must mock:** `_rebuild_feed(supabase, target, produced_yt,
  produced_x)`. The supabase mock must serve three tables off a fluent
  `.select().eq().order().execute().data` chain returning lists of dicts:
  - `daily_feeds`: rows with keys `feed_story_id, feed_score,
    feed_matched_interest_id, feed_slot_kind, feed_position` (ordered by
    `feed_position`). Source rows carry `feed_slot_kind="source"`.
  - `user_feed_allocation`: `allocation_category, allocation_slot_count,
    allocation_sort_order` (categories `youtube`/`x` drive source slots).
  - `interests`: `interest_id, interest_slug`.
  The mock also needs `daily_feeds.delete().eq().eq().execute()` and
  `daily_feeds.insert(rows).execute()` (capture `insert` rows to assert).
- **Source rows are NOT category-tagged in `daily_feeds`.** SP4 should assert on
  total source-reel survival up to the combined youtube+x budget, NOT on which
  carried row landed in which category slot (the pool is shared, position-
  ordered). If prior source rows exceed total youtube+x budget, the overflow is
  trimmed (matches pre-existing budget-cap behavior).
- **`_main` still early-returns `1` at lines ~217–219 BEFORE calling
  `_rebuild_feed` when both produced lists are empty.** So in the live path a
  fully-empty produce never reaches the rebuild. SP2 owns the fail-safe-write /
  abort-vs-preserve decision around that delete+insert; the carry-forward logic
  itself is correct for any caller that does reach `_rebuild_feed` with empty
  lists (verified). SP2 should decide whether to route empty-produce through the
  rebuild (to refill from carried rows) or keep the early return.
- The backup write at line ~249 still uses the same-day-clobbering
  `/tmp/ash_feed_backup_<date>.json` path — untouched here, owned by SP2.
