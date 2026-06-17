# Execution report — Phase 7e-1, Sub-phase 3: Cleanup correctness — no stale or stacked retries

**Date:** 2026-06-16
**Status:** SUCCESS
**Sub-phase:** 3 of 4 ("Cleanup correctness — no stale or stacked retries" + unit tests)

## What I implemented

### Part A — inactive-cancel wiring (`src/lib/reel/useReelAudio.ts`)
Added a single `cancelPendingRetry()` call into the existing `isActive=false`
"pause + rewind" effect, before pausing, and added `cancelPendingRetry` to that
effect's deps. This closes the race where a `canplay`/`loadeddata` fires AFTER the
user scrolled away and would otherwise re-issue `play()` on a now-inactive reel
(double narration). SP1's mechanism was untouched — only the cancel call + dep added.

```ts
// When this story stops being active, pause + rewind so re-entry replays it.
useEffect(() => {
  if (isActive) {
    return;
  }
  // Reason: cancel any armed one-shot play() retry FIRST. If the user scrolled
  // away while the element was still buffering, a canplay/loadeddata firing after
  // this point would re-issue play() on a now-inactive reel — starting a second
  // narration over the new active reel. Cancelling here closes that race.
  cancelPendingRetry();
  const audioElement = audioRef.current;
  if (audioElement) {
    audioElement.pause();
    audioElement.currentTime = 0;
  }
  setCurrentTimeMs(0);
}, [isActive, cancelPendingRetry]);
```

### Part B — unit tests (`tests/lib/reel/useReelAudioRetry.test.tsx`, new)
Placed alongside the existing reel tests (`tests/lib/reel/`). All four cases:
(a) retry-on-ready, (b) no-stack, (c) inactive-cancel, (d) not-allowed-no-retry.
Verbose `test_*` names; Rule-9 WHY comments on every test.

