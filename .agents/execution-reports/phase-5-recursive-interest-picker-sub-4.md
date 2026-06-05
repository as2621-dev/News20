# Phase 5 — Sub-phase 4 execution report

**Sub-phase:** Picker page + selection tray + follows persistence → ranking
**Status:** SUCCESS
**Validation:** PASS
**DoD:** PASS (one clause flagged as a scoped Python follow-up — entity-axis ranker consumption)
**Date:** 2026-06-05

---

## What I implemented

The page container that wires SP3's recursive engine into the onboarding flow, the
persistent bottom tray, and the two-axis follows-persistence function — completing the
splash → email → **picker** → loading → reel flow.

### `src/lib/onboardingProfile.ts` (extended — added `persistPickerFollows`)
Added a NEW picker-follows persist path alongside the untouched `persistInterestProfile`:
- **Topic follows** (`type === 'topic'`) → canonicalized via the EXISTING
  `findCanonicalInterest` (label/slug match against the public-read `interests` tree) →
  `user_interest_profile` upsert (reusing `ProfileUpsertRow` + `onConflict:
  "profile_user_id,profile_interest_id"`). A topic matching NOTHING is surfaced in
  `unpersisted` (never orphaned — mirrors the existing custom flow). Topics reach the
  ranker via `user_interest_profile`, which the Python ranker already reads.
- **Entity follows** (`type === 'entity'`, real registry id) → `user_entity_follows`
  upsert `{ follow_user_id, entity_id: followId, follow_path: path, follow_source,
  follow_weight }`, `onConflict: "follow_user_id,entity_id"`.
- **Weight map** `ENTITY_FOLLOW_WEIGHT_BY_SOURCE = { seed: 1.0, more: 1.0, custom: 2.0 }`
  — a SINGLE exported tunable map (not scattered), cited to ranking-spec §7 intent signal
  in a comment. Invariant `custom > more ≥ seed`.
- **Free-text customs** (`kind === 'freetext'`) → surfaced in `unpersisted`, NO
  `user_entity_follows` row (the NOT-NULL `entity_id` FK forbids it; orphan-avoidance,
  Rule 12), logged with a `fix_suggestion`.
- **Onboarded stamp** written ALWAYS (even on an empty `selections` skip). An empty array
  is a valid no-op: NO profile/follow rows, but onboarded_at IS stamped, and it does NOT
  error.
- Returns `PersistPickerResult { profile_count, entity_follow_count, unpersisted }`. Full
  types, Google-style JSDoc with example, structured logging mirroring the file, errors
  surfaced (thrown) never swallowed.

### `src/components/onboarding/OnboardingPicker.tsx` (new, 263 lines)
- Creates ONE store via `createSelectionStore()` in a `useRef` lazy initializer (stable
  across renders — NOT per-render).
- Renders the 8 `PICKER_TREE` categories as collapsible `CategorySection`s (chevron,
  `aria-expanded`, live per-category count from `store.all()` filtered on `path[0]`),
  each with collapsible `SubcategorySection`s that drop a `<FollowSet path={[cat.label,
  sub.label]} store={store}/>` per set (SP3 handles the recursion).
- Hosts `<SelectionTray store={store}/>` fixed at the bottom.
- Continue/Skip affordance (`data-picker-continue`): ALWAYS enabled (skippable); "Skip
  for now" at zero, "Continue with N" at >0; ≥48px; calls `onComplete(store.all())`.
- `data-onboarding-picker`, `data-picker-category/-toggle/-count`, `data-subcategory-toggle`
  hooks.

### `src/components/onboarding/SelectionTray.tsx` (new, 280 lines)
- Subscribes via `useSyncExternalStore(store.subscribe, store.getSnapshot)`.
- Shows: **total count** (Fraunces display per §8, system-serif fallback — no globals.css
  edit), live **preview** of recent picks (+N more), **per-category count pills** (group
  on `path[0]`), a toggleable **Review panel** grouped by category, and **Copy/Export** of
  the §7-shaped payload (drops the picker-internal `canonicalKey`). Fixed dark (`--ink`)
  bar, inline §8 hex tokens. `data-selection-tray`, `data-tray-count`, `data-tray-category`,
  `data-tray-review`, `data-tray-export` hooks. Clipboard failure is non-fatal.

