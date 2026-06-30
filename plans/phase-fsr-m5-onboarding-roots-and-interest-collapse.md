# Phase FSR-M5: Top-level-category onboarding + interest collapse

**Milestone:** M5 — Top-level-category onboarding + interest collapse (PRD `plans/prd.md` `## Milestones`)
**Status:** Not started
**Estimated effort:** M

## Goal
The onboarding interest picker shows ROOT categories only (1 layer, no drill-down), and an idempotent transform collapses every existing deep `user_interest_profile` row to its root `interests` row via `category_for_slug` — with no duplicate `(profile_user_id, profile_interest_id)` rows and no orphans.

---

## Context the sub-agent must not re-derive (verified 2026-06-30)

- **The live picker component is `src/components/onboarding/TopicTree.tsx`**, NOT `InterestChips.tsx` (archived/superseded). `OnboardingFlow.tsx` step `picker` renders `<TopicTree onComplete=…>`. `TopicTree` renders the **full recursive** `PICKER_TREE` (8 roots → branches → leaves) via `buildOutlineTree(PICKER_TREE)` and recursive `TreeRow`. M5 must make it render **roots only** (the 8 depth-0 categories, `level === 0`), with no expand/drill-down.
- **`PICKER_TREE`** comes from `src/lib/followSets.ts` (`liftPickerTree(RAW_PICKER_DATA)`); each root is `level === 0`. The seed tree itself (`src/lib/pickerSeedTree.ts`) is LIFTED-VERBATIM prototype data — **do NOT edit it**; M5 changes what the picker *renders*, not the seed data.
- **`user_interest_profile.profile_interest_id` is a UUID FK → `interests.interest_id`** (NOT a slug). The collapse target is the **depth-0 `interests` row** for each deep row's root. Bridge: `interests.interest_slug` is a dotted path (`'sport.soccer.epl'`); its root segment (`split_part(slug,'.',1)` / `slug.split('.',1)[0]`) is the depth-0 row's slug (`'sport'`), and `category_for_slug(slug)` returns that root `FeedCategory`. Depth-0 rows have `parent_interest_id IS NULL` and `interest_slug = '<root>'`.
- **`agents/pipeline/categories.py`** is the pure source of truth: `SLUG_TO_CATEGORY` + `category_for_slug(slug) -> FeedCategory`. The 8 roots: `ai, geopolitics, business, environment, politics, tech, sport, arts`. The Python collapse helper MUST import/use `category_for_slug` (Decision: implement E1/collapse against the existing map, do not fork — PRD Rule 7).
- **Dedup-on-conflict:** collapsing two deep rows of the same user to the same root (e.g. `sport.soccer` + `sport.cricket` → `sport`) collides on `uq_user_interest (profile_user_id, profile_interest_id)`. Rule (PRD Implementation Decisions): **keep the higher `profile_weight`**; `profile_source` unchanged. Idempotent: a row already pointing at its root is a no-op on re-run.
- **Test infra:** TS = **vitest** (jsdom), tests under `tests/lib/**/*.test.{ts,tsx}` (vitest include glob is `tests/lib/**` + `tests/seed/**` only). Python = pytest under `tests/agents/pipeline/`; existing `tests/agents/pipeline/test_categories.py` is the anchor for category tests. Migrations: `psql` + `pg_virtualenv` (Postgres 16) are present, so the migration applies against ephemeral Postgres (preferred DoD), with a SQL parse/structural check as the floor.
- **LIVE-DB residual:** running the collapse against the production `user_interest_profile` is **LIVE-E2E (deferred)** — no DB creds in this sandbox. The migration file is authored + verified here (ephemeral-PG apply); the live backfill run is the deferred residual.

---

## Sub-phases

