# Phase 2 (M2 Detail + Trust) — Sub-phase 3 execution report

**Sub-phase:** Trust strip — BiasBar + coverage + blindspot + opposing view
**Status:** SUCCESS — DoD PASS
**Date:** 2026-05-31

## What I built
The "COVERAGE" authority strip that mounts in the SP2 `StoryDetail` reveal slot. `TrustStrip` (fleshed out from the SP2 stub, prop shape unchanged) composes, on the editorial trust-card surface:
1. the `COVERAGE` mono label + a **blindspot chip** shown ONLY when `blindspot_lean` is non-null (naming the under-covered lean, e.g. `BLINDSPOT · RIGHT`), else a neutral `BALANCED` chip;
2. `<BiasBar/>` — the L/C/R proportion bar;
3. the `COVERED BY N OUTLETS` mono count from `coverage_outlet_count`;
4. `<OpposingViewCard/>` — the opposing-view quote card (renders nothing when `opposing_view_text` is null).

`BiasBar` renders three fills (`bg-bias-left/center/right`) whose widths are the normalized proportions of the coverage counts, plus a mono `L · N  C · N  R · N` legend. `OpposingViewCard` is the one sparing light (`surface` `#D1D4BD`) card with the quote in Playfair.

## Files touched (ONLY these — scope lock honoured)
- CREATED `src/components/detail/BiasBar.tsx` — the L/C/R proportion bar + pure `computeBiasSegmentProportions`.
- CREATED `src/components/detail/OpposingViewCard.tsx` — the opposing-view quote card; returns `null` when text is null.
- EDITED `src/components/detail/TrustStrip.tsx` — fleshed out the SP2 stub; **`TrustStripProps` shape unchanged** (`{ trustSummary: TrustSummary }`).
- CREATED `tests/lib/detail/trustStrip.test.tsx` — the proportion-math + blindspot-branch + opposing-view tests.

