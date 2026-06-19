# Phase SP3 — Sub-phase 4 execution report: Finish breaking removal in the 3 lagging scripts + cross-surface parity

**Status:** SUCCESS
**Date:** 2026-06-19
**Files touched (ONLY these 4):**
- `scripts/produce_source_reels.py`
- `scripts/e2e/allocate_test_feeds.py`
- `scripts/e2e/drive-profile.ts`
- `scripts/e2e/profiles.json`

---

## 1. Per-script: breaking-ALLOCATION removed / velocity KEPT

### `scripts/produce_source_reels.py`
- **Removed (allocation):** the `_category_of_existing_row` branch
  `if row.get("feed_slot_kind") == "breaking": return "breaking"` — the only allocation/slot
  read of `breaking` in the file. Existing rows now bucket purely as topic (interest) rows.
- **Removed (docs):** three docstring mentions of "breaking + topic reels" / "A breaking row
  keeps `breaking`" reworded to topic-only.
- **Velocity KEPT:** none present in this file (no `story_is_breaking` here).
- **SP2 logic untouched:** the source-row carry-forward (`carried_source_rows`), the
  fail-safe write guard (`feed_rebuild_aborted` on source-reel regression), the
  `_count_existing_source_rows` empty-produce routing, and the backup-path collision logic
  are all unchanged.

### `scripts/e2e/allocate_test_feeds.py`
- **Removed (allocation):** the `_slot_category` branch
  `if slot.feed_slot_kind == "breaking": return "breaking"`. Docstring "source/breaking tier"
  → "source tier".
- **Taxonomy drift fix:** fallback `... if node else "culture"` → `... if node else DEFAULT_CATEGORY`
  (imported `DEFAULT_CATEGORY` from `agents.pipeline.categories`, which is `"arts"`). Importing
  the canonical default — not re-hardcoding `"arts"` — prevents future drift (per mission's
  preferred approach).
- **Velocity KEPT:** none present.
- **SP2 logic untouched:** `_load_existing_source_rows` + `_replace_source_slots`
  source-aware re-placement (SP2 SP3) and the `source_rows_replaced` logging are unchanged.
  The `SLOT_KIND_SOURCE` source branch in `_slot_category` is retained.

### `scripts/e2e/drive-profile.ts`
- **Removed (allocation):** replaced the entire stale `BUCKET_DISPLAY_NAMES` map
  (`breaking`/`world`/`markets`/`tech: "Tech & Science"`/`culture`/`podcasts`) with the 8
  picker roots + 2 source axes, labels **verbatim equal** to `DESIGN_BUCKETS[id].name`
  (e.g. "Geopolitics", "Tech", "Arts"). No `breaking` entry. This map is read by
  `stepBuild30` to resolve `boost_bucket` → the "More <Label>" aria-label stepper.
- **Velocity KEPT:** N/A (no velocity concept in this file).

### `scripts/e2e/profiles.json`
- **Taxonomy drift fix:** profile-d `"boost_bucket": "culture"` → `"arts"`. The other two
  `boost_bucket` values (`tech`, `sport`) are already valid roots — left as-is.

---

## 2. `grep -rn "breaking" scripts/` — every hit classified

```
scripts/regenerate_feed_content.py:268: # ...so no breaking signal — the   → VELOCITY (out of scope; comment for is_breaking=False)
scripts/regenerate_feed_content.py:270: detail_category_for_segment(segment_slug, is_breaking=False)  → VELOCITY (Detail-template, not allocation; out of scope)
scripts/e2e/drive-profile.ts:81: // ...No fold, no `breaking`.  → DOCUMENTATION (my own comment noting the removal; not allocation)
```
**No allocation hits remain.** `regenerate_feed_content.py` is NOT in this sub-phase's scope;
its `is_breaking=False` is the Detail-template velocity signal (correctly preserved). The
`drive-profile.ts:81` hit is a comment I added documenting the removal.

---

