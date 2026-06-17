# Execution report — Phase 7e-1, Sub-phase 1: Retry-on-ready in `useReelAudio.playAudio`

**Date:** 2026-06-16
**Status:** SUCCESS
**Sub-phase:** 1 of 4 ("Retry-on-ready in `useReelAudio.playAudio`")

## What I implemented

In `src/lib/reel/useReelAudio.ts`, `playAudio()` now self-heals when `play()`
loses the media-load race instead of logging once and giving up.

On rejection it classifies the error:
- **Not-allowed** (`DOMException` with `name === "NotAllowedError"`, the iOS
  pre-unlock autoplay block) → keeps the existing `reel_audio_play_rejected` warn
  and arms **no** retry (the tap/unlock path owns recovery).
- **Not-ready / otherwise** (`NotSupportedError` / `AbortError`, or
  `readyState < HTMLMediaElement.HAVE_CURRENT_DATA`) → arms **exactly one** one-shot
  retry via `canplay` (with `loadeddata` as a fallback trigger) that, when fired,
  removes both listeners, clears the guard ref, and calls `play()` again
  (re-checking `audioRef.current` still exists).

Stacking is prevented by a `pendingRetryCleanupRef` (`useRef<(() => void) | null>`):
if a retry is already armed, a second rejection is a no-op.

Key excerpt (the catch block):

```ts
} catch (playError) {
  const isNotAllowed = playError instanceof DOMException && playError.name === "NotAllowedError";
  const isNotReady = !isNotAllowed && audioElement.readyState < HTMLMediaElement.HAVE_CURRENT_DATA;

  logger.warn("reel_audio_play_rejected", { /* unchanged */ });

  if (isNotAllowed || pendingRetryCleanupRef.current !== null) {
    return;
  }
  if (!isNotReady) {
    return;
  }

  const cleanupRetryListeners = (): void => {
    audioElement.removeEventListener("canplay", handleCanPlayRetry);
    audioElement.removeEventListener("loadeddata", handleCanPlayRetry);
    pendingRetryCleanupRef.current = null;
  };
  const handleCanPlayRetry = (): void => {
    cleanupRetryListeners();
    const currentElement = audioRef.current;
    if (currentElement) {
      void currentElement.play();
    }
  };
  audioElement.addEventListener("canplay", handleCanPlayRetry);
  audioElement.addEventListener("loadeddata", handleCanPlayRetry);
  pendingRetryCleanupRef.current = cleanupRetryListeners;

  logger.info("reel_audio_play_retry_armed", { story_index: storyIndex, ready_state: audioElement.readyState, fix_suggestion: "..." });
}
```

Plus a `cancelPendingRetry` `useCallback` (runs the stored cleanup, no-op if none)
and an unmount-only `useEffect` that calls it so a mid-arm unmount cannot leak the
`canplay`/`loadeddata` listeners.

## Files modified
- `src/lib/reel/useReelAudio.ts`

## Divergences from the plan
- **Added `cancelPendingRetry` + an unmount cleanup effect (not strictly listed for
  SP1).** Reason: the SP1 instructions explicitly asked to "leave the
  `pendingRetryCleanupRef` and a small cleanup helper in place so SP3 can wire it"
  and to handle the unmount-leak review concern. The helper had to have at least one
  use or biome flags it as dead code; wiring it to unmount cleanup is the minimal,
  correct use and fixes a real listener leak. **No public contract change** —
  `ReelAudioController` and `playAudio: () => Promise<void>` are unchanged. The
  cancel handle is an internal `useCallback`/ref in the same module, which is the
  cheapest seam for SP3 (same file).
- **Added one `reel_audio_play_retry_armed` info log** (allowed by the brief). The
  richer `retry_succeeded` / `retry_exhausted` logging is left for SP4 to avoid churn.

## Code review findings + fixes (Step B/C)
- **Classification correctness — PASS.** `NotAllowedError` returns before arming;
  the RCA's `NotSupportedError` + `readyState:0` path arms (0 < HAVE_CURRENT_DATA=2).