### Sub-phase 1: Render the onboarding interest picker as roots-only (1 layer)
- **Files touched:** `src/components/onboarding/TopicTree.tsx` (render only). Do NOT touch `pickerSeedTree.ts`, `followSets.ts`, `treeSelection.ts`, or `OnboardingFlow.tsx`.
- **What ships:** The onboarding interest picker shows the 8 depth-0 category rows only — no caret/expand, no nested branches/leaves, no per-branch Add-custom. Tapping a root toggles selecting that root category. `onComplete(store.all())` still fires (each selected root is a topic follow for the root node); Done stays always-enabled; zero selections remains a valid skip.
- **Definition of done:** A vitest render test at `tests/lib/onboarding/topicTree.rootsOnly.test.tsx`: renders `<TopicTree>`, asserts (a) exactly the 8 root labels (`AI, Geopolitics, Business, Environment, Politics, Tech, Sport, Arts`) render, (b) NO descendant label renders (e.g. `Soccer`, `NFL`, `Foundation models & LLMs`, `Nvidia` are absent — assert by querying for a representative deep label and expecting null), (c) no expand/caret control is present (no element toggles children into view), (d) tapping a root then Done calls `onComplete` with exactly that root's selection. The test must FAIL if drill-down is reintroduced (Rule 9 — it asserts the *absence* of depth, the product rule).
- **Dependencies:** none.

### Sub-phase 2: Pure interest-collapse transform (`category_for_slug`-based, with dedup rule)
- **Files touched:** `agents/pipeline/categories.py` (ADD a pure helper, e.g. `collapse_profile_rows_to_roots(rows) -> list[...]`; do not alter `category_for_slug`/`SLUG_TO_CATEGORY`). New test `tests/agents/pipeline/test_interest_collapse.py`.
- **What ships:** A pure, idempotent function that takes the collapse-relevant fields of a user's `user_interest_profile` rows (each carrying the row's `interest_slug` + `profile_weight` + identity) and returns the collapsed set: each deep slug mapped to its root via `category_for_slug`, deduped per `(user, root)` keeping the **higher `profile_weight`**, `profile_source` carried unchanged. No DB, no clock, no network (mirrors the rest of `categories.py`).
- **Definition of done:** `pytest tests/agents/pipeline/test_interest_collapse.py` green, asserting the WHY (Rule 9): (1) deep→root — `sport.soccer.epl` and `business.equities.semis` collapse to `sport`/`business`; (2) **dedup on conflict** — two deep rows of one user collapsing to the same root yield ONE row with the **max** weight (assert the lower-weight row is dropped, not summed/averaged); (3) **idempotency** — feeding the function its own output is a fixed point (no further change, no new dupes); (4) an already-root row (`sport`) is unchanged; (5) unknown-root slug falls back per `category_for_slug` (→ `arts`) without crashing. Tests run against in-memory fixtures (no DB).
- **Dependencies:** none (independent of Sub-phase 1 and 3 — different file region).

### Sub-phase 3: Idempotent SQL migration that collapses `user_interest_profile` to roots
- **Files touched:** NEW `supabase/migrations/0022_collapse_interest_profile_to_roots.sql`. (Number is provisional — pick the next free integer at run time; current max is 0021.)
- **What ships:** A forward-only, **idempotent** SQL migration that, for every `user_interest_profile` row whose `profile_interest_id` is NOT a depth-0 `interests` row, repoints it to the depth-0 root `interests` row for its `interest_slug`'s root segment, then resolves the resulting unique-constraint collisions by keeping the **higher-weight** row and deleting the rest (`profile_source` preserved). Re-running matches zero rows (WHERE guards on `depth_level > 0` / non-root target), mirroring the idempotent style of `0021_taxonomy_8_roots_backfill.sql`. The root-of-slug lookup uses `split_part(interest_slug,'.',1)` joined to the depth-0 row with that slug — equivalent to `category_for_slug` in SQL (a header comment must state this twin invariant, Rule 7).
- **Definition of done:** ⚠ irreversible (data migration — see Risk lens). Apply the migration against an **ephemeral Postgres** (`pg_virtualenv` / a throwaway `initdb` cluster) over a minimal fixture: seed `interests` (a few depth-0 roots + deep children, slugs as in the schema) + `user_interest_profile` with deep rows incl. a same-root pair at differing weights, apply 0022, then assert: every remaining profile row points at a depth-0 interest; the same-root pair collapsed to ONE row with the MAX weight; row count is conserved minus exactly the deduped collisions; **a second apply changes zero rows** (idempotency). If ephemeral-PG apply is infeasible in the run environment, the floor DoD is a SQL parse/structural check (statements parse; idempotency guards present) — and the apply is recorded as part of the LIVE-E2E residual. The live production backfill run is marked **LIVE-E2E (deferred)**.
- **Dependencies:** Sub-phase 2 (the SQL must encode the SAME deep→root + keep-higher-weight rule the Python transform defines and tests; author the rule once, mirror it — do not let the two diverge).

