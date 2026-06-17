# RCA: Reel audio silent on fast-scroll (play() races media-load, never retries)

**Date:** 2026-06-16
**Reporter:** user (manual reproduction, twice)
**Status:** Diagnosed — fix proposed, not applied

## Symptom
- Fast-scrolling straight to a far reel (e.g. reel 8) leaves the narration **silent**.
  Scrolling to reel 9 and back to 8 makes it play.
- On first app load, audio "breaks" (stutters) and posters don't appear; ~30 min
  later everything is smooth.

## Reproduction
User-reproduced on device, deterministic on the fast-scroll path:
1. Open the reel feed.
2. Swipe quickly past several reels to a far one (reel 8) without dwelling.
3. Reel 8 shows but audio never starts.
4. Swipe to reel 9, swipe back to reel 8 → audio plays.

Mechanism confirmed by code trace + live-wire inspection (not a headless repro of
the timing race itself — the race is network-dependent):
- All 15 current digests return **HTTP 200** for `digest_audio_url` and
  `digest_ambient_poster_url` (Supabase public storage, ~1.3 MB mp3s). Not a 404.
- Audio objects are served with `cache-control: no-cache`, `cf-cache-status: MISS`
  then `HIT` on a repeat fetch (Supabase CDN is cold per object on first fetch).

## Trace
1. **Symptom:** reel 8's `<audio>` never produces sound on direct fast-scroll.
2. **Proximate cause:** `ReelStage.tsx:151-155` — on `isActive && isAudioUnlocked`,
   the effect calls `void audioController.playAudio()` exactly once.
3. **`playAudio`** (`useReelAudio.ts:116-132`) does `await audioElement.play()`; on
   rejection it **only logs** `reel_audio_play_rejected` and returns — the comment
   says "let the tap handler retry", but on a fast scroll there is no tap.
4. **Why play() rejects:** the element is `preload="none"` until it enters the
   preload window. `BlipReel.tsx:132` computes
   `preloadIndexSet = {activeIndex} ∪ computePreloadIndices(activeIndex, len)`
   (`preload.ts:17`, lookahead=2). Reels skipped during a fast scroll were never in
   the window, so reel 8 flips to `preload="auto"` **at the same render** it becomes
   active. At that instant `readyState===0` and the network fetch has just started,
   so `play()` rejects (NotSupported/Abort on not-ready media).
5. **Why bounce-back fixes it:** all reel sections stay mounted in the scroll stack.
   The failed first `play()` still kicked off the fetch, so the element buffers in
   the background. Going inactive pauses + rewinds (`useReelAudio.ts:197-207`) but
   keeps the buffer; returning to reel 8 re-fires the play effect, and now
   `readyState` is high enough that `play()` succeeds. The bounce **is** the missing
   retry, performed manually.
6. **Root cause:** a single un-retried `play()` that races media-load. No code path
   re-attempts playback once the media becomes playable.

Secondary contributors (different symptoms, same area):
- **`cache-control: no-cache`** — `persist.py:233` uploads with
  `file_options={"content-type": ..., "upsert": "false"}` and no `cacheControl`, so
  Supabase serves `no-cache`. The browser never persists the mp3 across element
  reloads/remounts → re-fetch + stutter; CDN is also cold per object on first fetch.
- **Tiny preload window + zero image preload** — only active+next2 audio preload;
  posters (`ReelStage.tsx:197`) have no preload at all → first-arrival stutter and
  blank posters until fetched.
- **No caption track on any current digest** — `digest_caption_track_url` empty for
  all 15 rows. Separate bug; karaoke word-lighting can't run. Noted, not fixed here.

## Classification
**State / race** (dominant) — `play()` races the media-load lifecycle and there is
no recovery when it loses. Secondary **design** (preload window too small to cover a
fast scroll; no retry architecture) and **environmental** (`no-cache` upload header)
contributors drive the *stutter / first-load* symptoms but not the *silent reel*.

## Root cause
On activation the reel calls `audioElement.play()` exactly once and, if that promise
rejects because the media isn't loaded yet (the normal case for a reel reached by
fast-scrolling, which was `preload="none"` until that very moment), it logs and gives
up. Nothing re-attempts playback when the element subsequently becomes playable, so
the reel stays silent until the user manually re-enters it — which re-fires the play
effect after the element has buffered. The fix is to make playback self-heal: retry
`play()` once the element signals it can play.

## Proposed fix