**Harness divergence (important):** the RCA sketch used `renderHook` from
`@testing-library/react`, but that package is **NOT a project dependency** and is
used nowhere in the suite (verified: `node_modules/@testing-library` absent, zero
`renderHook` references). Per Rule 11 (match codebase conventions, don't fork) I used
the established reel-test harness instead — React 19 `react-dom/client` `createRoot`
+ `react`'s `act` (exactly as `allCaughtUp.test.tsx` / `firstRunBanner.test.tsx`),
with a tiny `ReelAudioHarness` component that captures the controller. The fake
element is bound via `audioRef.current` per the sketch.

Key test mechanics (the fake element + the four assertions):

```tsx
function makeFakeAudio(initialPlayResult: Promise<void>): FakeAudio {
  const listeners: Record<string, Listener[]> = {};
  let nextPlayResult = initialPlayResult;
  const play = vi.fn(() => {
    const result = nextPlayResult;
    nextPlayResult = Promise.resolve(); // consumed → next call resolves (now-ready)
    return result;
  });
  // addEventListener/removeEventListener record into `listeners`; el cast through
  // unknown as a deliberate partial HTMLAudioElement stand-in for jsdom.
  return { el, fire, listenerCount, play, removeEventListener, setNextPlayResult };
}

// (a) first play() rejects NotSupportedError → fire("canplay") → play() called 2x,
//     listeners removed.
// (b) two not-ready rejections → still 1 listener per event → one canplay → 1 retry.
// (c) playAudio() arms; renderHarness(false) cancels (listenerCount→0); late
//     canplay does NOT call play() again (stays at 1).
// (d) NotAllowedError → no listener armed; canplay does NOT call play() again.
```

## Files created / modified
- **Modified:** `src/lib/reel/useReelAudio.ts` (cancel call + dep in the inactive effect — minimal)
- **Created:** `tests/lib/reel/useReelAudioRetry.test.tsx`

(Note: the SP3 plan named `tests/lib/useReelAudio.test.tsx`, but the existing reel
tests live in `tests/lib/reel/` and `tests/lib/reel/useReelAudio.test.ts` already
exists for `computeNextReelState`. Filename chosen to avoid overwrite and to sit
alongside its peers, per the sub-phase instruction "match where existing reel tests
live.")

## Divergences from the plan
1. **Harness:** `react-dom/client` `createRoot` + `act` instead of
   `@testing-library/react` `renderHook` (not a dependency — see above). Same
   behaviour exercised; no new dependency added.
2. **Test filename/location:** `tests/lib/reel/useReelAudioRetry.test.tsx` (see note).

## Code review findings + fixes (Steps B/C)
- **Does inactive-cancel run before a late canplay? — PASS.** `cancelPendingRetry()`
  runs synchronously in the inactive effect, which fires on the `isActive: false`
  re-render; test (c) proves the retry listeners are gone (count 0) before the late
  `canplay` and `play()` stays at 1.
- **Do tests genuinely fail on pre-fix logic? — PASS (proved, see Step E).**
- **Flaky timing? — NONE.** Fully event-driven (synchronous `fire()`), no real or
  fake timers; promises are awaited inside `act`.
- **Biome `noAssignInExpressions` (low, FIXED).** My fake's `addEventListener` used
  `(listeners[ev] ??= []).push(...)`; biome flagged it (even though the prod file
  uses the same form). Rewrote to an explicit `const registered = … ; push; assign`.
- **Unhandled rejections (low, none).** The first `play()` rejection is awaited
  inside `playAudio`'s try/catch; the per-call default resets `nextPlayResult` to a
  resolved promise so the retry's `play()` doesn't dangle.

## Validation (Step D) — exact commands + outcomes
- `npx biome check src/lib/reel/useReelAudio.ts tests/lib/reel/useReelAudioRetry.test.tsx`
  → **PASS** ("Checked 2 files… No fixes applied") after the `noAssignInExpressions` fix.
- `npx tsc --noEmit` → **PASS** (no output, no errors).
- `npx vitest run tests/lib/reel/useReelAudioRetry.test.tsx` → **PASS** (1 file, 4 tests).
- `npx vitest run tests/lib/reel` → **PASS** (7 files, 42 tests; 38 prior + my 4).
  No NEW failures. The known `tests/lib/app/tabBar.test.tsx` failure is outside
  `tests/lib/reel` and not exercised by this scope.

## Definition of done (Step E): **PASS**
- All four tests pass after the fix (4/4).
- (a) and (c) **provably fail on pre-fix code:** I `git stash push`-ed ONLY
  `src/lib/reel/useReelAudio.ts` (reverting to the committed pre-SP1 hook), re-ran
  the new test file → **3 failed, 1 passed**:
  - `test_active_reel_retries_play_once_when_media_becomes_ready` (a) — **FAILED** (play called once, no retry).
  - `test_going_inactive_before_canplay_cancels_the_retry` (c) — **FAILED** (no armed retry / no cancel path).
  - `test_repeated_not_ready_rejections_do_not_stack_multiple_retries` (b) — also FAILED (no retry mechanism at all).
  - `test_not_allowed_error_arms_no_retry` (d) — PASSED (pre-fix never retried anything, so trivially holds).
  Then `git stash pop` restored my changes; verified the file is byte-identical to a
  pre-stash backup (`diff -q` → matches), `cancelPendingRetry` present at the inactive
  effect (line 266) + unmount effect (line 281), and the full suite is green again.
  **Tree is NOT left stashed.**

## Concerns / notes for SP4
- The `isActive=false` effect now depends on `cancelPendingRetry` (a stable
  `useCallback([])`), so its identity never changes — no extra effect churn.
- SP4 (logging) should add `reel_audio_play_retry_succeeded` inside
  `handleCanPlayRetry` after the awaited retry `play()`; if it does, the no-stack
  test (b) and retry test (a) still hold (they assert call counts + listener
  teardown, not log text). No test change anticipated for SP4 unless it bounds
  retries N>1, which would touch the no-stack assumptions.
- Tests bind a partial fake `HTMLAudioElement`; if SP4 reads additional element
  members on the retry path, extend `makeFakeAudio` accordingly.

## Return to orchestrator
1. **STATUS:** SUCCESS
2. **Files touched:** `src/lib/reel/useReelAudio.ts`, `tests/lib/reel/useReelAudioRetry.test.tsx`
3. **Validation:** PASS — biome clean, tsc clean, new file 4/4, `tests/lib/reel` 42/42,
   no NEW failures (known `tabBar.test.tsx` is outside this scope, not run/affected).
4. **Definition of done:** PASS — 4/4 pass; (a) and (c) proven to fail on pre-fix code; scope green.
5. **Concerns:** harness uses `react-dom/client` (no `@testing-library/react` dep);
   test filename is `useReelAudioRetry.test.tsx` under `tests/lib/reel/`. See SP4 notes above.