### Sub-phase 4: Roots-only picker DoD guard + collapse-rule parity check
- **Files touched:** `tests/lib/onboarding/topicTree.rootsOnly.test.tsx` (extend), `tests/agents/pipeline/test_interest_collapse.py` (extend) — no production code. A short note appended to the execution report cross-linking the SQL twin.
- **What ships:** The phase-locking checks that prove M5's two halves agree and stay agreed: (a) a picker test asserting the roots-only picker writes ONLY root-node follows (so a fresh onboarding never *creates* a deep `user_interest_profile` row — the collapse stays a one-time historical fixup, not a recurring need); (b) a parity assertion that the Python transform's deep→root mapping equals `category_for_slug` for a representative slug set spanning all 8 roots + a legacy alias (`world→geopolitics`, `markets→business`) + the unknown→`arts` fallback — the exact mapping the SQL migration must mirror.
- **Definition of done:** Both suites green; the picker test fails if a future change lets the roots-only picker emit a sub-root follow; the parity test fails if the transform's mapping drifts from `category_for_slug`. This sub-phase LOCKS the contract (roots-only in, roots-only stored) that Sub-phases 1–3 each implement a slice of.
- **Dependencies:** Sub-phase 1 (picker behavior), Sub-phase 2 (transform), Sub-phase 3 (SQL twin to assert parity against).

---

## Phase-level definition of done
`/run-phase` validates, before the single commit:
1. `npm test` (vitest) green — the roots-only picker render test (SP1) and the picker-emits-only-roots guard (SP4) pass.
2. `pytest tests/agents/pipeline/test_interest_collapse.py` green — deep→root, dedup-keep-higher-weight, idempotency, root-unchanged, fallback, and the `category_for_slug` parity check (SP2 + SP4).
3. `supabase/migrations/0022_*.sql` applies cleanly against an ephemeral Postgres over the fixture and is idempotent on a second apply (SP3); OR (fallback) parses + passes the structural/idempotency-guard check, with the apply logged as a LIVE-E2E residual.
4. `ruff check` / `ruff format` (Python) and `next lint` (TS) pass on touched files.

**LIVE-E2E (deferred):** running 0022 against the production `user_interest_profile` and verifying no orphaned/duplicate rows on real profiles (no DB creds in this sandbox).