### `src/components/onboarding/OnboardingFlow.tsx` (modified)
- Swapped the `chips` step → `picker` step rendering `<OnboardingPicker onComplete=…/>`.
- Removed the `InterestChips` import, the `selection: InterestSelection` state, the
  `handleSelectionChange`, and the `hasMinimumSelection`/`canContinue` "pick ≥1" gate
  (the picker is skippable).
- `handleComplete(selections)` → `setStep("loading")` → `persistPickerFollows(userId,
  selections)` → `router.push("/")`; surfaces `unpersisted` inline; on throw shows the
  error and returns to `picker`. A zero-length skip persists nothing (no error) and routes
  to the reel. Session-wait + onboarded logic intact.

### `src/app/(onboarding)/onboarding/page.tsx` (NOT modified)
No functional change needed — it gates the onboarded-skip and renders `<OnboardingFlow/>`,
which is unchanged at that seam.

### Tests
- `tests/lib/onboardingProfile.test.ts` (extended, +4 `persistPickerFollows` tests):
  two-axis write with **custom weight > seed weight** (asserted with actual numbers);
  SKIP writes NO follow rows but stamps onboarded_at and doesn't throw; free-text custom
  surfaced + NO `user_entity_follows` row (no orphan); unmatched topic surfaced + no
  orphan profile row. Reuses the existing mocked-Supabase `makeFakeClient`.
- `tests/lib/onboarding/onboardingPicker.test.tsx` (new, 5 tests): all 8 categories render
  (Health absent); skippable CTA reads "Skip" + count 0; selecting a seed chip flows
  through the SHARED store to the tray count (proves single-store wiring) + flips CTA to
  Continue + updates per-category count; `onComplete` fires with the §7 selection;
  `onComplete` fires with `[]` on a pure skip. `@/lib/entities` mocked.

---

## Files touched
- `src/lib/onboardingProfile.ts` (extended — `persistPickerFollows` + `PersistPickerResult`
  + `ENTITY_FOLLOW_WEIGHT_BY_SOURCE`; `persistInterestProfile`/its types untouched)
- `src/components/onboarding/OnboardingPicker.tsx` (new)
- `src/components/onboarding/SelectionTray.tsx` (new)
- `src/components/onboarding/OnboardingFlow.tsx` (modified — chips→picker step)
- `tests/lib/onboardingProfile.test.ts` (extended — +4 tests)
- `tests/lib/onboarding/onboardingPicker.test.tsx` (new — 5 tests)

NOT touched: `onboarding/page.tsx` (no change needed), globals.css, tailwind.config.ts,
any Python pipeline file, any SP1/SP2/SP3 source file, `InterestChips.tsx`. Verified via
`git diff --name-only` + `git status`.

---

## Decisions / divergences
1. **Weight map:** `{ seed: 1.0, more: 1.0, custom: 2.0 }` — one exported `const`
   (`ENTITY_FOLLOW_WEIGHT_BY_SOURCE`), cited to ranking-spec §7. `more === seed` for v1
   (the spec only requires `custom > more ≥ seed`); a future tune can lift `more` without
   touching call-sites.
2. **`extraPaths` v1 handling:** `user_entity_follows.follow_path` is a single `text[]`, so
   only the PRIMARY `selection.path` is persisted. Alternate paths (a canonical entity
   reached via several routes) are a DOCUMENTED v1 omission (noted in the function JSDoc),
   NOT a silent drop — the schema is one path column; multi-path persistence is a tracked
   follow-up.
3. **Free-text surfacing:** `kind:'freetext'` follows have no `entities` row → cannot be a
   `user_entity_follows` row (NOT-NULL FK). Surfaced in `unpersisted` + logged with a
   `fix_suggestion`, mirroring the existing unmatched-custom handling (Rule 12).
