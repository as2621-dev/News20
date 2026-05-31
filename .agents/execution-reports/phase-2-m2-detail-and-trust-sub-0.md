# Phase 2 (M2 Detail & Trust) — Sub-phase 0 (shell pre-step): LayerStack lateral-layer shell

**STATUS: SUCCESS — DoD PASS**

Built the structural lateral-layer shell that Phase 2's later sub-phases (detail panel, trust strip, timeline) mount into. The reel renders unchanged as the base layer; the Detail layer is a minimal stub for SP2.

## What I built
- A `LayerStack` shell that hosts the reel as its base layer, owns the Detail-layer open/close state (`isDetailOpen` + `openDetailStory`), applies the reel dim/scale-back depth cue (`scale(0.94) brightness(0.45)`, port-map §3.3) when Detail is open, and renders a stub right-lateral Detail slot (`translateX(100%)` closed → `0` open).
- A small React context (`LayerStackContext` + `useLayerStack()` hook) so the reel can publish its active story upward and SP2's Detail panel can call `closeDetail()`. The hook throws outside a provider (Rule 12, fail loud).
- Mounted `LayerStack` in `src/app/page.tsx`, wrapping `<Reel/>`, still inside `PhoneShell`. Did NOT create `(reel)/page.tsx` (would collide at `/` — Phase 1e trap).
- Surgical reel edit: `Reel` consumes `useLayerStack()` and syncs `stories[activeIndex]` upward via one effect. No audio/karaoke/scroll-snap/status-machine logic touched; no visible/interactive element added — the reel looks and behaves exactly as before.

## Files created / modified
- CREATED `src/components/shell/LayerStack.tsx`
- CREATED `src/components/shell/LayerStackContext.tsx`
- MODIFIED `src/app/page.tsx` (compose `LayerStack` between `PhoneShell` and `Reel`)
- MODIFIED `src/components/reel/Reel.tsx` (import `useLayerStack`; one effect syncing the active story upward — ~14 lines)

Nothing outside the allowed set was touched.

## The Story id field (the key finding)
The mission guessed `story_id`. **The actual TS field on the `Story` interface (`src/types/feed.ts`) is `digest_id`** (a `string`). Its JSDoc says it maps to the Postgres `stories.story_id` column, but the in-code field name SP2 must key on is **`digest_id`**. The reel already keys `ReelStory` on `story.digest_id`. My context's `openDetail(story)` / `openDetailStory` / `activeStory` all carry the full `Story`, so SP2 reads `openDetailStory.digest_id`.

## Divergences (and why)
1. **Quote style — used DOUBLE quotes, not single.** The mission text said "single quotes," but `biome.json` sets `quoteStyle: "double"` and every existing file uses double quotes. Rule 7 (pick the more-tested/recent pattern) + Rule 11 (match the codebase) → double quotes. The single-quote instruction is wrong for this repo; flagging it.
2. **Active-story seam, not a swipe trigger.** The mission's preferred path was a swipe-right affordance calling `openDetail(currentStory)`. Wiring a real drag-to-open into `ReelStory`/`ReelChrome` is non-trivial and risks reel regression, and any visible affordance would violate the "look exactly as before" lock. Per the mission's explicit fallback, I surfaced the active story upward (context `activeStory` + `setActiveStory`) and left the actual drag-to-open trigger to SP2. DoD #4 ("active story reachable by the shell") is satisfied via `useLayerStack().activeStory`.
3. **Stub slot is plain CSS-transition, not framer drag.** As instructed — SP2 owns the drag gesture, staggered reveal, and content. I kept the prototype's exact lateral curve (`420ms cubic-bezier(0.22,0.61,0.36,1)`) and the `scale(0.94)/brightness(0.45)` constants verbatim so SP2's framer port lands on the same values.

## Self code-review findings + fixes
- **[low] Off-screen stub trapping taps** — added `inert={!isDetailOpen}` + `aria-hidden` on the `<aside>` so the closed (off-screen) Detail layer never intercepts pointer events over the reel. React 19 types support `inert`; tsc clean.
- **[info] Reduced motion** — verified it matches `ReelStory`/`AllCaughtUp`: read once via framer-motion `useReducedMotion()`; both the reel scale-back and the lateral slide set `transition: "none"` (snap) when set. The dim/scale state still applies; only the animation is dropped, matching `styles.css`'s reduced-motion media query.
- **[info] No reel regression** — the reel edit is a pure upward state sync (effect + context read). `LayerStack` root is `relative h-full w-full`, wrapping the reel in a `height/width:100%` div — same sizing the reel already received from `PhoneShell`'s `absolute inset-0` content area. No layout shift.
- No critical/high issues found.

## Validation results
- **Biome lint** (`npm run lint`): `Checked 50 files. No fixes applied.` — **0 errors.**
- **Typecheck** (`npx tsc --noEmit`): exit 0 — **0 errors.**
- **Tests** (`npm test` / vitest): **11 files passed, 95 tests passed.** (The `--localstorage-file` warnings are pre-existing vitest/jsdom noise, not failures.)
- **Build** (`npm run build`, static export): exit 0 — `✓ Compiled successfully`, `✓ Exporting (2/2)`, `/` prerendered as static (○). (The `MODULE_TYPELESS_PACKAGE_JSON` warning is pre-existing tailwind-config noise, unrelated.)

## DoD — PASS
1. ✅ `next build` static-export passes.
2. ✅ `/` renders the reel as the base of the LayerStack with no visual/behavioral change (no UI added; identical sizing; reel internals untouched).
3. ✅ `LayerStack` exists, hosts the reel layer, owns `isDetailOpen` + open story state, exposes `openDetail`/`closeDetail` via context, applies the dim/scale-back when open, and has a stub Detail slot.
4. ✅ The reel's active story is reachable by the shell via `useLayerStack().activeStory`.
5. ✅ lint 0, tsc 0, vitest 95/95 green.

## Integration contract for SP2
- **Story id field: `digest_id`** (string) on the `Story` interface. Key the Detail panel on `openDetailStory.digest_id` (and fetch `detail_chunks`/`story_trust`/`story_timeline` by it).
- **Mount point:** the `<aside aria-label="Story detail">` inside `LayerStack.tsx`. Replace the empty body with `<StoryDetail story={openDetailStory} />` (guard `openDetailStory != null`). The `<aside>` already slides `translateX(100%) → 0`; SP2 may replace the CSS transition with a framer `motion.aside` + `drag="x"` drag-to-follow (port-map §3.2) and the staggered `.reveal` entrance (§3.3) — constants to reuse are in `LayerStack.tsx` (`LATERAL_TRANSITION`, `REEL_SCALEBACK_*`).
- **Open trigger (SP2's job):** in `Reel`/`ReelChrome`, consume `useLayerStack()` and call `openDetail(activeStory)` from a swipe-right affordance. `activeStory` is already kept in sync by the reel — SP2 only needs to add the gesture/trigger.
- **Close:** the Detail panel calls `useLayerStack().closeDetail()` from its back-swipe / close control. `LayerStack` keeps `openDetailStory` mounted through the slide-out; if SP2 needs to unmount heavy content, clear it on transition-end.
- **Reduced motion:** `LayerStack` already snaps under `useReducedMotion()`. SP2's framer panel should mirror this (skip stagger, snap the slide).
- **One watch-item:** `Reel` now requires a `LayerStack` ancestor (`useLayerStack()` throws otherwise). Any future test/story that renders `<Reel/>` in isolation must wrap it in `<LayerStack>`. No current test renders `Reel` directly, so nothing breaks today.