**Fix:** In `useReelAudio.playAudio`, when `play()` rejects and the element is not
yet playable, attach a **one-shot `canplay`/`loadeddata`** listener that retries
`play()` once (and proactively `load()` the element on activation so the fetch starts
immediately instead of lazily). Guard so only one pending retry exists at a time and
it's cleared when the story goes inactive.

**Files:**
- `src/lib/reel/useReelAudio.ts` (retry-on-ready in `playAudio`; cleanup on inactive)
- `src/components/blip/reel/ReelStage.tsx` (ensure the active element eagerly loads;
  no longer rely on `preload="none"` for the active reel)

**Why this is the root cause and not a symptom:** the silent reel is caused
specifically by an un-retried `play()` losing the load race; the manual bounce works
*only* because it re-issues `play()` after buffering. Adding the automatic retry
removes the exact gap the manual bounce fills — it fixes the cause, not the surface.

**What this fix rules out:** any "reel is silent until I navigate away and back"
failure, for every reel, regardless of how it was reached (fast scroll, deep link,
auto-advance into an un-preloaded reel).

**What this fix does NOT address:** first-load stutter and blank posters
(needs wider prefetch + poster preload), repeated re-fetching (needs `cacheControl`
on upload + CDN warm), and the missing caption tracks. These are the follow-up phases
below.

## Regression test
Encodes the intent (Rule 9): *the active reel must become audible without manual
re-entry, even when play() initially rejects because the media isn't ready.* Fails on
current code (no retry → `play` called once), passes after the fix (retry on
`canplay` → `play` called again and succeeds).

```ts
// tests/lib/useReelAudio.test.tsx
import { act, renderHook } from "@testing-library/react";
import { useReelAudio } from "@/lib/reel/useReelAudio";

function makeFakeAudio() {
  const listeners: Record<string, Array<() => void>> = {};
  let rejectFirst = true;
  return {
    el: {
      readyState: 0,
      paused: true,
      currentTime: 0,
      load: vi.fn(),
      play: vi.fn(() => {
        // First call (media not ready) rejects, like a real fast-scroll arrival.
        if (rejectFirst) {
          rejectFirst = false;
          return Promise.reject(new DOMException("not ready", "NotSupportedError"));
        }
        return Promise.resolve();
      }),
      addEventListener: (ev: string, cb: () => void) => {
        (listeners[ev] ??= []).push(cb);
      },
      removeEventListener: vi.fn(),
    },
    fire: (ev: string) => listeners[ev]?.forEach((cb) => cb()),
  };
}

it("retries play() once the element can play, instead of staying silent", async () => {
  const fake = makeFakeAudio();
  const { result } = renderHook(() =>
    useReelAudio({ storyIndex: 8, storyCount: 15, isActive: true, onEnded: vi.fn() }),
  );
  // bind the fake element to the hook's ref
  (result.current.audioRef as { current: unknown }).current = fake.el;

  await act(async () => {
    await result.current.playAudio(); // first attempt rejects (not ready)
  });
  expect(fake.el.play).toHaveBeenCalledTimes(1);

  await act(async () => {
    fake.el.readyState = 4; // HAVE_ENOUGH_DATA
    fake.fire("canplay"); // element is now playable → retry must fire
  });
  expect(fake.el.play).toHaveBeenCalledTimes(2); // FAILS on current code
});
```

## What this fix does NOT address
- **First-load stutter / blank posters** — preload window is only active+next2 and
  posters have no preload. Needs wider, network-aware prefetch + poster preload.
- **Repeated re-fetch** — `no-cache` upload header (`persist.py:233`) means the
  browser never keeps files; needs `cacheControl` on upload + post-publish CDN warm.
- **Missing caption tracks** — every current digest has empty
  `digest_caption_track_url`; karaoke can't light words. Separate investigation.

## Follow-ups
Proposed phase (4 sub-phases) to ship the full solution:
- **7e-1 — Self-healing playback (this RCA's fix).** Retry-on-ready in
  `useReelAudio`; eager `load()` on active. Highest leverage, smallest change.
- **7e-2 — Network-aware prefetch + pre-download.** Widen audio lookahead; add poster
  `Image()`/`<link rel=preload>`; pre-download the whole feed on Wi-Fi, bounded
  lookahead on cellular (`navigator.connection`).
- **7e-3 — Cache + CDN.** Set `cacheControl: '604800'` on `persist.upload_to_bucket`;
  warm Supabase CDN with a HEAD/GET sweep after publish so the first real user hits a
  warm edge.
- **7e-4 — Caption track gap.** Investigate why `digest_caption_track_url` is empty
  for all current digests; restore karaoke alignment output.