4. **Skippable gate removed:** dropped `hasMinimumSelection`/"pick ≥1"; the CTA is always
   enabled. A zero-follow skip persists nothing, no error, routes to the reel.
5. **InterestChips retained (flow-unused):** `InterestChips.tsx` is no longer in the FLOW
   (satisfies the DoD "old InterestChips path removed from the flow") but is INTENTIONALLY
   retained — its `InterestSelection` type is still imported by `onboardingProfile.ts`
   (`persistInterestProfile`) and `followSets.ts`; deleting it would break the build, and
   `persistInterestProfile` may serve the M3 voice path. **For the phase-gate slop scan:
   `InterestChips.tsx` + `persistInterestProfile` are intentional retained code, not dead
   code.**
6. **Export drops `canonicalKey`:** the tray Copy/Export emits exactly the §7 payload
   fields (`followId, label, path, type, kind?, ticker?, source`), omitting the
   picker-internal `canonicalKey`/`extraPaths`.

---

## Self-review findings + fixes
- **Store created once (not per-render):** `useRef` lazy initializer guard. Asserted by the
  "selecting a chip → count 1" test (a per-render store would reset to 0). Severity n/a — correct by design. ✓
- **Custom weight > seed:** asserted with `toBeGreaterThan` AND exact constants. ✓
- **Free-text / unmatched-topic never orphaned:** asserted no row carries the freetext id;
  no `user_interest_profile` row for an unmatched topic. ✓
- **Skip:** asserted BOTH follow-table upserts absent + onboarded stamp present + no throw. ✓
- **InterestChips removed from flow:** grep-confirmed the flow only mentions it in a doc
  comment (not imported/rendered). ✓
- **Tray via `useSyncExternalStore`:** confirmed. ✓
- **a11y / ≥44px:** chips (SP3) ≥44px `aria-pressed`; category/subcategory toggles
  `aria-expanded`; CTA ≥48px real button; tray Review/Copy ≥44px, Review `aria-expanded`. ✓
- **Errors surfaced:** persist throws caught in the flow → inline error → return to picker. ✓
- **Biome formatting** flagged 2 files (long call args / JSX wrap) — auto-fixed with
  `biome check --write`. Severity: low. Fixed. No critical/high issues.

---

## Validation results
- **`npx tsc --noEmit`** → `EXIT=0` (ZERO errors). The SP3-noted pre-existing
  `entities.ts:248` error is no longer present.
- **`npx biome check .`** → `Checked 105 files in 54ms. No fixes applied.` (clean).
- **`npx vitest run`** (full suite) → `Test Files 30 passed (30)` / `Tests 269 passed
  (269)` — no regressions (SP3 left 260; +9 here = 269: 4 persist + 5 picker component).
- **New SP4 tests** (`onboardingProfile.test.ts` + `onboardingPicker.test.tsx`) →
  `Test Files 2 passed (2)` / `Tests 15 passed (15)`.
- Passed within the 2-attempt bound (one format round). No skipped/shallow tests.

---

## Definition of done — per clause
SP4 DoD: *"completing the picker writes ≥1 `user_interest_profile` + ≥1
`user_entity_follows` row scoped to `auth.uid()`, with a `custom` follow carrying higher
`follow_weight` than a `seed` follow; **skipping** writes nothing and routes to the reel
with no error state; the old `InterestChips` path is removed from the flow;
`reference/ranking-spec.md` consumers (feed_assembly) read both follow sources."*

- **≥1 profile + ≥1 entity row scoped to auth.uid(), custom weight > seed** — PASS
  (mock-asserted with actual weight numbers; rows scoped to `USER_ID`; upserts on the
  unique/PK pairs).
- **Skipping writes nothing + routes to reel, no error** — PASS (mock-asserted: no follow
  upserts, onboarded stamped, no throw; the flow routes `router.push("/")`).
