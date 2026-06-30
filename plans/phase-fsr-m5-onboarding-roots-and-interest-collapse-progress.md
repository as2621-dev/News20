# Progress: phase-fsr-m5-onboarding-roots-and-interest-collapse

**Phase file:** plans/phase-fsr-m5-onboarding-roots-and-interest-collapse.md
**Started / shipped:** 2026-06-30
**Branch:** claude/feed-source-revamp-plan-388edf

## Test-env note
- TS = vitest (jsdom) via the repo's `node_modules/.bin/vitest` (ran `npm install`
  first — node_modules was empty). pytest = uv tool env
  `/root/.local/share/uv/tools/pytest/bin/python`. Migration = ephemeral PG16 via
  `pg_virtualenv` (ran live in-sandbox — not skipped).
- Baseline: the full vitest suite has ONE pre-existing failure unrelated to M5 —
  `tests/lib/app/tabBar.test.tsx` ("renders all four tabs … expects no `Thirty` tab").
  Verified it fails identically on the clean tree (without the TopicTree change), so it
  is NOT this phase's regression. Everything else (486 tests) green.

## CRITICAL foundation note
0023 already minted the 8 depth-0 root interest nodes (`interest_slug` == FeedCategory
key) and re-parented depth-1 picker leaves under them. So M5's collapse is a CLEAN
repoint onto rows that already exist (no minting here).

## Migration number used: **0024**
0022 (`source_clusters`) and 0023 (`root_interest_nodes`) are both taken; 0024 is the
next free integer. (The phase file's "0022" placeholder is stale — both 0022/0023 were
consumed by later phases.)

## Sub-phase progress
- [x] 1: Roots-only onboarding picker render (`TopicTree.tsx`) — COMPLETED
- [x] 2: Pure interest-collapse transform (`collapse_profile_rows_to_roots`) — COMPLETED
- [x] 3: ⚠ IRREVERSIBLE idempotent SQL migration 0024 (collapse to roots) — COMPLETED
- [x] 4: Roots-only DoD guard + collapse-rule parity check — COMPLETED

## STATUS: PHASE SHIPPED

### Files added/changed
- `src/components/onboarding/TopicTree.tsx` — REWRITTEN to render ONLY the 8 depth-0
  `PICKER_TREE` roots as a single flat layer (no caret/drill-down/nested branches/
  add-custom). Each root toggles a `topic` `FollowSelection` (`followId` = root slug,
  `label` = root label, `path` = `[label]`) built via `selectionFromNode` — so it
  persists through the existing `persistPickerFollows` topic path, matching the depth-0
  `interests` row by label/slug. `onComplete(store.all())` + always-enabled Done +
  zero-selection skip preserved. (SP1)
- `tests/lib/onboarding/topicTree.rootsOnly.test.tsx` — NEW vitest render test (7 cases):
  exactly the 8 roots render, no deep labels, no caret/expand control, root→Done emits
  the single root follow, zero-selection skip, render==PICKER_TREE roots, and the SP4
  lock "select-all-roots emits ONLY root follows (type topic, path len 1, id ∈ root
  slugs)". (SP1 + SP4)
- `agents/pipeline/categories.py` — ADDED `ProfileInterestRow` (frozen pydantic model)
  + pure `collapse_profile_rows_to_roots(rows)`: deep slug → root via
  `category_for_slug`/`root_interest_slug_for_category`, dedup per `(user, root)` keeping
  MAX `profile_weight` (source carried from the kept row), idempotent fixed-point,
  unknown→`arts`. `category_for_slug`/`SLUG_TO_CATEGORY` untouched. (SP2)
- `tests/agents/pipeline/test_interest_collapse.py` — NEW pytest (14 cases):
  deep→root, dedup-keep-higher-weight (not sum/avg, loser dropped), per-user dedup,
  idempotency, already-root unchanged, unknown→arts, empty→empty, AND the SP4 parity
  class pinning the transform's destination to `category_for_slug` across all 8 roots +
  legacy aliases (`world→geopolitics`, `markets→business`) + unknown fallback. (SP2 + SP4)
