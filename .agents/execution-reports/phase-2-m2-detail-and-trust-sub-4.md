# Phase 2 (M2 Detail + Trust) — Sub-phase 4 execution report

**Sub-phase:** Timeline drawer — "HOW IT DEVELOPED"
**Status:** SUCCESS — DoD PASS
**Date:** 2026-05-31

## What I built
Fleshed out the SP2 stub `StoryTimelineDrawer.tsx` into the real collapsed/expandable
**"HOW IT DEVELOPED"** drawer (port-map §2 row 6 "timeline collapsed/expanded";
prototype `#trust-toggle` / `.trust-drawer` / `.tl-item`). It starts collapsed,
renders a full-width tappable header (≥44px hit target), and on tap expands to show
every `TimelineEvent` in the **received order** (`timeline_event_index` order —
NOT re-sorted), each row showing `timeline_when_label` (mono) over
`timeline_what_text` (sans), with the prototype's accent-dot + connector. A second
tap collapses it. An empty `timeline` renders nothing.

## Files touched (only these — scope lock honoured)
- EDITED `src/components/detail/StoryTimelineDrawer.tsx` — replaced the stub body with
  the real toggle + ordered event list. **`StoryTimelineDrawerProps` shape unchanged**
  (`timeline: TimelineEvent[]`); `StoryDetail` still mounts `<StoryTimelineDrawer timeline={detail.timeline} />`.
- CREATED `tests/lib/detail/storyTimelineDrawer.test.tsx` — the component test
  (collapsed-default + toggle round-trip + ordering + empty-guard).

Verified by mtime that no sibling file changed: `StoryDetail.tsx`, `TrustStrip.tsx`,
`BiasBar.tsx`, `OpposingViewCard.tsx`, `KeyFigureCard.tsx` all retain their
pre-session timestamps. Did not touch `LayerStack.tsx`, `src/types/detail.ts`,
`fetchStoryDetail.ts`, any reel/feed file, or any plan/reference/migration/seed.

## Toggle + ordering + reduced-motion approach
- **Collapsed by default:** `useState<boolean>(false)`. The header carries
  `data-timeline-toggle="collapsed|expanded"` + `aria-expanded` as the authoritative
  state.
- **Toggle:** the header is a full-width `<button>` (`min-h-[44px]`, top-border divider
  matching the prototype `#trust-toggle`) whose `onClick` flips the state; a chevron
  (`⌄`) rotates 180° on expand. Hit target ≥44px per port-map §3.2.
- **Ordering preserved (no re-sort):** the events are `.map`'d in array order exactly
  as received. The connector-line "last event" branch uses the map position, not a
  sort. A `[...timeline].reverse()` mutation was injected to prove the order test
  fails (see Rule-9 check) — confirming no silent re-sort.
- **Reduced motion (port-map §3.3):** the body uses framer `AnimatePresence` +
  `motion.div` animating `height: 0 → auto` on the lateral easing curve
  (`cubic-bezier(0.22,0.61,0.36,1)` — the prototype `.trust-drawer` `max-height`
  ease, in framer form). Under `useReducedMotion()` the initial/animate/exit heights
  are all `"auto"` and the transition is `{ duration: 0 }` — open/close **snaps**, no
  animated height, mirroring the prototype's `prefers-reduced-motion` rule that
  disables `.trust-drawer` transitions. The chevron rotation also snaps.

## Empty-timeline handling
`if (timeline.length === 0) return null;` — a story with no development events shows
**no drawer at all** (no header onto an empty drawer), matching the `KeyFigureCard` /
`OpposingViewCard` null-omission idiom. Covered by a test.

## Divergences (documented, not drift)
1. **Quote style — DOUBLE quotes**, 2-space, matching Biome config + every existing
   file in the repo (Rule 7/11), as SP0–SP3 did. The global single-quote rule is
   wrong for this repo.
2. **Header label = "HOW IT DEVELOPED"** (not the prototype's separate `STORY TIMELINE`
   toggle label + inner `HOW IT DEVELOPED` heading). The mission names the surface
   "HOW IT DEVELOPED"; I used that single label on the toggle to avoid a redundant
   double heading, keeping the same mono treatment. No data/behaviour change.