## 3. Per-root label-parity table (scripted assertion, PASS)

`DESIGN_BUCKETS[id].name` (onboarding / Build-your-30 source of truth) vs
`drive-profile.ts` `BUCKET_DISPLAY_NAMES`:

| root | DESIGN_BUCKETS | drive-profile | match |
|---|---|---|---|
| ai | AI | AI | OK |
| geopolitics | Geopolitics | Geopolitics | OK |
| business | Business | Business | OK |
| environment | Environment | Environment | OK |
| politics | Politics | Politics | OK |
| tech | Tech | Tech | OK |
| sport | Sport | Sport | OK |
| arts | Arts | Arts | OK |
| youtube | YouTube | YouTube | OK |
| x | X | X | OK |

`stale driver ids: none`. **PARITY: PASS** (scripted node assertion, exit 0).

Note on the Python scripts' category labels: `allocate_test_feeds.py` / `produce_source_reels.py`
derive a slot's topic label via `category_for_slug` (SP1's canonical 8-root map) and the
`"source"` slot tier — both now resolve into the same 8 roots (+ `source`), so a given root's
backend category string equals its `DESIGN_BUCKETS` key. The user-visible label is rendered
frontend-side from `DESIGN_BUCKETS`, keyed by that root — single source of truth, no drift.

---

## 4. Validation

| Check | Result |
|---|---|
| `grep -rn "breaking" scripts/` | only velocity + 1 doc-comment hit (classified above) — PASS |
| `.venv/bin/ruff check produce_source_reels.py allocate_test_feeds.py` | All checks passed! |
| `python -c "json.load(open('scripts/e2e/profiles.json'))"` | profiles.json OK (valid JSON) |
| `npx tsc --noEmit` | no errors for drive-profile.ts / feedBuckets.ts |
| label-parity assertion (10 roots) | PARITY: PASS |
| `pytest tests/scripts/test_produce_source_reels_rebuild.py -q` | **3 passed** (SP2 regression intact) |

SP2's regression test (`_rebuild_feed` source-reel carry-forward) still passes — confirms the
breaking-branch removal did not disturb the source-row preservation logic. The test uses only
`source` + `interest` slot kinds (never `breaking`), so the removed branch was dead for it.

---

## 5. Divergences

- **None of substance.** Used `DEFAULT_CATEGORY` import instead of a literal `"arts"` in
  `allocate_test_feeds.py` (the mission's *preferred* anti-drift option).
- The `git diff --stat` shows ~16 OTHER files changed (categories.py, feedBuckets.ts, sim/world.py,
  tests, fixtureFeed.ts, etc.). **These are sibling SP1/SP2/SP3 agents' work, NOT mine** — I touched
  only the 4 permitted files. Flagging for transparency (Rule 12), not as my changes.

---

## 6. Definition of done (phase file SP4 DoD)

- `grep -rn "breaking" scripts/` returns only non-allocation hits (velocity / doc-comment): **PASS**
- Same category label string for a given root across surfaces (onboarding `DESIGN_BUCKETS` ==
  Build-your-30 == `drive-profile.ts`), proven by the scripted 10-root parity table: **PASS**

**Definition of done: PASS.**

---

## 7. Concerns

1. **End-to-end browser smoke not run.** The DoD's "seeded profile through onboarding →
   Build-your-30 → reel" live smoke requires a running dev server + seeded users + Chrome/CDP
   (the full `/go-live-check` rig). I proved parity via a deterministic scripted label assertion
   instead (the DoD allows "manual or scripted"). A future live `drive-profile.ts --profile
   profile-d-arts-mixed` run will exercise the `boost_bucket: "arts"` → "More Arts" stepper path
   end-to-end; worth a one-off confirmation when the rig is up.
2. **`regenerate_feed_content.py` velocity** (`is_breaking=False`) is correctly preserved but is
   outside this sub-phase — no action needed; noted so it isn't mistaken for a missed allocation hit.
```
