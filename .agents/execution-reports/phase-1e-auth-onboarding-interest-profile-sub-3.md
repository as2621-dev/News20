# Phase 1e SP3 — 3-level interest chips + custom interest + strict toggle

**Status:** COMPLETE
**Date:** 2026-05-30

## Implemented
- **`src/lib/interests.ts`** — typed data-access over the public-read `interests` tree (anon client), sibling of `supabaseFeed.ts`:
  - `Interest` type (verbose fields mirroring migration-0003 columns: `interest_id`, `parent_interest_id`, `interest_slug`, `interest_label`, `depth_level`, `interest_segment_slug` (nullable), `interest_search_query` (nullable), `interest_kind`).
  - `fetchRootInterests(client?)` → depth-0 rows via `.is("parent_interest_id", null).eq("interest_is_active", true).order("interest_sort_order")`.
  - `fetchChildInterests(parentInterestId, client?)` → direct children via `.eq("parent_interest_id", parentInterestId).eq("interest_is_active", true).order(...)` — the lazy expansion read.
  - Injectable client default `getSupabaseBrowserClient()` (matches `supabaseFeed.ts`). Structured logging on start/complete/fail; both fns throw with `fix_suggestion` (no swallowed errors).
- **`src/components/onboarding/CustomInterestChip.tsx`** — `"use client"` free-text chip. On submit emits the trimmed label via `onAddCustom(label)`; never emits blank/whitespace (guarded). No DB write.
- **`src/components/onboarding/InterestChips.tsx`** — `"use client"` tree with lazy expansion: depth-0 on mount; tapping a chip selects it AND fetches+reveals children (depth-1, then depth-2); per-selected-node strict toggle ("ONLY THIS"); composes `CustomInterestChip`. Selection (taxonomy picks + strict flags + customs) held in-memory and surfaced via `onSelectionChange(selection)`. No DB write.
- **`tests/lib/interests.test.ts`** — boundary-mock tests (Rule 9).

## Files created (ONLY the allowed set)
- `src/lib/interests.ts`
- `src/components/onboarding/InterestChips.tsx`
- `src/components/onboarding/CustomInterestChip.tsx`
- `tests/lib/interests.test.ts`

**Explicit scope-lock confirmation:** I touched NOTHING else. `git status` shows `src/lib/supabase/client.ts` as modified and other untracked files (`auth.ts`, `EmailSignIn.tsx`, `(auth)/callback`, migration 0003, seed, SP1/SP2 reports) — these are all pre-existing SP1/SP2 artifacts (timestamped 20:13–20:14), NOT mine. My files are timestamped 20:23–20:24. I ran no git-mutating commands. I created no docs under `plans/`, `reference/`, or `supabase/`. New types (`SelectedTaxonomyInterest`, `SelectedCustomInterest`, `InterestSelection`) are defined INSIDE the in-scope component file, not in `src/types/`.

## Divergences (+ why)
- **Selection state shape:** `InterestChips` exposes selection as `{ taxonomy_selections: SelectedTaxonomyInterest[], custom_selections: SelectedCustomInterest[] }` via `onSelectionChange`, not a single flat array. WHY: SP4 persists taxonomy picks (which have an `interest_id`) and customs (which do NOT — canonicalized later) down two different paths; splitting them at the seam keeps SP4's persist logic clean. Each taxonomy selection carries `depth_level` so SP4 can weight depth-2 leaves vs depth-0 categories (phase Open Q1) without a re-fetch.
- **Strict toggle visibility:** the toggle renders only for an already-selected chip (strictness on an unpicked node is meaningless; unpicking clears the flag). Matches the phase intent ("marks that *selection* strict").
- **Custom canonicalization deferred:** per phase Open Q2 (flat custom node for v1), SP3 only carries `interest_kind: "custom"` + label; SP4 resolves it. No divergence — explicitly out of SP3 scope.

## Review findings + fixes (Step B–C)
- **Fix 1 (self, pre-validate):** initially placed the `CustomInterestChip` import at the bottom of `InterestChips.tsx` (after the component). Moved to the top with the other imports — Biome `organizeImports`/import-after-statement would have flagged it.
- **Fix 2 (Biome safe-fix):** `npx biome check --write` auto-fixed 3 files — collapsed `fetchRootInterests` signature to one line (lineWidth 120) and sorted the `type Interest` import in two files. No logic change.
- Verified: no `any`; the single `as never` is at the test boundary with a `// Reason:` (matches `supabaseFeed.test.ts`); both data-access fns throw (no swallowed errors); double quotes throughout; child-fetch failure logs loudly (not silent).

## Validation output (Step D)
- `npm run lint` (Biome) → `Checked 43 files in 18ms. No fixes applied.` — **0 errors.**
- `npx tsc --noEmit` → no output — **0 errors.**
- `npx vitest run` → `Test Files 10 passed (10) / Tests 89 passed (89)` — **all pass** (4 new tests in `interests.test.ts`).
- `npm run build` → `✓ Compiled successfully` / `✓ Exporting (2/2)` — **static export compiles.**

## Definition of done (Step E)
**PASS.**
- Chips render seeded depth-0 categories: `fetchRootInterests` returns only depth-0 (`.is("parent_interest_id", null)`), asserted vs mocked client.
- Tapping reveals depth-1 then depth-2: `fetchChildInterests(parentId)` queries by that parent (asserted; test fails if the parent filter is dropped — Rule 9), and `InterestChips.handleChipTap` calls it on tap, caching + rendering children recursively.
- Toggling strict marks that selection strict: `handleStrictToggle` flips `selectedStrictById[id]`, surfaced as `profile_is_strict` in the selection.
- Custom free-text yields a pending custom selection with `interest_kind: "custom"` (no `interest_id`).
- No write happens in SP3 (verified: no `.insert`/`.upsert`/`.update` anywhere in the three files; data-access is select-only).

## Concerns for SP4
- **Selection object shape SP4 will persist** (`InterestSelection`, exported from `InterestChips.tsx`):
  ```ts
  interface InterestSelection {
    taxonomy_selections: {
      selection_kind: "taxonomy";
      interest_id: string;       // → user_interest_profile.profile_interest_id
      interest_label: string;    // display only
      depth_level: number;       // for default profile_weight by depth (Open Q1)
      profile_is_strict: boolean;// → user_interest_profile.profile_is_strict
    }[];
    custom_selections: {
      selection_kind: "custom";
      interest_kind: "custom";   // canonicalize/attach in SP4 (Open Q2: flat custom node)
      custom_label: string;      // no interest_id yet
    }[];
  }
  ```
- SP4 maps each `taxonomy_selection` to a `user_interest_profile` row with `profile_source='typed'`, `profile_is_strict` preserved, and a `profile_weight` chosen by `depth_level`. Customs need a node created/matched first (never a dangling row — phase DoD), then the same profile write.
- **"Pick at least 1" gate (Open Q3):** SP3 does NOT enforce a minimum. SP4 should gate "continue" on `taxonomy_selections.length + custom_selections.length >= 1`.
- **If SP4 needs these types in a shared location** (`src/types/`) or imported by `onboardingProfile.ts`, they currently live in `InterestChips.tsx` (scope-lock constraint). SP4 can import them from `@/components/onboarding/InterestChips` or relocate them — flag to orchestrator if a `src/types/` home is preferred.
- `onSelectionChange` fires on every change (incl. the initial empty selection once roots load, due to the `interestsById` dep). SP4 should treat it as the current full snapshot, not a delta.
```
