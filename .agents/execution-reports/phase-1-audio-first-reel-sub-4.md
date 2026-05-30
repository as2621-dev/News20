# Phase 1 — Sub-phase 4 execution report: first-run audio unlock + finite-loop states

**Status:** SUCCESS (automated DoD green; visual DoD PENDING-human-smoke per Rule 9)
**Scope:** sub-phase 4 of 4 (the LAST). SP1/SP2/SP3 consumed; SP3's `Reel.tsx` extended + `ReelStory.tsx` `preload`/unlock seam upgraded as briefed. **Did NOT commit** (orchestrator commits at phase end).

---

## What I built

The full finite loop that closes the M1 experience: the iOS first-tap audio-unlock gate, the four state surfaces (loading skeleton, tap-to-start, all-caught-up finish line, error/offline), and the complete reel **status state machine** wired through a pure, unit-tested transition function. Plus the gap-free **audio preload window** (pure + tested) wired into the `<audio preload>` attribute.

### Files touched (all paths absolute)

**New:**
- `/Users/asheshsrivastava/News20/News20/src/components/reel/TapToStart.tsx` — first-tap audio-unlock overlay (ports `#tap-start` / `firstStart()`).
- `/Users/asheshsrivastava/News20/News20/src/components/reel/LoadingSkeleton.tsx` — finite-bar + poster/caption skeletons + `BUFFERING TODAY'S DIGEST…` (ports `enterReelWithLoading`).
- `/Users/asheshsrivastava/News20/News20/src/components/reel/AllCaughtUp.tsx` — `30 / 30` finish line + replay CTA (ports `showCaughtUp`, minus the M3 follow-update card).
- `/Users/asheshsrivastava/News20/News20/src/components/reel/ReelError.tsx` — offline/failed-load screen + retry (ports `showErrorScreen`, minus the M3 "continue with N downloaded").
- `/Users/asheshsrivastava/News20/News20/src/lib/reel/preload.ts` — pure `computePreloadIndices(activeIndex, storyCount, lookahead=2)`.
- `/Users/asheshsrivastava/News20/News20/tests/lib/preload.test.ts` — 11 tests for the preload window.
- `/Users/asheshsrivastava/News20/News20/tests/lib/reel/reelStatus.test.ts` — 17 tests for the `nextReelStatus` transition table.

**Edited (the SP3 seam, as briefed):**
- `/Users/asheshsrivastava/News20/News20/src/components/reel/Reel.tsx` — extended `ReelStatus` union, added `ReelEvent` + the pure exported `nextReelStatus`, wired the full machine (loading/tapstart/error rendering, replaced the caughtup placeholder with `<AllCaughtUp>`, replay + retry handlers, the active-story play-handle registry for in-gesture unlock, preload-window computation).
- `/Users/asheshsrivastava/News20/News20/src/components/reel/ReelStory.tsx` — added `shouldPreload` (→ `preload="auto"|"none"`) and `onRegisterActivePlay` props; the `preload` upgrade + the play-handle registration effect.

**NOT touched** (Rule 3): `src/app/layout.tsx` (see Deferrals), `ReelChrome.tsx` (reused `FEED_TOTAL`/`FEED_START_INDEX` exports — no edit needed), `useReelAudio.ts`, `gestures.ts`, `KaraokeCaption.tsx`, `globals.css`, `page.tsx`, all SP1/SP2 files.

---

## The `nextReelStatus` transition table (the testable seam)

Exported from `Reel.tsx`; the component dispatches every status change through it. Union: `"loading" | "tapstart" | "playing" | "caughtup" | "error"`. Events: `feed_loaded | feed_failed | first_tap | reached_caught_up | replay | retry`.

| from | event | to |
|---|---|---|
| `loading` | `feed_loaded` | `tapstart` |
| `loading` | `feed_failed` | `error` |
| `tapstart` | `first_tap` | `playing` |
| `playing` | `reached_caught_up` | `caughtup` |
| `caughtup` | `replay` | `playing` |
| `error` | `retry` | `loading` |
| *any other pair* | — | **unchanged (guarded no-op)** |

It is a **total function** (defined for all 5×6 pairs; the test walks every pair and asserts a valid result, never a throw). Guarded no-ops are explicitly tested (e.g. a stray `reached_caught_up` while `loading` stays `loading`) so an out-of-order or mis-fired event can never silently corrupt the machine (Rule 9 — the tests fail on a wrong target status, not merely on compile).

---

## How "no audio before the first tap" is enforced (traced)

Two independent guards, both verified by reading the play path end-to-end:

1. **The auto-play effect is gated on `isAudioUnlocked`.** `ReelStory`'s effect is `if (isActive && isAudioUnlocked) playAudio()`. In `loading` no story is mounted; in `tapstart` `isAudioUnlocked` is `false`, so the active story does **not** auto-play. The `<TapToStart>` overlay (z-30) sits above the stories, so the stories' own framer `onTap` can't fire either.
2. **The unlock play happens SYNCHRONOUSLY inside the tap gesture.** `TapToStart.onStart` → `Reel.handleStart`, which (a) sets `isAudioUnlocked=true`, (b) calls `activeStoryPlayRef.current?.()` **in the click call stack** — this is the iOS-required in-gesture `play()` that unlocks the audio element — then (c) moves the machine to `playing`. The active `ReelStory` registers its `playAudio` into that ref (via an effect) the moment it becomes active, so at first tap (story 0, the only active story) the ref holds the right handle. After unlock, all subsequent plays come from the `isAudioUnlocked` effect, so the ref is only load-bearing for the single tapstart→playing transition.

The registry uses **compare-and-clear** deregistration: cross-instance effect/cleanup ordering during auto-advance is not guaranteed, so a `null` cleanup only takes effect if the stored handle still equals the deregistering story's handle (prevents a late cleanup from clobbering the next active story's registration). Low-frequency correctness fix, applied in Step C.

---

## Preload wiring (gap-free auto-advance, port-map §6)

`computePreloadIndices(activeIndex, storyCount, lookahead=2)` returns the next 1–2 in-range indices after the active one — excludes the active index, never negative, never `≥ storyCount`, returns `[]` on the last story (finite — never wraps). `Reel.tsx` builds `preloadIndexSet = {activeIndex} ∪ computePreloadIndices(...)` and passes `shouldPreload={set.has(i)}` to each `ReelStory`; the `<audio>` gets `preload="auto"` for that window and `"none"` otherwise (upgrades SP3's `preload={isActive ? "auto" : "none"}`).

---

## Step B self-review (severity-tagged) + Step C fixes

- **[MED] Cross-instance play-handle race.** The active-story play registry could be nulled by a late cleanup during auto-advance, leaving `handleStart` with no handle. Only reachable if the tapstart→playing tap coincides with a story transition (it can't — tapstart precedes any advance), so impact is theoretical, but **FIXED** with compare-and-clear deregistration (`Reel.registerActiveStoryPlay` + `ReelStory` passing its handle on cleanup).
- **[MED] a11y: `aria-label` on a role-less `<div>` (Biome `useAriaPropsSupportedByRole`).** The loading container carried `aria-busy`/`aria-label` with no role. **FIXED** — added `role="status"` (the buffering semantic, which natively supports both).
- **[LOW] a11y: inner `<svg>` without title (Biome `noSvgWithoutTitle`).** `ReelError`'s glyph svg. **FIXED** — `aria-hidden="true"` on the svg (it's decorative; the screen carries `role="alert"` + text).
- **[LOW] Formatting** (`AllCaughtUp.tsx`, `reelStatus.test.ts`). **FIXED** via `biome check --write`.
- **Reduced motion:** skeleton uses `animate-pulse` + `motion-reduce:animate-none` (verified `motion-reduce:animate-none` compiles into the built CSS); AllCaughtUp's fade-up is framer-motion gated on `useReducedMotion()` (variants collapse to no-offset). `globals.css` already kills `.ambient` drift under the media query. The prototype's `.sk`/`.fade-up`/`.press` classes are NOT in SP1's `globals.css`, so I expressed those affordances via Tailwind utilities + framer instead of editing `globals.css` (Rule 3 — it's not in my file list).
- **No `any`** anywhere; verbose entity-prefixed names; structured `@/lib/logger` events (`reel_audio_unlocked`, `reel_reached_caught_up`, `reel_replay_requested`, `reel_feed_retry_requested`, `reel_feed_load_failed` with `fix_suggestion`). Purity of `nextReelStatus` + `computePreloadIndices` verified (no I/O, no refs).

---

## Step D validation (run myself; all green)

| Check | Result |
|---|---|
| `npx vitest run` | **PASS** — Test Files **6 passed**, Tests **61 passed**. Scope `tests/lib/**` (tokens, captionState, normalizeM0, reel/useReelAudio, **preload** = 11 new, **reel/reelStatus** = 17 new). |
| `npx tsc --noEmit` | **PASS** — exit 0. |
| `npm run lint` (`biome check .`) | **PASS** — exit 0, "Checked 33 files. No fixes applied." |
| `npm run build` | **PASS** — exit 0, "Compiled successfully", "Exporting (2/2)", emits `out/index.html` (12.9 KB). |

Structural assertions on the bundle: all four state components' load-bearing strings are present in the export (`Tap to start your briefing`, `BUFFERING TODAY'S DIGEST`, `caught up`, `today's briefing`) — proving they're wired into the reel, not tree-shaken. `BUFFERING TODAY'S DIGEST…` renders directly into the SSR `index.html`, confirming the machine's initial `loading` state. `motion-reduce:animate-none` present in built CSS.

---

## Step E — Definition of done (per item)

Phase-file SP4 DoD:

1. **Vitest: `preload` picks the correct next indices.** **PASS** — `computePreloadIndices`: start `0 → [1,2]`, last `→ []`, second-last `→ [last]`, excludes active, clamps to `storyCount-1`, never negative, counts 1 & 2, custom lookahead, degenerate inputs. 11 tests, Rule-9 WHY (finite — no wrap past the finish line).
2. **Vitest: state-machine transitions are covered.** **PASS** — `nextReelStatus`: all 6 legal transitions, guarded no-ops, total-function exhaustiveness, full happy-path composition. 17 tests; each pins an exact target status so a wrong transition FAILS.
3. **Biome passes.** **PASS** (exit 0).
4. **Visual smoke** (first tap unlocks + starts, no audio before the gesture; advancing past the last fixture shows All-caught-up; replay → story 1; loading + error render on demand). **PENDING-human-smoke (Rule 9)** — I can't see a browser; automated parts green + structurally asserted, but the interaction feel must be eyeballed.

### Exact human-smoke steps (for the orchestrator/user)
Run `npm run dev` (or serve `out/`) and confirm:
1. **On load** → the loading skeleton (finite bar + shimmer blocks + `BUFFERING TODAY'S DIGEST…`) shows briefly, then the tap-to-start overlay (blip glow + play ring + "Tap to start your briefing").
2. **Before tapping** → **no audio plays** (the iOS gate). Confirm silence.
3. **Tap the overlay** → digest-1 audio starts immediately and the overlay disappears; captions begin tracking.
4. **Auto-advance to the end** (or swipe through) → after digest-5 ends, the **All-caught-up** screen appears: `30 / 30 · DONE`, "You're all caught up.", replay CTA.
5. **Tap replay** → scrolls back to story 1, digest-1 replays from 0.
6. **Error state** → (no UI trigger in M1; reachable by forcing `getFeed()` to reject) the calm offline screen + Retry renders; Retry returns to loading → tapstart.
7. **System reduced-motion ON** → skeleton shimmer stops; caught-up fade-up is instant.

---

## Concerns for the orchestrator's phase-level checks

1. **Full reel boots end-to-end:** mount → `loading` (skeleton in SSR HTML) → feed resolves → `tapstart` → tap → `playing` → … → `caughtup` → replay → `playing`. The machine + both pure seams are unit-covered; the **audio-driven feel** (does the karaoke track real audio, does the first-tap unlock actually start iOS audio) is the human smoke this phase exists to enable — **must be eyeballed in a browser**, ideally the real iOS WebView for the unlock (jsdom/desktop can't validate the iOS gesture requirement).
2. **`ReelStory` gained 2 props** (`shouldPreload`, `onRegisterActivePlay`) beyond the bare "preload upgrade." `onRegisterActivePlay` is structurally required by the resolved decision ("playAudio() the active story" from the unlock handler) — `Reel` owns status/unlock but not the audio element, so the active story must hand up a play handle. Documented in both files. Flagging in case the phase-level slop scan questions the extra prop.
3. **Deferral (documented, Rule 3):** `layout.tsx`'s "audio-unlock context" (port-map §6) was explicitly out of my file list — the unlock state stays in `Reel.tsx` (`isAudioUnlocked` + the play registry) as SP3 left it. A later nicety, not regressed.
4. **Scope trims (port fidelity):** AllCaughtUp omits the "while you were out / 1 followed story has an update" card (M3 follow/timeline data — out of scope); ReelError omits "continue with N downloaded" (implies a partial offline cache that doesn't exist against bundled fixtures). Both noted in the component docstrings. Replay returns to story 0; no error-state UI trigger exists in M1 (only reachable by a `getFeed` rejection).
5. **No commit made** — stage SP4's files (+ SP1/2/3) at phase end.

---

## Return summary

1. **STATUS: SUCCESS**
2. **Files touched:** `src/components/reel/TapToStart.tsx`, `src/components/reel/LoadingSkeleton.tsx`, `src/components/reel/AllCaughtUp.tsx`, `src/components/reel/ReelError.tsx`, `src/lib/reel/preload.ts`, `src/components/reel/Reel.tsx`, `src/components/reel/ReelStory.tsx`, `tests/lib/preload.test.ts`, `tests/lib/reel/reelStatus.test.ts`
3. **Validation:** vitest **PASS** (61/61, 6 files, scope `tests/lib/**`), tsc **PASS** (exit 0), lint **PASS** (Biome exit 0), build **PASS** (exit 0, emits `out/index.html`)
4. **DoD:** preload-indices test **PASS** · state-machine-transitions test **PASS** · Biome **PASS** · visual smoke **PENDING-human-smoke** (Rule 9 — steps above)
5. **Concerns:** full-boot end-to-end + the iOS first-tap-unlock feel need human/iOS smoke; 2 added `ReelStory` props (one structurally required for in-gesture unlock); `layout.tsx` audio-unlock context deferred (documented); AllCaughtUp/ReelError M3 cards trimmed to scope — all detailed above.