3. **`<ol>`/`<li>` semantics** for the ordered event list (it is an ordered
   development sequence) — the prototype uses `<div>`s; the ordered-list element is
   the correct semantic for an index-ordered timeline and renders identically.

## Self code-review findings + fixes
- **[med → fixed] Connector `last:hidden` would not work.** My first pass put the
  connector-line hide on a `<span>` with Tailwind `last:hidden`, but `last:` targets
  the last *child of its parent* — the connector span is not the `<li>`'s last child
  (the `<p>` is), so the line would never hide on the final event. Fixed by computing
  `isLastEvent = eventPosition === timeline.length - 1` and omitting the connector
  span on the last row (prototype `.tl-item:last-child::after { display:none }`).
- **[med → addressed] AnimatePresence exit lingers under jsdom.** framer's
  `AnimatePresence` keeps the exiting drawer body mounted while the close
  height-animation runs; under jsdom's no-op rAF that animation never settles, so the
  event rows stay in the DOM after a collapse tap. This is a test-environment artifact,
  not a production bug (real rAF unmounts on settle; the `overflow-hidden` height-0
  wrapper clips during collapse — the standard framer idiom, matching the prototype's
  `max-height:0` collapse). Resolved by asserting the **round-trip collapse** on the
  authoritative `data-timeline-toggle` / `aria-expanded` state (a no-op toggle would
  leave it `expanded` and FAIL); documented the reasoning inline in the test. The
  initial-collapsed assertion (no exit involved) still asserts zero event rows in the
  DOM.
- **[info] Hit target.** Full-width `min-h-[44px]` button — ≥44px per §3.2.
- No critical/high issues.

## Validation results
- **Biome** (`biome check` on the 2 touched files): `Checked 2 files in 78ms. No fixes applied.` — **0 errors, 0 warnings.**
- **Typecheck** (`tsc --noEmit`, whole project): exit **0** — **0 errors.**
- **Vitest** (`vitest run`, full suite): **15 files / 122 tests passed** (was 14/117 after SP3 — +1 file, +5 tests, **no regressions**).
- **Vitest** (the new file alone): **5/5 passed.**
- **Build** (`npm run build`, static export): exit **0** — `✓ Generating static pages (6/6)`, `✓ Exporting (2/2)`, `/` prerendered static (○). No `.next` cache issue (no `rm -rf .next` needed).
- **Rule-9 mutation check (proves the order test has teeth):** injecting
  `[...timeline].reverse()` into the render made **2 ordering assertions FAIL**
  ("renders ALL events in the received index order" + "preserves a NON-sorted input
  order verbatim"); the file was then restored (verified `reverse()` occurrences = 0).
  The test cannot pass while event ordering is dropped or a defensive re-sort is added.

## DoD check
- ✅ **Drawer starts collapsed** — test asserts `data-timeline-toggle="collapsed"`,
  `aria-expanded="false"`, zero event rows, and no event text in the DOM on mount.
- ✅ **Tapping expands to show all events in index order** — test taps the toggle, then
  asserts state → `expanded` and the rendered `timeline_event_index` + `when` sequence
  equals the input order `[0,1,2]` (and verbatim for a scrambled input — no re-sort).
- ✅ **Tapping again collapses** — second tap returns the authoritative toggle state to
  `collapsed` / `aria-expanded="false"` (a no-op toggle FAILS — Rule 9).
- ✅ **Out-of-order render FAILS the test** — proven by the reverse-mutation check.
- ✅ **Empty timeline renders nothing** — test asserts no toggle, no `<section>`, no
  "HOW IT DEVELOPED" text for `timeline={[]}`.
- ✅ **Reduced motion snaps** — `useReducedMotion()` swaps the height animation to
  `{duration:0}` with `height:"auto"` throughout (no animated height); wired.
- ✅ lint 0, tsc 0, build passes.

**DoD: PASS.**

## Concern / handoff
- The single nuance worth a second look at phase DoD time: the collapse *animation*
  (height → 0 → unmount) can only be eyeballed on a device/simulator — jsdom can't
  exercise the real rAF settle. The toggle *state machine* is unit-proven; the
  animated height feel is what needs human smoke (same class of PENDING as SP2's drag
  feel). Not faked — flagged.
