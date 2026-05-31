# Phase 2 (M2 Detail + Trust) — Sub-phase 2 execution report

**Sub-phase:** Detail layer shell — drag-to-open panel, staggered reveal, body + key figure, mount slots
**Status:** SUCCESS — DoD PASS (one item PENDING human smoke, see below)
**Date:** 2026-05-31

## What I built
The swipe-right Detail layer: a framer-motion `motion.aside` that slides `x: 100% → 0`, follows the finger on a rightward drag, and mounts the real `StoryDetail` reading panel. On open it fetches the full Detail payload, then renders — in a staggered `.reveal` entrance (port-map §3.3) — the chunked **Playfair** body (in `chunk_index` order), the accent-coded `KeyFigureCard`, and the `TrustStrip` (SP3) + `StoryTimelineDrawer` (SP4) **stubs** already wired into the layout so SP3/SP4 edit only their own files. Drag-to-open and scrollTop-gated drag-to-close both live in `LayerStack` — the reel is never touched.

## Files touched (only these)
- CREATED `src/components/detail/StoryDetail.tsx` — the reading panel (fetch + staggered reveal + body/key-figure + stub slots + scrollTop seam).
- CREATED `src/components/detail/KeyFigureCard.tsx` — `var(--accent)` figure card; renders nothing when `key_figure_value` is null.
- CREATED `src/components/detail/TrustStrip.tsx` — **MINIMAL STUB** for SP3.
- CREATED `src/components/detail/StoryTimelineDrawer.tsx` — **MINIMAL STUB** for SP4.
- EDITED `src/components/shell/LayerStack.tsx` — replaced SP0's CSS-transition `<aside>` stub with a `motion.aside` panel mounting `StoryDetail`, plus the drag-to-open / drag-to-close gestures.
- CREATED `tests/lib/detail/storyDetail.test.tsx` — the component test (chunk-order DoD).