I did NOT touch `StoryDetail.tsx`, `LayerStack.tsx`, `StoryTimelineDrawer.tsx`, `src/types/detail.ts`, `fetchStoryDetail.ts`, any reel/feed file, or any plan/reference/migration/seed. (The `M` flags on `src/app/page.tsx` / `src/components/reel/Reel.tsx` in `git status` are SP0's pre-step edits, not mine — verified untouched.)

## Proportion-math approach + all-zero handling
The math lives in a pure, exported `computeBiasSegmentProportions(left, center, right)` (verbose names) so it is testable in isolation:
- `total = left + center + right`; each segment width = `count / total * 100` as a CSS `%` string. The visual proportion is therefore exactly the coverage proportion (matches the prototype `biasBar()` `pct = n/tot*100`).
- **All-zero guard:** when `total === 0` (no outlets in any lean bucket), dividing would yield `NaN%` and an invisible/broken bar. The guard returns three equal thirds (`${100/3}%`) instead — the bar still renders an even neutral split. No divide-by-zero, no `NaN`.

## Blindspot branch (null vs set)
The `>70%-one-side` rule is applied at write time, so SP3 only branches on `blindspot_lean === null` vs set:
- `blindspot_lean` set → render the blush blindspot chip (`data-blindspot-chip="present"`) reading `BLINDSPOT · {LEAN.toUpperCase()}`.
- `blindspot_lean === null` → render NO blindspot chip; the neutral `BALANCED` chip (`data-blindspot-chip="absent"`) shows instead.

## Opposing-view branch
`OpposingViewCard` returns `null` when `opposing_view_text` is null (no empty sage shell — mirrors SP2's `KeyFigureCard` null-omission idiom). `TrustStrip` simply passes the nullable text down; the card owns the branch.

## Token usage + contrast caveat
- Bar fills use the `bias-left` (#3B82F6) / `bias-center` (#A1A1AA) / `bias-right` (#E8B7BC) tokens (`bg-bias-*`). All trust counts/labels are `font-mono` (port-map §4).
- **`#E8B7BC` contrast caveat respected:** the `R` legend label uses `text-white/55` (NOT the thin blush mono on `#020617`), while `L`/`C` keep their tokens (which read fine). The bar FILL still carries the blush on the right segment (caveat says fills are fine). The blindspot chip uses `#E8B7BC` text but on its own tinted blush field (`rgba(232,183,188,.1)`) + border — the prototype's accepted treatment, not bare mono on `#020617`.

## Divergences (documented, not drift)
1. **DOUBLE quotes / Biome formatting.** Matched the repo's Biome config (`quoteStyle: "double"`, every existing file) and let Biome autofix import ordering + a long-call collapse, per Rule 7/11 (as SP0–SP2 did). The global single-quote rule is wrong for this repo.
2. **Component tests render via `react-dom/client` + React 19 `act`** (no `@testing-library/react` — not a dependency; adding one is forbidden by the scope lock). Matches SP2's `storyDetail.test.tsx` idiom. Lives under `tests/lib/detail/` so the existing vitest `include` picks it up with no config change.
3. **Legend `R` label colour = `text-white/55`, not the `bias-right` token** — a deliberate honouring of the `#E8B7BC` contrast caveat (the prototype colours it `BIAS.right`; the port-map explicitly overrides that for small mono on `#020617`).

## Self code-review findings + fixes
- **[low → fixed] Import ordering (Biome `organizeImports`)** in `TrustStrip` — `react` type import must precede `@/` aliases; fixed via `biome check --write`.
- **[low → fixed] Long `computeBiasSegmentProportions(...)` call wrapped** in `BiasBar` — Biome collapsed it to one line (fits 120 cols).
- **[info] `blindspot_lean as string` cast** inside the `hasBlindspot` branch — safe because the JSX branch is gated on `blindspot_lean !== null`; TS narrows on the const but not across the ternary, so the cast is the minimal, commented bridge. No `any`.
- No critical/high issues. No `StoryDetail`/shared-file edits — verified the four-file scope.

## Validation results
- **Biome** (`biome check .`): `Checked 61 files. No fixes applied.` — **0 errors, 0 warnings.**
- **Typecheck** (`tsc --noEmit`): exit **0** — 0 errors.
- **Vitest** (`vitest run`, full suite): **14 files / 117 tests passed** (was 13/105 after SP2 → +1 file, +12 tests, no regressions). `--localstorage-file` warnings are pre-existing jsdom noise.
- **Build** (`npm run build`, static export): exit **0** — `✓ Compiled successfully`, `✓ Generating static pages (6/6)`, `✓ Exporting (2/2)`, `/` prerendered static (○). (First `npm run build` failed on a STALE `.next` webpack-runtime cache, NOT my code — `rm -rf .next && npm run build` passes clean. Flagged loud, not masked.) `MODULE_TYPELESS_PACKAGE_JSON` is pre-existing tailwind-config noise.
- **Rule-9 mutation check (proves the tests have teeth):**
  - Broke the proportion math (returned RAW counts as `px` widths instead of normalized `%`) → **3 tests FAILED** (the math unit test, the sum-to-100 test, the rendered-width test).
  - Broke the blindspot branch (`hasBlindspot = false` always) → the present-chip test **FAILED**.
  - Restored both originals → 12/12 pass again; verified no mutation residue (`px\`` count 0, `hasBlindspot = false` count 0) and Biome clean.

## DoD check
- ✅ **A test asserts the bias-bar segment widths equal the normalized counts** — both the pure `computeBiasSegmentProportions` (`count/total*100`) and the rendered `data-bias-segment` inline widths are asserted; a sum-to-100 test catches raw-count regressions. Proven to FAIL on a fabricated/un-normalized proportion (mutation check).
- ✅ **A test asserts the blindspot branch** — a story WITH `blindspot_lean = "right"` shows the present chip reading `BLINDSPOT · RIGHT` (and no balanced chip); a story with `blindspot_lean = null` shows NO present chip, only `BALANCED`. Proven to FAIL on a wrong blindspot state (mutation check).
- ✅ **Opposing-view card** renders its quote when present, nothing when null (component returns null; tested both branches, plus `TrustStrip` omits it for a null-opposing-view story).
- ✅ `COVERED BY N OUTLETS` reads `coverage_outlet_count` (tested).
- ✅ lint 0, tsc 0, build passes.
- ✅ `TrustStripProps` shape unchanged (`{ trustSummary: TrustSummary }`); `StoryDetail` mount untouched.

## Concerns
- None blocking. The trust strip's real *visual* feel (the blush blindspot chip on the trust card, the sage opposing-view card against the near-black canvas) is unit-verified for structure/branch but, like SP2's drag feel, the final on-device colour/contrast read is a human-smoke item — the contrast caveat is honoured in code (R legend de-blushed, chip on tinted field), so this is a confidence check, not a known gap.
