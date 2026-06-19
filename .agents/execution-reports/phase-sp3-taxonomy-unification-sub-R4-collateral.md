# Phase SP3 — Taxonomy-drift collateral cleanup (sub-R4)

Migrate three user-facing surfaces (+1 Python twin) off the retired folded taxonomy
(`world_politics, tech_science, markets, culture, wildcard, world, podcasts`) onto the
8 canonical picker roots + 2 source axes in `src/lib/feedBuckets.ts` `DESIGN_BUCKETS`.

## STATUS: SUCCESS

## Per-file changes

### 1. `tests/lib/userInterests.test.ts` (the current RED)
- Fixture row `interest_segment_slug: "markets"` (retired slug) → `"business"`; row id
  `i-mkt`→`i-biz`, label `"Markets"`→`"Business"`, assertions/comments updated.
- Expected accent **unchanged** (`#22C55E`): `business` carries the same locked green
  that `markets` folded into (`interests.ts SEGMENT_ACCENT_HEX.business = #22C55E`).
- Intent preserved (Rule 9): still asserts (a) locked green for the root pick, cyan for
  the tech leaf, (b) depth-0 root sorts ahead of the depth-2 leaf.
- **Result: green.**

### 2. `src/lib/sourceSwipeData.ts` — `ACCENT_BY_TOPIC`
Aligned every accent to `DESIGN_BUCKETS` exactly (resolved the SP2-flagged conflict):
| key | was | now (DESIGN_BUCKETS) |
|---|---|---|
| ai | `#22D3EE` (collided with tech) | `#3B82F6` |
| geopolitics | `#A78BFA` (wrong — purple) | `#EF4444` |
| politics | `#A78BFA` | `#A78BFA` (unchanged) |
| tech | `#22D3EE` | `#22D3EE` |
| business / environment / sport / arts | already correct | unchanged |
- Reordered to canonical DESIGN_BUCKETS order. `DEFAULT_ACCENT = #3B82F6` (untagged
  fallback) left as-is — now matches `ai`/brand-primary, intended.
- `SOURCE_SWIPE_PLATFORMS` still lists `podcast`/"Podcasts": that is a real
  `ContentSourceType` **enum** (a source axis that folds to youtube per
  `feedBuckets.SOURCE_TYPE_TO_DESIGN_BUCKET`), NOT a retired taxonomy key — retained (Rule 3).

### 3. `src/lib/detailTemplates.ts` + twin `agents/pipeline/detail_templates.py`
**Approach: additive alias map, NOT a key rename** (see Conflict below). Added
`DETAIL_CATEGORY_ALIASES` (TS export + Python twin) folding the SP3 roots onto the
existing template-layout keys, and routed the resolvers through it:

Detail-template key remap (SP3 root → borrowed template, content unchanged):
| SP3 root | template | rationale |
|---|---|---|
| geopolitics | `world` | split `world_politics` keeps the partisan-coverage/world-stakes layout |
| politics | `world` | no bespoke layout; nearest is world/stakes |
| environment | `world` | no bespoke layout; nearest is world/stakes |
| business | `markets` | old `markets` fold collapses into business |
| arts | `culture` | old `culture` catch-all renames to arts |
| ai | `tech` | no bespoke layout; rides the tech why-it-matters/the-concept layout |
| tech / sport / youtube / x | identity | already canonical keys |
| podcasts | `youtube` (source layout) | retained enum; no SP3 axis of its own |

- **TS:** `templateForCategory` now folds an SP3-root `detailCategory` via the alias map,
  else looks up the canonical key, else falls back to Culture. So a `geopolitics` /
  `business` / `ai` story renders a meaningful template instead of the default.
- **Python:** merged the new roots into `_FEED_CATEGORY_TO_DETAIL` (alongside the
  retained legacy `world_politics`/etc.) and into `_SEGMENT_TO_DETAIL` (the segment
  backfill added `ai/business/environment/politics/arts` per sub-3), so both live call
  sites (`persist.py`, `orchestrator.py` → `detail_category_for_segment`) resolve a
  template for the new segment values instead of falling to culture. Legacy-input
  outputs are unchanged (`geopolitics`→`world` etc.), so the pinned tests stay green.
- Twins kept in sync (`DETAIL_CATEGORY_ALIASES` identical in both).

## CONFLICT SURFACED (Rule 7/12) — read this

The task said "remap the category KEYS to the 8 new roots; only the KEYS change." A literal
key-rename was **not safe** here and I did not do it, because:
1. Two test files pin the OLD key set and are **outside my allowed file list**:
   `tests/lib/detailTemplates.test.ts` and `tests/agents/pipeline/test_detail_templates.py`
   both assert `len(DETAIL_TEMPLATES) == 8` and `set == {world,markets,tech,sport,culture,youtube,podcasts,x}`.
   Renaming keys turns both RED with no way for me to fix them.
2. Every **live producer** of `story_detail_category` is outside scope and still emits
   the legacy keys: `src/lib/feed/fixtureFeed.ts` `SEGMENT_DETAIL_CATEGORY`
   (`business→markets, arts→culture, ai→tech, geo/politics/environment→world`) and the
   Python resolvers' historical outputs. A rename would silently mis-resolve every
   produced story to the Culture default.

So I kept the template **layout** keys canonical and made the lookup accept BOTH
taxonomies (alias fold). This fully satisfies the task's actual product requirement
("a `geopolitics` story resolves a template", "don't leave a root unmapped") and
preserves content, **without** breaking the two out-of-scope pinned tests or any live
consumer. A future sweep (when the producers + both test files can move together) can
finish the literal rename; flagged for SP4 / orchestrator.

## Validation (all green)
- `npx vitest run tests/lib/userInterests.test.ts tests/lib/detailTemplates.test.ts` → **17 passed** (userInterests now green; detail parity still green).
- `npx tsc --noEmit` → **exit 0** (TS edits typecheck).
- `pytest tests/agents/pipeline/test_detail_templates.py test_detail_enrichment.py` → **43 passed**.
- `ruff check agents/pipeline/detail_templates.py` → clean.
- Residual-token grep on the 3 in-scope files: `sourceSwipeData.ts` and
  `userInterests.test.ts` have **zero** retained tokens. `detailTemplates.ts` hits are
  all the legitimately-retained `DetailCategory` enum strings + alias-doc references
  (`markets`/`culture`/`podcasts` are the canonical template keys, not stray taxonomy) —
  expected per the task's enum carve-out.

## Concerns
1. **Detail-template literal rename deferred** (see Conflict). The alias fold is the
   non-regressing half; the full rename needs `fixtureFeed.SEGMENT_DETAIL_CATEGORY` +
   both pinned test files to move in one commit. Flag for SP4.
2. `interests.ts:213` JSDoc still says "Markets pick" (stale comment, not behavior,
   out of scope) — leave for the doc sweep.
3. Python `DETAIL_CATEGORY_ALIASES` is exported-but-unused by the resolvers (they use the
   merged dicts); it exists as the documented twin-parity surface of the TS export.
   Ruff passes (module-level export, not a local). Intentional.
