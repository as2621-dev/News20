# Execution report — Phase 7e-1, Sub-phase 2: Eager-load the active element

**Date:** 2026-06-16
**Status:** SUCCESS
**Sub-phase:** 2 of 4 ("Eager-load the active element so the retry has something to wait on")

## What I implemented

### Point 1 — active reel never `preload="none"` (verify-only, NO change)
Confirmed by reading, NOT changed (Rule 3):
- `BlipReel.tsx:132` builds `preloadIndexSet = new Set([activeIndex, ...computePreloadIndices(activeIndex, stories.length)])` — the active index is unconditionally unioned in.
- `BlipReel.tsx:364` passes `shouldPreload={preloadIndexSet.has(storyIndex)}`.
- The active story always has `storyIndex === activeIndex`, so `preloadIndexSet.has(activeIndex)` is always `true`.
- `ReelStage.tsx:337` renders `preload={shouldPreload ? "auto" : "none"}` — nothing downstream overrides it.

Conclusion: the active reel ALWAYS renders `preload="auto"`. The active index can never render `preload="none"`, so per the brief I made **no change to BlipReel.tsx**.

### Point 2 — explicit `load()` kick on activation (ReelStage.tsx)
In the activation effect (`ReelStage.tsx`, the `[isActive, isAudioUnlocked]` effect), before calling `playAudio()`, I read `audioController.audioRef.current` and call `.load()` only when the element exists AND `readyState === HTMLMediaElement.HAVE_NOTHING` (=0). This kicks the fetch immediately on a fast-scrolled reel so a `canplay`/`loadeddata` event is guaranteed to fire for SP1's retry-on-ready listener.

```ts
// Reason: a reel reached by a fast scroll was preload="none" and flips to
// preload="auto" at the same render it becomes active, so readyState is still
// HAVE_NOTHING and the first play() loses the load race. Explicitly kick the
// fetch here when the element hasn't started loading so a `canplay` is
// guaranteed to fire for useReelAudio's retry-on-ready to hook (phase 7e-1).
// Guarded on readyState===HAVE_NOTHING so we never restart an already-buffered
// download or interrupt in-flight playback.
// biome-ignore lint/correctness/useExhaustiveDependencies: playAudio is stable; intentionally omitted.
useEffect(() => {
  if (isActive && isAudioUnlocked) {
    const audioElement = audioController.audioRef.current;
    if (audioElement && audioElement.readyState === HTMLMediaElement.HAVE_NOTHING) {
      audioElement.load();
    }
    void audioController.playAudio();
  }
}, [isActive, isAudioUnlocked]);
```

The `HAVE_NOTHING` guard ensures `load()` only fires on an element that hasn't begun loading — never restarting an already-buffered download or interrupting in-flight playback (which is exactly the bounce-back case where the element already buffered and `readyState >= HAVE_CURRENT_DATA`). Point 3 (inactive reels stay `preload="none"`, lookahead unchanged) is satisfied by not touching BlipReel or preload.ts.

## Files modified
- `src/components/blip/reel/ReelStage.tsx` (activation effect only)
- `src/components/blip/reel/BlipReel.tsx` — NOT modified (point 1 already held)

## Divergences from the plan
- None. The brief allowed editing BlipReel only if the active index could render `preload="none"`; it cannot, so BlipReel was left untouched (surgical, Rule 3).

## Code review findings + fixes (Step B/C)
- **Active reel renders `preload="auto"`? — PASS.** Traced `preloadIndexSet` always contains `activeIndex`; no override. No fix needed.
- **`load()` only fires when needed? — PASS.** Guarded on `readyState === HAVE_NOTHING` and on element existence. On the bounce-back / already-buffered path `readyState >= HAVE_CURRENT_DATA (2) > 0`, so `load()` is skipped. Inactive reels never enter the `isActive` branch, so they are untouched.
- **Double-load with the hook's logic? — PASS / none.** SP1's hook does NOT call `load()` (it only attaches a one-shot `canplay`/`loadeddata` retry listener and re-issues `play()`). This `load()` is the single trigger that makes that listener fire on a `HAVE_NOTHING` element; on already-loading/loaded elements it's a no-op, so there is no competing/duplicate load.
- **TS strict / no `any` — PASS.** `audioRef.current` is `HTMLAudioElement | null`; `HTMLMediaElement.HAVE_NOTHING` is a standard DOM constant. No `any`.
- Severity of all findings: none above informational. No critical/high to fix.

## Validation (Step D) — exact commands + outcomes
- `npx biome check src/components/blip/reel/ReelStage.tsx src/components/blip/reel/BlipReel.tsx` → **PASS** ("Checked 2 files… No fixes applied").
- `npx tsc --noEmit` → **PASS** (no output, no errors).
- `npx vitest run tests/lib/reel tests/components` → **PASS** — `tests/components` does not exist; reel coverage is `tests/lib/reel` → 6 files, 38 tests passed. No `ReelStage`/`BlipReel` component tests exist (jsdom can't drive `HTMLMediaElement`), consistent with the plan.
- Known pre-existing `tests/lib/app/tabBar.test.tsx` ("Thirty" tab) failure is outside the run scope and was not triggered. **No NEW failures.**

## Definition of done (Step E): PASS
- Active reel always renders `preload="auto"`: PASS (verified via BlipReel `preloadIndexSet` always containing `activeIndex`).
- Issues `load()` on becoming active when not already loaded: PASS (`readyState === HAVE_NOTHING` guard in the activation effect).
- Inactive reels' `preload="none"` unchanged: PASS (BlipReel + preload.ts untouched).

## Concerns / notes for the orchestrator
- The fix's value depends entirely on SP1's retry-on-ready listener (already shipped) firing on the `canplay`/`loadeddata` this `load()` guarantees — verified SP1 listens for exactly those events.
- In-browser fast-scroll verification (no bounce) is SP4's responsibility; this sub-phase ships the load-kick that makes that path possible and validates it via biome/tsc/unit tests only.
- The `HAVE_NOTHING` guard intentionally does NOT re-`load()` an element that errored mid-load (readyState may have advanced). That's acceptable here: a fresh active arrival on an unstarted element is the targeted race; recovery from a failed in-progress load is out of this phase's scope (7e-2/7e-3 cache/CDN work).

## Return to orchestrator
1. **STATUS:** SUCCESS
2. **Files touched:** `src/components/blip/reel/ReelStage.tsx` (BlipReel.tsx verified, not modified)
3. **Validation:** PASS — biome clean (2 files), tsc clean, `tests/lib/reel` 38/38. No NEW failures; the known `tests/lib/app/tabBar.test.tsx` failure is out of run scope and untouched.
4. **Definition of done:** PASS
5. **Concerns:** depends on SP1's `canplay`/`loadeddata` retry (present); in-browser fast-scroll confirmation is SP4.