- **Old `InterestChips` path removed from the flow** — PASS (flow no longer imports/renders
  it; `InterestChips.tsx` retained only as a type dependency — see Decision #5).
- **`reference/ranking-spec.md` consumers read BOTH follow sources** — PARTIAL / FLAGGED:
  - **Topic axis = DONE.** Topic follows persist to `user_interest_profile`, which the
    Python ranker ALREADY reads (`agents/pipeline/stages/ranking.py`,
    `agents/ingestion/interest_keyed_pipeline.py`, `daily_batch.py` — grep-confirmed).
  - **Entity axis = REQUIRED FOLLOW-UP (Python, OUT OF SP4 SCOPE).** Entity follows go to
    the NEW `user_entity_follows` table, which the Python ranker does NOT yet read (grep:
    ZERO `user_entity_follows` references under `agents/`). Wiring it requires editing
    `agents/pipeline/` (e.g. `stages/ranking.py` to hydrate + weight entity follows),
    which is OUTSIDE SP4's declared TS file scope. **NOT faked (Rule 12).** Recommend a
    follow-up sub-phase: read `user_entity_follows` per user, map `entity_id`/path to
    candidate stories, apply `follow_weight` as an affinity boost.
- **RLS allow/deny on `user_entity_follows`** — PASS (encoded, deferred runtime) in SP1's
  `supabase/tests/0007_entity_registry_assertions.sql`; referenced, not re-authored.

---

## Font/token follow-up (Concern #3)
Per spec §8 the picker uses Fraunces / Spline Sans / Spline Sans Mono. SP4 styles (tray +
picker) are self-contained inline §8 hex tokens with **system-font fallbacks** (Fraunces →
Georgia serif, Spline Sans → system sans, Spline Sans Mono → ui-monospace) — I did NOT
register the webfonts (globals.css/tailwind out of SP4 scope). **Follow-up (styling pass):**
register the three webfonts (next/font or `@font-face`) so the picker + tray render in the
intended editorial faces. No behavior depends on this.

---

## Concerns for the phase-level gate
1. **Entity-axis ranker consumption is the one open DoD clause** (above) — a Python
   `agents/pipeline/` follow-up, explicitly out of SP4's TS scope. The topic axis is fully
   wired; the entity table + RLS + persistence are in place, only the Python read is
   pending. This is the single most important follow-up to surface.
2. **Slop scan:** `InterestChips.tsx` and `persistInterestProfile` are now flow-unused but
   INTENTIONALLY retained (type dependency + possible voice reuse) — treat as intentional,
   not dead code (Decision #5).
3. **Live registry:** migration 0007 must be applied + seeded for Show-more/Add-your-own to
   resolve live; until then SP3's offline fallbacks (`moreSeeds`, free-text) keep the picker
   usable. Free-text customs persist as `unpersisted` until a registry seed resolves them.
4. **Webfont registration** (Concern #3) — cosmetic follow-up, no behavior dependency.

---

## Return to orchestrator
1. **STATUS:** SUCCESS
2. **Files touched:** `src/lib/onboardingProfile.ts`, `src/components/onboarding/OnboardingPicker.tsx`
   (new), `src/components/onboarding/SelectionTray.tsx` (new),
   `src/components/onboarding/OnboardingFlow.tsx`, `tests/lib/onboardingProfile.test.ts`,
   `tests/lib/onboarding/onboardingPicker.test.tsx` (new). (`onboarding/page.tsx` unchanged.)
3. **Validation:** PASS — tsc 0 errors; biome clean (105 files); vitest 269/269 (30 files).
4. **DoD:** PASS for all clauses EXCEPT entity-axis ranker consumption, which is FLAGGED as
   a scoped Python follow-up (topic-axis consumption DONE).
5. **Concerns:** entity-axis ranker wiring (Python follow-up); InterestChips/persistInterestProfile
   intentionally retained; registry must be applied/seeded for live Show-more/search; webfont
   registration pending.