- **Retry stacking — PASS.** Guarded by `pendingRetryCleanupRef.current !== null`.
- **Listener removed after firing — PASS.** `handleCanPlayRetry` calls
  `cleanupRetryListeners()` first (removes both listeners + nulls ref), then plays.
- **Unmount mid-arm leak — FIXED** via the new unmount cleanup effect.
- **Function declaration order (low).** `cleanupRetryListeners` is declared before
  `handleCanPlayRetry`; each only invokes the other at listener-fire time, after both
  are initialized — no temporal-dead-zone hazard. Verified by biome (clean).
- No `any` used; no critical/high issues remained to fix.

## Validation (Step D) — exact commands + outcomes
- `npx biome check src/lib/reel/useReelAudio.ts` → **PASS** ("Checked 1 file… No fixes applied").
- `npx tsc --noEmit` → **PASS** (no errors; tsc is configured and runs standalone).
- `npx vitest run tests/lib/reel` → **PASS** (6 files, 38 tests).
- `npx vitest run tests/lib/` → 429 passed, **1 pre-existing failure** in
  `tests/lib/app/tabBar.test.tsx` (expects a 4-tab bar; current code renders a
  "Thirty" tab). Confirmed unrelated: `git stash` (my change removed) → the same
  tabBar test still fails. Not caused by SP1, out of this sub-phase's scope.

## Definition of done (Step E): PASS
By code reading: a rejected-not-ready `play()` arms a one-shot `canplay`/`loadeddata`
listener that, on the event, removes both listeners and re-issues `play()` exactly
once; a `NotAllowedError` rejection returns before arming (no retry). Stacking is
guarded. Biome passes. The SP3 unit test that will mechanically prove this is not
yet present (SP3 owns it), as expected by the plan.

## Concerns / notes for the orchestrator

**For SP3 (inactive-cancel + tests):**
- To cancel a pending retry when the story goes inactive, call **`cancelPendingRetry()`**
  inside the existing `isActive=false` effect (`useReelAudio.ts`, the "pause + rewind"
  effect, now ~lines 257-268). It is an in-scope `useCallback` in the same module —
  no import/contract change needed. It is a no-op when nothing is armed.
- The unmount cleanup effect I added already covers the unmount case; SP3 only needs
  the inactive-before-`canplay` case.
- The RCA test sketch binds a fake element to `audioRef.current`. Note the production
  code reads `HTMLMediaElement.HAVE_CURRENT_DATA` (=2) and `DOMException`; both exist
  in jsdom, so the sketch's `readyState=0` + `NotSupportedError` exercises the
  not-ready path, and `readyState=4` + `fire("canplay")` triggers the single retry.

**For SP4 (logging):**
- `reel_audio_play_retry_armed` is already emitted at arm time (info). Add
  `reel_audio_play_retry_succeeded` inside `handleCanPlayRetry` **after** the retried
  `play()` resolves (await it there), and `reel_audio_play_retry_exhausted` if you
  later bound retries (currently a single attempt — see the plan's open question;
  "exhausted" only applies if SP4 introduces N>1). Both should carry `story_index`
  + `fix_suggestion`, matching the existing log style.

## Return to orchestrator
1. **STATUS:** SUCCESS
2. **Files touched:** `src/lib/reel/useReelAudio.ts`
3. **Validation:** PASS — biome clean, tsc clean, reel tests 38/38; the lone
   `tests/lib/app/tabBar.test.tsx` failure is pre-existing and unrelated (verified by stash).
4. **Definition of done:** PASS (verified by code reading; SP3 owns the mechanical test).
5. **Concerns:** SP3 should call `cancelPendingRetry()` in the `isActive=false`
   effect; SP4 should add `retry_succeeded` inside `handleCanPlayRetry` after the
   awaited retry `play()` (and `retry_exhausted` only if it bounds retries N>1).
