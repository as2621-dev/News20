# Phase 5 — Sub-phase 3 execution report

**Sub-phase:** Recursive follow-set engine (FollowSet + FollowChip)
**Status:** SUCCESS
**Validation:** PASS
**DoD:** PASS
**Date:** 2026-06-05

---

## What I implemented

The recursive heart of the picker: the lifted typed tree, the canonical-dedupe
selection store, and the two recursive UI units that render against it.

### `src/types/picker.ts` (new, 182 lines)
The §5 model + store types: `PickerNode`, `PickerFollowSet` (+ `registry`,
`moreSeeds`, `allowCustom`), `PickerCategory`/`PickerSubcategory`, `FollowSelection`
(spec §7 shape + `canonicalKey` + `extraPaths`), `FollowSource`, `CanonicalKey`, and
the `SelectionStore` interface (`toggle`/`has`/`hasCanonical`/`all`/`count`/
`subscribe`/`getSnapshot`). Re-exports `EntityKind` from `@/lib/entities`.

### `src/lib/pickerSeedTree.ts` (new, 1036 lines — pure lifted data)
The prototype's `DATA` const transcribed **verbatim** (lifted, not authored) into a
typed `RAW_PICKER_DATA` with `RawNode`/`RawSet`/`RawSubcategory`/`RawCategory`. Split
out per the phase's permitted seed-tree split so the engine stays small.

### `src/lib/followSets.ts` (new, 421 lines — the engine)
- `slug()` ported **verbatim** from the prototype (`lower → [^a-z0-9]+ → '-' → trim`).
- `liftPickerTree()`/`PICKER_TREE`: transforms `RAW_PICKER_DATA` into the typed §5
  tree with **path-derived ids** (chip = `${idBase}/${slug(label)}`, set =
  `${idBase}/${slug(set.label)}`, sub = `${cat.id}/${slug(sub.label)}`) — exactly the
  prototype/registry scheme. Tags `type` (`entity` iff `kind`), attaches a `registry`
  pointer `{ parent: setId, kind }` to entity sets (gates Show-more/Add-your-own), and
  keeps `set.more` as `moreSeeds` (offline fallback).
- `canonicalKeyFor()`: cross-path dedupe identity — `${kind}:${ticker ?? slug(label)}`
  (entities), `topic:${slug}` (topics), `freetext:${slug}` (customs).
- `selectionFromNode`/`selectionFromEntity`/`selectionFromFreeText`: pure builders for
  the §7-shaped `FollowSelection` (seed / more / custom).
- `createSelectionStore()` → `FollowSelectionStore`: a framework-light **subscribable**
  store keyed by `followId`, with canonical dedupe (one entry per `CanonicalKey`,
  appending alternate paths into `extraPaths`), consumed via `useSyncExternalStore`.

### `src/components/onboarding/FollowChip.tsx` (new, 108 lines)
A real `<button>` with `aria-pressed`, `minHeight: 44`, label + (entity) `ticker` in
the rust accent (`#9a4a1f`), deep-green selected fill (`#3a5a40`), dashed border for
customs. Data hooks: `data-follow-chip`, `data-follow-id`, `data-selected`,
`data-custom`, `data-ticker`, `data-ticker-label`. Self-contained inline §8 tokens.