## Out of scope
- The **source/cluster onboarding UI** and the **priority feed mix** (that is M6 — `feed_assembly`, `user_content_sources`, cluster bulk-select). M5 touches ONLY the interest (category) picker render + the collapse transform/migration.
- Editing `pickerSeedTree.ts` / `followSets.ts` / `treeSelection.ts` (the seed data + selection engine stay as-is; M5 changes only what `TopicTree` renders).
- News fetching / `interest_search_query` rekeying (M2/M4), importance (M3), the `interests` tree itself, `user_entity_follows`, and `user_interest_traits` (deprecated).
- Un-migrating or deleting any `interests` rows. The deep `interests` taxonomy STAYS (it still backs `DepthMatch` scoring and source-tagged content per PRD Decision #2); only `user_interest_profile` *pointers* collapse.

## Open questions
- **Migration number:** 0022 is provisional (next free integer ≥ 0022 at run time).
- **Roots-only follow shape:** confirm a selected root in `TopicTree` resolves to the depth-0 `interests` node via the existing `persistPickerFollows` topic-canonicalization (label/slug match on the root) — if the root label doesn't canonicalize cleanly, SP1 must ensure the root selection carries the root slug so persistence isn't surfaced as `unpersisted`. (Non-blocking; verify during SP1.)
- **Weight on collapse-dedup:** PRD says "keep the higher weight." Confirmed as MAX (not sum) — surfaced here so a reviewer can veto if summing was intended.

## Self-critique

**Product lens (CMO hat — brief: `documents/feed-source-revamp-plan.md` / PRD):**
PASS. M5's two MVP capabilities trace to sub-phases: User Story 1 (pick top-level interests only) → SP1 (roots-only render) + SP4 (picker emits only roots). User Story 2 (existing deep selections collapse, no broken profile) → SP2 (pure transform) + SP3 (migration). No scope creep: the source/cluster picker and feed mix are explicitly deferred to M6 and fenced in Out-of-scope. The milestone's own riskiest item is correctness of the idempotent collapse (data-mutating, irreversible), and it is tested *first-class* in SP2/SP3, not deferred. M5's 90-day-metric contribution (fast onboarding + no broken migrated profiles) is measurable at phase end via the offline tests; the live-profile run is the stated residual.

**Engineering lens (CTO hat — PRD Technical Foundation + reference docs):**
PASS, two findings addressed. (1) Risk of forking the slug→root map: resolved by mandating SP2 import `category_for_slug` and SP3 mirror it in SQL with an explicit twin-invariant comment + SP4 parity test (PRD Rule 7 / Decision #2). (2) Every DoD is fresh-context-verifiable: SP1 = "8 roots render, deep labels absent, onComplete payload"; SP2/SP4 = named pytest assertions; SP3 = "apply to ephemeral PG, assert root-only + max-weight + idempotent re-apply" — all concrete, none rely on "works end-to-end." SP4 (the lock-in sub-phase) deliberately cements only the *contract* (roots in → roots stored) that SP1–3 already commit to, not an early API shape, so it doesn't paint anything into a corner. No two sub-phases are secretly the same: SP1 is UI render, SP2 is a Python pure fn, SP3 is SQL, SP4 is cross-cutting guards — four genuinely distinct units (not padding). Stack: all files are within the fixed stack (Next/React TS UI, Python pipeline, Supabase SQL migration); nothing implies a new service.

**Risk lens (what blows up):**
PASS with explicit flags. *File-boundary conflicts:* SP1 and SP4 both touch `tests/lib/onboarding/topicTree.rootsOnly.test.tsx` (SP4 extends SP1's file) and SP2/SP4 both touch `test_interest_collapse.py` — dependency is marked (SP4 depends on SP1+SP2+SP3); no two sub-phases edit the same PRODUCTION file (SP1=TopicTree.tsx, SP2=categories.py, SP3=new .sql). *Test coverage:* every sub-phase's DoD is a test, not a manual smoke (the picker DoD is a render test per the offline-verifiability override, not manual). *Reversibility:* SP3 is ⚠ **irreversible** (a data migration that deletes deduped rows) — flagged so `/run-phase` proceeds with care; mitigated by ephemeral-PG apply + idempotency guard before any live run, and the live run is itself deferred. *Painting into a corner:* simulate SP1→2→3→4 — SP1 makes the picker roots-only (so no NEW deep rows), SP2 defines the rule, SP3 applies it to history, SP4 locks both ends; SP4 still works given SP1–3's state (it asserts their outputs). Order is sound.

**Irreversible sub-phases:** Sub-phase 3 (data migration `0022_*.sql` — repoints + deletes deduped `user_interest_profile` rows). Verified offline against ephemeral Postgres before the live run; the live production backfill is LIVE-E2E (deferred).
