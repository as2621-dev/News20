# Phase 7e-1: Self-healing reel playback (retry play() on ready)

**Milestone:** M7 — Production feed automation & first-run onboarding feed
**Status:** Planned (RCA `.agents/rca/2026-06-16-reel-audio-play-race.md`, 2026-06-16)
**Estimated effort:** S

## Goal
The active reel's narration **always starts on its own** — including when the reel
was reached by a fast scroll (so its `<audio>` was `preload="none"` and unbuffered).
Today `useReelAudio.playAudio` calls `audioElement.play()` exactly once and, when it
rejects because the media is not yet loaded, only logs and gives up — so the reel
stays silent until the user manually leaves and re-enters it (the bounce is a manual
retry after the element buffered). This phase makes playback retry itself when the
element becomes playable, and makes the active element start loading immediately, so
no manual bounce is ever needed.

## Scope guard
ONLY the silent-reel race (RCA dominant cause: state/race). First-load stutter, blank
posters, the `no-cache` upload header, CDN warming, and the missing caption tracks are
explicitly out of scope here — they are phases 7e-2 / 7e-3 / 7e-4. See "Out of scope".

## Sub-phases

### Sub-phase 1: Retry-on-ready in `useReelAudio.playAudio`
- **Files touched:** `src/lib/reel/useReelAudio.ts`
- **What ships:** when `play()` rejects, classify the error — a **not-ready** media
  error (`NotSupportedError` / `AbortError` / `readyState < HAVE_CURRENT_DATA`)
  schedules **one** retry via a one-shot `canplay` (fallback `loadeddata`) listener;
  a **not-allowed** error (`NotAllowedError`, the iOS pre-unlock autoplay block) does
  NOT retry (the existing tap/unlock path owns that). Guard so at most one pending
  retry exists (store the listener in a ref, no-op if one is already armed). The retry
  re-checks the element still exists before calling `play()` again.
- **Definition of done:** new unit test (SP3) proves a rejected-not-ready `play()`
  followed by a `canplay` event re-issues `play()` exactly once; a `NotAllowedError`
  rejection arms no retry. Biome passes.
- **Dependencies:** none

### Sub-phase 2: Eager-load the active element so the retry has something to wait on
- **Files touched:** `src/components/blip/reel/ReelStage.tsx` (audio element +
  activation effect), `src/components/blip/reel/BlipReel.tsx` (preload set)
- **What ships:** the **active** reel's `<audio>` is never `preload="none"` — union
  the active index into the eager set (already true via `preloadIndexSet` at
  `BlipReel.tsx:132`, so confirm + assert), and on activation explicitly call
  `audioElement.load()` before `playAudio()` (`ReelStage.tsx:151-155`) so the fetch
  starts immediately on a fast-scrolled reel instead of lazily. This guarantees a
  `canplay` event will fire for SP1's retry to hook.
- **Definition of done:** code review confirms the active reel always renders
  `preload="auto"` and issues `load()` on becoming active; no change to inactive
  reels' `preload="none"`. Manual in-browser fast-scroll (SP4) plays without bounce.
- **Dependencies:** Sub-phase 1

### Sub-phase 3: Cleanup correctness — no stale or stacked retries
- **Files touched:** `src/lib/reel/useReelAudio.ts`, `tests/lib/useReelAudio.test.tsx` (new)
- **What ships:** the pending one-shot retry listener is removed when the story goes
  inactive (the existing `isActive=false` effect at `useReelAudio.ts:197-207`) and on
  unmount, so a `canplay` that fires after the user has scrolled away never starts
  audio on a now-inactive reel (which would cause two narrations at once). Tests
  encode the intent (Rule 9): (a) rejected-not-ready → `canplay` → retried once;
  (b) retry does not stack on repeated rejections; (c) going inactive before `canplay`
  cancels the retry — `play()` is NOT called again; (d) `NotAllowedError` arms no retry.
- **Definition of done:** all four tests pass after the fix and (a)/(c) fail on the
  pre-fix code. `npm test` green.
- **Dependencies:** Sub-phase 1, Sub-phase 2

### Sub-phase 4: Structured logging + in-browser verification
- **Files touched:** `src/lib/reel/useReelAudio.ts` (log events only)
- **What ships:** structured logs `reel_audio_play_retry_armed` and
  `reel_audio_play_retry_succeeded` / `reel_audio_play_retry_exhausted` with
  `story_index` + `fix_suggestion`, so the silent-reel path is observable in the field
  (complements the existing `reel_audio_play_rejected`). Then drive the real reel in a
  browser (`/verify` or `/debug`): fast-scroll straight to a far reel and confirm
  audio starts with no bounce, and that scrolling away mid-load leaves no orphan audio.
- **Definition of done:** in-browser repro of the original bug now plays on first
  arrival; logs show `armed`→`succeeded` on a throttled network; no double-audio when
  scrolling away during load.
- **Dependencies:** Sub-phase 1, Sub-phase 2, Sub-phase 3

## Phase-level definition of done
Fast-scrolling directly to any reel starts its narration automatically — the
"silent until I bounce to the next reel and back" behaviour is gone. The active
element eagerly loads; a lost `play()` race self-heals via exactly one retry on
`canplay`; retries never stack and never fire on a reel the user has already left;
iOS pre-unlock autoplay blocks are untouched. Unit tests cover retry, no-stack,
inactive-cancel, and not-allowed-no-retry; in-browser repro confirms the fix.

## Out of scope
- **7e-2** — wider/network-aware audio prefetch + poster preload + Wi-Fi
  pre-download (first-load stutter, blank posters).
- **7e-3** — `cacheControl` on `persist.upload_to_bucket` (`persist.py:233`,
  currently `no-cache`) + post-publish CDN warm (repeated re-fetch / cold edge).
- **7e-4** — missing `digest_caption_track_url` on current digests (karaoke).
- Any change to scroll-snap / active-index observation, or to which inactive reels
  preload — inactive reels stay `preload="none"`.

## Open questions
- Should the retry be a single attempt or bounded N? Proposed: **one** retry on the
  first `canplay` — with SP2's eager `load()`, one `canplay` is sufficient; more would
  risk fighting the iOS autoplay policy. Revisit only if field logs show
  `retry_exhausted` on healthy networks.
- jsdom cannot drive real `HTMLMediaElement.play()`/`readyState`; SP3 tests bind a
  hand-rolled fake audio element to `audioRef.current` (per the RCA test sketch). This
  is the established seam — confirm it matches existing reel test style before adding.

## Self-critique

**Product lens:** PASS. Directly fixes a user-reported, reproduced launch blocker
(reel silent on fast-scroll) with no new surface area or user-visible change beyond
"audio just works." Smallest fix that removes the manual bounce.

**Engineering lens:** PASS. Stack-aligned (React hook + client component, Vitest).
Root-cause fix (retry the lost race) not a symptom patch (pre-download). DoDs are
checkable via unit tests with deterministic fake-audio mocks plus one in-browser
confirmation. SP1 owns logic, SP2 guarantees the event SP1 waits on actually fires,
SP3 prevents the obvious regression (double-audio / stacked retries), SP4 makes it
observable — coherent, no padding.

**Risk lens:** PASS with flags. No schema, no irreversible step, client-only. Main
risk is a retry that fires after the user scrolled away (double narration) — mitigated
explicitly by SP3's inactive-cancel test. Second risk is retry-looping against iOS
autoplay policy — mitigated by distinguishing `NotAllowedError` (no retry) from
not-ready (retry). Painting-into-corner: none; 7e-2/7e-3 layer cleanly on top and are
independent.

**Irreversible sub-phases:** none.