### `src/components/onboarding/FollowSet.tsx` (new, 406 lines)
The recursive set unit: eyebrow (mono caps) + **Select all** + **Show more**
(entity sets only; `listEntities` paginated by `nextCursor`, deduped against mounted
ids, control hidden at `nextCursor === null`, **offline fallback** to `moreSeeds` on
error) + chip grid (recursively mounts a selected chip's nested `FollowSet`s) + **Add
your own** (debounced `searchEntities`; pick a hit → custom-resolved entity, else
free-text follow). 2px left rule for nesting. Subscribes to the store via
`useSyncExternalStore` so lazy-mount + preserve-on-collapse are driven by STORE state.

### Tests (new)
- `tests/lib/followSets.test.ts` (310 lines) — slug, lift/ids, the four marquee lifts,
  canonical dedupe, store toggle/subscribe/snapshot, cross-path dedupe + path-removal.
- `tests/lib/onboarding/followSet.test.tsx` (366 lines) — component tests via
  `react-dom/client` + `act` (the `trustStrip.test.tsx` idiom), `@/lib/entities`
  mocked. All four §4 marquee cases as first-class DOM tests + Show-more append/
  no-overlap/terminate + Select all + lazy-mount/preserve-on-collapse + cross-path
  dedupe + Add-your-own (free-text + resolved) + chip a11y (button/aria-pressed/44px).

---

## Files touched
- `src/types/picker.ts` (new)
- `src/lib/followSets.ts` (new)
- `src/lib/pickerSeedTree.ts` (new — permitted seed-tree split)
- `src/components/onboarding/FollowChip.tsx` (new)
- `src/components/onboarding/FollowSet.tsx` (new)
- `tests/lib/followSets.test.ts` (new)
- `tests/lib/onboarding/followSet.test.tsx` (new)

NOT touched: globals.css, tailwind.config.ts, any SP1/SP2/SP4 file (verified via
`git status`/`git diff`).

---

## Decisions / divergences
1. **Store: a dependency-free vanilla subscribable store, NOT zustand and NOT
   component `useState`.** `zustand` is in package.json but used NOWHERE in `src/`
   (grep-verified); the shipped onboarding (`InterestChips`) uses `useState`. Per
   Rule 11 I avoided introducing a brand-new zustand idiom AND avoided component-local
   `useState` (which can't survive the unmount preserve-on-collapse requires). The
   store is a tiny class with `subscribe`/`getSnapshot`, read via React 19's built-in
   `useSyncExternalStore`. No new dependency. Factory (`createSelectionStore`) not a
   singleton, so sessions/tests don't bleed state.
2. **Dedupe key = `${kind}:${ticker ?? slug(label)}`** (entities), `topic:slug`,
   `freetext:slug`. Ticker-first because the registry holds 3 Nvidia rows all `NVDA`;
   they must collapse to one follow. The store keeps ONE entry per canonical key and
   APPENDS alternate paths into `extraPaths` (it does NOT create a second entry).
   `count()`/`all()` are canonical. Removing one path keeps the follow until the last
   path is gone (tested).
3. **Registry pointers derived from the lift:** a set gets `registry: { parent: setId,
   kind }` iff it has entity items; `kind` = the first entity item's kind (sets are
   homogeneous in the prototype — a Companies set is all `company`). Pure-topic sets
   get NO registry → no Show-more/search gate (matches the prototype, which only shows
   Show-more when `set.more` exists).
4. **`moreSeeds` = the OFFLINE fallback for Show-more.** Live Show-more calls
   `listEntities`; on error/offline it appends the lifted `set.more` (deduped) and
   marks the set exhausted (moreSeeds is a single static page). Spec §11 "registry
   offline → fall back to seeds."
5. **Free-text validation:** Add-your-own trims and `.slice(0, 80)` (spec §11 "trim/
   validate sensibly"); `maxLength={80}` on the input.
6. **Lazy mount + preserve-on-collapse via STORE state:** a selected chip's nested
   sets render (and UNMOUNT on deselect), but child selections live in the store, so
   re-selecting the parent restores them. The DOM is a pure projection of store state.

---

## Self-review findings + fixes
- **Lifted-id spot-check** (Nvidia under Earnings) == `business/corporate-news/what-to-
  track/earnings/companies-to-track/nvidia`, the exact spec id. ✓ (pinned by a test)
- **Cross-path canonical** — Nvidia(AI hardware) and Nvidia(Earnings) have DISTINCT
  ids but the SAME canonical `company:NVDA`; store collapses to one entry. ✓
- **Show-more dedupe + terminate + offline fallback** — all three asserted. ✓
- **Chips are real `<button>`s with `aria-pressed`, `minHeight:44`.** ✓ (asserted)
- **`showMoreStarted` dead variable** — found by biome (FIXABLE warning), removed it +
  its setter call. (Was the only real finding.) Severity: low. Fixed.
- **Component test cross-path assertion was wrong** (asserted `count===1` but selecting
  the Earnings chip itself adds Earnings as a follow → count 2). Fixed the assertion to
  check the Nvidia canonical specifically (exactly one Nvidia entry, both paths), which
  is the actual dedupe contract. Severity: test-only, medium. Fixed.
- **`act()` environment warning** — set `IS_REACT_ACT_ENVIRONMENT = true` at the top of
  the component test; warnings gone, updates flush synchronously. Severity: low. Fixed.
- No critical/high issues. No `@testing-library`. No globals.css/tailwind edits.

---

## Validation results
- **`npx vitest run tests/lib/followSets.test.ts tests/lib/onboarding/followSet.test.tsx`**
  → `Test Files 2 passed (2)` / `Tests 30 passed (30)`.
- **Full suite `npx vitest run`** → `Test Files 29 passed (29)` / `Tests 260 passed
  (260)` — no regressions (SP2 left 230; +30 here = 260).
- **`npx biome check .`** → `Checked 102 files in 66ms. No fixes applied.` (clean repo).
- **`npx tsc --noEmit`** → ZERO errors in any SP3 file. One pre-existing error remains
  in `src/lib/entities.ts:248` (an SP2 Supabase `.returns<EntityRow[]>()` RPC-typing
  quirk) — that file has NO diff vs HEAD (untouched by me; out of SP3 scope). Flagged
  honestly (Rule 12), not masked.
- Passed within the 2-attempt bound (one fix round for the test assertion + act flag).

---

## Definition of done — PASS
Re-checked against the phase file's SP3 DoD, each clause verified by a test:
- Earnings → *Companies to track* with tickers + Select all + Show more. ✓
- Oil & gas → three sets (Majors / Midstream / Equipment). ✓
- NFL → *Teams* (Show more) **and** *People*; College football independent. ✓
- A music genre → its Artists; multi-genre multi-select. ✓
- Selecting a chip lazily mounts its nested sets; deselecting collapses but the store
  still holds child selections (re-select restores). ✓
- An entity reachable via two paths (Nvidia AI-hardware + Earnings) dedupes to ONE
  underlying canonical follow carrying BOTH paths. ✓
- Mocked-data + component tests encode each marquee case (Rule 9 WHY comments). ✓
No skipped/shallow tests; the dedupe test asserts canonical count + both paths, the
preserve-on-collapse test asserts state survives a real DOM unmount.

---

## Font/token follow-ups (Concern #3)
Per spec §8 the picker uses Fraunces / Spline Sans / Spline Sans Mono. SP3 styles are
self-contained inline §8 hex tokens with **system-font fallbacks** (acceptable for v1
per §8) — I did NOT register the webfonts (globals.css/tailwind are out of SP3 scope).
**Follow-up for SP4 (or a styling pass):** register the three webfonts (next/font or a
`<link>` + globals.css `@font-face`/Tailwind `fontFamily`) so the picker renders in the
intended editorial faces rather than system fallbacks. No behavior depends on this.

---

## Concerns for SP4 — the exact API SP4 consumes

**Tree (import from `@/lib/followSets`):**
```ts
import { PICKER_TREE, createSelectionStore } from "@/lib/followSets";
import type { PickerCategory, FollowSelection, SelectionStore } from "@/types/picker";
// PICKER_TREE: PickerCategory[]  — 8 categories → subs → sets → items (recursive).
```
SP4's `OnboardingPicker` renders `PICKER_TREE` as Category/Subcategory shells (the
collapsible `<details>` the prototype uses) and drops a `<FollowSet followSet={set}
path={[cat.label, sub.label]} store={store} />` per subcategory set.

**Store (one per picker session, owned by `OnboardingPicker`):**
```ts
const store = createSelectionStore();            // SelectionStore
store.subscribe(listener); store.getSnapshot();  // for useSyncExternalStore in the tray
store.all(): FollowSelection[];                  // one entry per CANONICAL follow
store.count(): number;                           // tray total
```
- **Per-category counts:** group `store.all()` by `selection.path[0]` (the category
  label is always the first path segment): `byCat[sel.path[0]] = (… ?? 0) + 1`. This
  exactly reproduces the prototype's tray pill-counts.
- **§7 persistence payload:** each `FollowSelection` already IS the §7 shape —
  `{ followId, label, path, type, kind?, ticker?, source }` plus `canonicalKey` and
  `extraPaths`. For persistence:
  - **Topic follows** (`type === 'topic'`) → `user_interest_profile` (Open Q1's
    recommendation).
  - **Entity follows** (`type === 'entity'`, includes `kind:'freetext'`) →
    `user_entity_follows`. Use `followId` as the `entity_id` for the PRIMARY path; the
    full set of paths to persist into `follow_path` is `[selection.path,
    ...(selection.extraPaths ?? [])]`. Map `source → follow_weight` (custom > more >
    seed) per §7. **Free-text customs** carry `kind:'freetext'` and no registry row —
    SP4 decides (Open Q2) to store them as a free-text follow (recommended) vs resolve;
    they have a path-derived `followId` (`${setId}/${slug(label)}`), stable + unique.
  - **Canonical dedupe is already done** — `all()` returns one entry per real-world
    follow, so SP4 never writes duplicate `(user, entity)` rows. If SP4 needs ONE
    `entity_id` per canonical entity but the registry has 3 Nvidia rows, pick the
    primary `followId` (first-selected) and record the alternates' paths in
    `follow_path`; the `entity_id` choice is SP4's per the SP1/SP2 carry-forward.
- **Skippable:** zero selections → `store.count() === 0` → SP4 writes nothing and
  routes to the reel (no error), satisfying the phase's skippable DoD.

**Note (SP1 live-apply):** migration 0007 is not yet applied; `listEntities`/
`searchEntities` resolve only after the owner's one-time apply+seed. SP3 mocks the
client in tests; the offline fallbacks (`moreSeeds`, free-text) keep the picker fully
usable even before the registry is live.