- `supabase/migrations/0024_collapse_interest_profile_to_roots.sql` — NEW ⚠ irreversible
  idempotent migration: dedup-losers-first (window over `(user, target_root)` by weight
  desc) THEN repoint survivors to the depth-0 root (`split_part(slug,'.',1)` join). Twin
  invariant + irreversibility/recovery note in the header. (SP3)

### SP3 ephemeral-PG16 apply (ran live — DoD met, not the floor)
Seeded a fixture (3 roots + deep children + a same-root weight pair 1.0/3.0 + an
already-root row + a second user) and applied 0024 via `pg_virtualenv`:
- non-root pointers after apply: **0** (every row at depth-0) ✅
- duplicate `(user, interest)` pairs: **0** (uq_user_interest holds) ✅
- same-root pair collapsed to ONE row, weight **3.0** (MAX), source **signal** (kept
  row's source carried) ✅
- row count **5 → 4** (conserved minus exactly the 1 deduped collision) ✅
- second apply: **zero rows changed** (snapshot diff empty — idempotent) ✅
The collapsed state matched the Python transform's output for the same inputs (parity).

### Phase DoD: PASS
1. vitest picker render (SP1) + picker-emits-only-roots guard (SP4): **7/7 green**.
   Full vitest: 486 pass, 1 pre-existing unrelated failure (tabBar — see Test-env note).
2. `pytest tests/agents/pipeline/test_interest_collapse.py`: **14/14 green**; plus the
   anchor `test_categories.py` still 20/20.
3. Migration 0024 applies cleanly on ephemeral PG16 + idempotent on second apply.
4. `ruff check` + `ruff format --check` on touched py: clean. `biome check` on touched
   TS: clean. `tsc --noEmit`: no errors in any touched file (remotion/* errors are a
   pre-existing separate sub-project, unrelated).

### Slop / CSO self-scan: PASS
- No `console.log`/TODO/FIXME in touched files; biome confirms no unused imports after
  the TopicTree rewrite (dropped `useState`, `buildOutlineTree`, `TreeRow`, etc.).
- Correctness: all 8 picker roots have a `SLUG_TO_CATEGORY` identity entry → every leaf
  slug collapses cleanly to one of the 8 roots (no ambiguous/orphaning leaf — SP3's
  fail-loud condition NOT triggered). Python + SQL produce identical collapse output.
- Security/ops: no RLS change; FKs preserved; migration is one `begin/commit`, fully
  idempotent, with an explicit irreversibility + recovery (PITR of
  `user_interest_profile` only) note.

### LIVE-E2E residual (deferred, NOT run — offline sandbox)
Running 0024 against the PRODUCTION `user_interest_profile` and verifying zero
orphaned/duplicate rows on real profiles (no Supabase creds in this sandbox). The
migration file is authored + ephemeral-PG-verified here; the live backfill via the
production session-pooler apply path is the standing per-migration residual.

### Concerns flagged for M6a (extends the onboarding flow after the picker)
- **M6a owns the source/cluster onboarding UI** (`src/components/sources/*`, the source
  swipe deck) — M5 deliberately did NOT touch it. The picker now hands `onComplete` an
  array of ROOT topic follows only; M6a's step after the picker should assume a
  roots-only interest set (no deep `user_interest_profile` rows from a fresh run).
- **Root-follow persistence path unchanged:** a root selection canonicalizes via
  `findCanonicalInterest` (label/slug match) onto the depth-0 `interests` row. If M6a
  changes `persistPickerFollows`, keep that label/slug → depth-0 match intact, else root
  follows surface as `unpersisted`.
- **The deep `interests` taxonomy STAYS** (still backs DepthMatch + source-tagged
  content). Only `user_interest_profile` pointers + the picker render collapsed. M6a must
  not assume the deep tree is gone.
- **`TopicTree` no longer uses** `buildOutlineTree`/`TreeRow`/`addCustomLeaf` etc. from
  `treeSelection.ts` — those helpers remain (still unit-tested) but are now unused by the
  live picker. Left in place (Rule 3: surgical) in case a future "go deeper" surface
  re-renders the recursive tree; flagged for a possible later dead-code sweep, not a
  blocker.