`page.tsx` needed no change (SP0 already mounts `LayerStack`). I did **not** touch `Reel.tsx`, `feed.ts`, `detail.ts`, `fetchStoryDetail.ts`, `LayerStackContext.tsx`, or any plan/reference/migration. (The `M` flags on `page.tsx`/`Reel.tsx` in `git status` are SP0's pre-step edits, not mine — verified by diffing: `page.tsx` = SP0's LayerStack mount, `Reel.tsx` = SP0's +14-line active-story sync.)

## How open/close drag is wired WITHOUT touching the reel
SP0 surfaced the reel's active story upward (`useLayerStack().activeStory`) and deliberately left the reel untouched. I kept that boundary:

- **Drag-to-OPEN:** a thin (`28px`) transparent left-edge `motion.div` region, mounted in `LayerStack` over the reel **only while Detail is closed and a story is active**. It is `drag="x"` with `dragSnapToOrigin`; `onDragEnd` commits open when `offset.x > 64 || velocity.x > 480` → `openDetail(activeStory)`. This matches the prototype `attachGestures` rule (`dx > 0 → openDetail`, the rightward swipe) and the §10 drag-to-follow + velocity upgrade. Because the region is a thin left-edge strip mounted only while closed, the reel's taps/scroll everywhere else are untouched, and it unmounts the instant Detail opens (never traps taps). The reel file is never edited — the trigger reads `activeStory` purely from context.
- **Drag-to-CLOSE:** the `motion.aside` panel itself is `drag="x"` (only while open), `dragConstraints={{left:0,right:0}}`, `dragElastic={{left:0,right:0.7}}` so it follows the finger rightward and snaps back to `x:0` when not committed. `onDragEnd` reads `detailScrollRef.current.scrollTop` synchronously and commits `closeDetail()` only when `scrollTop < 10 && (offset.x > 70 || velocity.x > 480)` — the prototype `attachBackSwipe` gate (`dx > 70 && scrollTop < 10`), so a close-drag never fights vertical reading scroll.
- **The scrollTop seam:** `LayerStack` owns a `RefObject<HTMLDivElement|null>` (`detailScrollRef`) and passes it to `StoryDetail` via the `scrollContainerRef` prop; `StoryDetail` attaches it to its scroll container. So `LayerStack` reads `scrollTop` inside the drag handler without `StoryDetail` re-rendering on scroll.
- **Reduced motion:** the panel slide transition becomes `{duration:0}` (snap) and the reveal drops its stagger (reduced item variants are already at rest) when `useReducedMotion()` is set — mirroring SP0's reel scale-back snap and the `ReelStory`/`AllCaughtUp` idiom.

## STUB prop interfaces SP3 / SP4 must preserve
`StoryDetail` passes each stub the already-fetched, already-ordered slice of the payload, so SP3/SP4 need **no extra fetch and no re-sort**.

**TrustStrip (SP3 — `src/components/detail/TrustStrip.tsx`):**
```ts
export interface TrustStripProps {
  /** fetchStoryDetail(...).trust_summary — coverage L/C/R counts, coverage_outlet_count,
      blindspot_lean: BiasLean | null, opposing_view_text: string | null */
  trustSummary: TrustSummary;
}
export function TrustStrip({ trustSummary }: TrustStripProps): JSX.Element
```
Mounted as `<TrustStrip trustSummary={detail.trust_summary} />`. SP3 fleshes out BiasBar + "COVERED BY N OUTLETS" + blindspot chip (only when `blindspot_lean !== null`) + OpposingViewCard — editing only this file (+ its own new `BiasBar.tsx`/`OpposingViewCard.tsx`).

**StoryTimelineDrawer (SP4 — `src/components/detail/StoryTimelineDrawer.tsx`):**
```ts
export interface StoryTimelineDrawerProps {
  /** fetchStoryDetail(...).timeline — already ordered by timeline_event_index; do NOT re-sort */
  timeline: TimelineEvent[];
}
export function StoryTimelineDrawer({ timeline }: StoryTimelineDrawerProps): JSX.Element
```
Mounted as `<StoryTimelineDrawer timeline={detail.timeline} />`. SP4 fleshes out the collapse/expand toggle + the ordered event list — editing only this file.

## Divergences (documented, not drift)
1. **Quote style — DOUBLE quotes** (Biome `quoteStyle: "double"`, every existing file). The global single-quote rule is wrong for this repo; per Rule 7/11 I matched the codebase, as SP0/SP1 did.
2. **Open-trigger affordance = a thin LEFT-EDGE drag region, not the whole reel surface.** The prototype attaches the open gesture to the entire reel; I cannot (scope lock forbids touching the reel, and a full-surface drag layer would block reel taps). A left-edge edge-swipe-right is the faithful, non-reel-touching equivalent of "swipe right opens detail." This is the SP0-sanctioned fallback (the trigger lives in the shell, reading `activeStory` from context).
3. **`LATERAL_TRANSITION` constant removed from `LayerStack.tsx`.** The mission said to "reuse" SP0's exported `LATERAL_TRANSITION`/`REEL_SCALEBACK_*`. In fact SP0 did **not** export them (module-private consts), and `LATERAL_TRANSITION` was a CSS-*string* (`transform 420ms cubic-bezier(...)`) — unusable by a framer `motion.aside`, which needs a structured `{duration, ease}`. I replaced it with `LATERAL_PANEL_TRANSITION = {duration:0.42, ease:[0.22,0.61,0.36,1]}` (the **same curve**, framer form) and removed the now-dead string constant to keep Biome's `noUnusedVariables` clean. `REEL_SCALEBACK_*` are unchanged and still drive the reel dim/scale-back. Net: same visual curve, no dead code.
4. **Component test renders via `react-dom/client` + React 19 `act`** (no `@testing-library/react` — not a dependency, and adding one is forbidden by the scope lock). Mocks `fetchStoryDetail` at the module boundary. Lives under `tests/lib/detail/` so the existing vitest `include` (`tests/lib/**`) picks it up without a config change.

## Self code-review findings + fixes
- **[med → fixed] Stale-fetch flash.** If the user opens story A then quickly opens B before A's fetch resolves, A's payload could overwrite B's. Added a monotonic `requestTokenRef` guard in the fetch effect — a resolved/ rejected response is applied only if it is still the latest request. Keyed on `story.digest_id`.
- **[low → fixed] Convention match for the `--accent` inline style.** Initially used an inline `["--accent" as string]` cast; switched to the repo's `AccentStyle = CSSProperties & { "--accent": string }` idiom (as `ReelStory` does) for Rule-11 consistency.
- **[low → addressed] Fail-loud on fetch error (Rule 12).** A failed Detail fetch shows a calm inline "Could not load… swipe back and try again" message, not a silent blank panel; the headline/segment label always render immediately from the in-memory story so the panel is never empty while loading.
- **[info] Null key figure.** `KeyFigureCard` returns `null` when `key_figure_value` is null (no empty shell); covered by a test.
- **[info] No reel regression / scope.** Verified via `git diff` that only the 6 listed files changed; `Reel.tsx`/`page.tsx` diffs are SP0's, not mine.
- No critical/high issues.

## Validation results
- **Biome** (`biome check .`): `Checked 58 files. No fixes applied.` — **0 errors, 0 warnings.**
- **Typecheck** (`tsc --noEmit`): exit 0 — **0 errors.**
- **Vitest** (`vitest run`, full suite): **13 files / 105 tests passed** (was 12/102 before — +1 file, +3 tests, no regressions). The `--localstorage-file` warnings are pre-existing jsdom noise (per SP0).
- **Build** (`npm run build`, static export): exit 0 — `✓ Compiled successfully`, `✓ Generating static pages (6/6)`, `✓ Exporting (2/2)`, `/` prerendered static (○). The `MODULE_TYPELESS_PACKAGE_JSON` warning is pre-existing tailwind-config noise.
- **Rule-9 mutation check (proves the test has teeth):** temporarily reversing the chunk render order made the order assertion FAIL (`renderedTexts` diverged), then I restored the file (verified `reverse()` count = 0). The test cannot pass while chunk ordering is dropped.

## DoD check
- ✅ **Component test** renders `StoryDetail` with `fetchStoryDetail` mocked to return chunks in `chunk_index` order and asserts they render in that exact DOM order (texts + ascending `data-chunk-index`); proven to FAIL if the order is dropped (Rule 9).
- ✅ Build passes (static export).
- ✅ With reduced motion the panel snaps (slide transition `{duration:0}`, reveal stagger dropped) — wired and reading `useReducedMotion()`.
- ✅ The panel shows the ordered chunked Playfair body + key figure for a story (tested); omits the card when the key figure is null (tested).
- ✅ Drag-to-open calls `openDetail(activeStory)` (left-edge region, offset/velocity threshold); drag-from-top (`scrollTop < 10`) calls `closeDetail()` (panel, scrollTop-gated). Wired in `LayerStack`.
- ✅ Reel dims/scales behind (SP0's `REEL_SCALEBACK_*`, unchanged) while Detail is open.
- ✅ lint 0, tsc 0.
- ⏳ **PENDING human smoke:** the real drag *feel* (finger-follow, commit thresholds, the scrollTop-gated close not fighting reading scroll) cannot be exercised headless — the actual pointer-drag gesture needs a device/simulator. Marked PENDING, not faked (as Phase 1 did). The decision logic (thresholds + scrollTop gate) is unit-reviewed; the gesture binding is what needs eyes.

## Concerns / handoff for SP3 & SP4
- **Edit ONLY your own file.** `StoryDetail.tsx` already imports and mounts both stubs with the exact props above; do not change `StoryDetail.tsx` or `LayerStack.tsx`. Flesh out the stub body in-place and add your own new sibling components (`BiasBar.tsx`/`OpposingViewCard.tsx` for SP3).
- **No re-sort.** `timeline` (SP4) arrives in `timeline_event_index` order; `detail_chunks` are already ordered. Render as-received.
- **Nullables.** SP3: `trust_summary.blindspot_lean` / `opposing_view_text` are `| null` — show the chip / opposing-view card only when non-null (the phase DoD's no-blindspot branch).
- **`var(--accent)`** is set on the Detail root by `StoryDetail` (per-story segment accent) — your stub bodies inherit it for accent-coded fills (BiasBar uses the `bias-left|center|right` tokens, not `--accent`, per port-map §4).
- **Reduced motion:** the reveal container/items already handle it at the `StoryDetail` level; your stub internals don't need their own reduced-motion branch unless you add new animation (e.g. SP4's expand/collapse should honour `useReducedMotion()`).
