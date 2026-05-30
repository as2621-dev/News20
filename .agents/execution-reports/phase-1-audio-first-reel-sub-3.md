# Phase 1 — Sub-phase 3 execution report: the reel surface (chrome + audio-driven karaoke + core gestures)

**Status:** SUCCESS (automated DoD green; visual DoD PENDING-human-smoke per Rule 9)
**Scope:** sub-phase 3 of 4. SP1 (scaffold/tokens/BlipLogo) + SP2 (feed contract/fixtures/selector) consumed read-only. **Did NOT commit** (orchestrator commits at phase end).

---

## What I built

The audio-first karaoke reel UI — the highest-risk surface this phase exists to test. A vertical scroll-snap reel that plays the 5 real M0 digests, with karaoke captions tracking each digest's REAL `<audio>` clock word-by-word over an ambient accent wash, finite `NN / 30` chrome, first-tap audio unlock, tap-pause, and auto-advance to a caught-up signal at the last story.

### Files touched (all paths absolute)
- `/Users/asheshsrivastava/News20/News20/src/lib/reel/useReelAudio.ts` — audio controller hook + the PURE `computeNextReelState`.
- `/Users/asheshsrivastava/News20/News20/src/lib/reel/gestures.ts` — framer `onTap` factory (`createTapPlayHandler`) + `useActiveStoryObserver` (IntersectionObserver → active index).
- `/Users/asheshsrivastava/News20/News20/src/components/reel/KaraokeCaption.tsx` — renders the current sentence's words from `captionStateAtTime`.
- `/Users/asheshsrivastava/News20/News20/src/components/reel/ReelChrome.tsx` — FiniteBar (+ `FEED_TOTAL`/`FEED_START_INDEX` constants), date, BlipLogo, profile button, seg chip + headline, speaker label, progress bar, action row.
- `/Users/asheshsrivastava/News20/News20/src/components/reel/ReelStory.tsx` — full-viewport story: ambient wash + blurred drifting poster + scrims; owns its `<audio>` + `useReelAudio`; mounts KaraokeCaption + ReelChrome.
- `/Users/asheshsrivastava/News20/News20/src/components/reel/Reel.tsx` — scroll-snap container + active-index state machine + `reelStatus` + Save/Follow lifted state.
- `/Users/asheshsrivastava/News20/News20/src/app/page.tsx` — overwritten to mount `<PhoneShell><Reel /></PhoneShell>`.
- `/Users/asheshsrivastava/News20/News20/tests/lib/reel/useReelAudio.test.ts` — 6 unit tests for `computeNextReelState`.

---

## Key decisions (as resolved in the brief, implemented verbatim)

### Vertical nav = CSS scroll-snap (NOT framer drag) — Rule-7 conflict resolved
The phase file's file list says `gestures.ts (framer-motion: swipe up/down=next/prev)`, but port-map §3.2 says "scroll-snap is simpler and feels most native on iOS WebView — pick one" and specs `Reel.tsx` as a "scroll-snap container." **I chose CSS scroll-snap** (`snap-y snap-mandatory`, one full-viewport `snap-start` `<section>` per story). An `IntersectionObserver` (`useActiveStoryObserver`, threshold 0.6) derives `activeIndex` — the scroll-snap analogue of the prototype's `S.idx`. `gestures.ts` then owns **tap = pause/play** via framer `onTap` (framer distinguishes tap from scroll by its own movement threshold, so the two never fight). framer `drag="x"` is reserved for the lateral Detail/Voice layers (out of scope). Documented in `gestures.ts` and `Reel.tsx` headers.

### Karaoke driven by the REAL audio clock — no drift
`useReelAudio` runs a `requestAnimationFrame` loop that ONLY *samples* `audioRef.current.currentTime * 1000` each frame (`setCurrentTimeMs`); it never accumulates an elapsed delta. The sampled `currentTimeMs` is passed to `captionStateAtTime(story.caption_sentences, currentTimeMs, story.speech_end_ms)`. **`speech_end_ms` is passed, NOT `audio_duration_ms`** (they differ for digest-2; the selector's invariant (c) needs speech end so no word stays `active` through the trailing ambience). Because the value read IS the audio's own position, it cannot drift vs. what's audible (buffering / rate / rAF throttling all move `currentTime`, which the next sample picks up). The sampler runs only while `isActive && isPlaying`, so paused/inactive stories cost nothing.

### Caption rendering = spread `word.css_class_names`
Each word span gets `className={word.css_class_names}` verbatim from the SP2 selector (`"w"` + `spoken`/`active` + `hl`) — byte-compatible with SP1's ported `.caption .w.*` CSS. No new caption CSS written, no restyle. The one `#FACC15` keyword/sentence and the white current word fall out of the existing CSS. Playfair via the `.caption` class (`font-serif`). Before the first sentence (`current_speaker === null`) an empty caption shell renders. (a11y: the full sentence is announced once via an `sr-only` span; the per-frame word spans are `aria-hidden` so screen readers don't read the flicker.)

### Speaker label — fixed identity colours
`ReelStory` derives `currentSpeaker` from its own clock via `captionStateAtTime(...).current_speaker` and hands it to `ReelChrome`, which maps **ALEX `#6C8CFF` / JORDAN `#C792EA`** (hardcoded `SPEAKER_IDENTITY_COLOR`, NOT segment accents). The selector keeps the sentence sticky through digest-2's post-speech tail, so the last speaker persists.

### Per-story `--accent` cascade
`ReelStory` sets `style={{ "--accent": story.segment_accent_hex }}` on its root; the ambient wash, scrims, finite-bar current segment, seg dot, and Follow-on tint all read `var(--accent)` (globals.css already wired by SP1). Seg chip text uses the per-story accent via inline `color`.

### FiniteBar constants (provenance: `data.js`)
`FEED_TOTAL = 30` and `FEED_START_INDEX = 25` are exported named constants in `ReelChrome.tsx` (where the FiniteBar lives, its only consumer), with a provenance `// Reason:` comment. `feedPosition = FEED_START_INDEX + storyIndex` → the 5 fixtures read **26/30 … 30/30** ("all caught up" = 30/30).

---

## The SP3 → SP4 `reelStatus` seam (foundation laid; SP4 wires the rest)

`Reel.tsx` exports:
```ts
export type ReelStatus = "playing" | "caughtup";
```
- Starts at `"playing"`.
- Flips to `"caughtup"` when auto-advance runs past the LAST story — driven by `computeNextReelState(...).isCaughtUp` inside `handleAudioEnded`.
- Renders a **minimal placeholder** for `"caughtup"` (a centered `NN / NN · done` line) — **NOT** the full AllCaughtUp screen (SP4 builds that).

First-tap audio start is a **plain tap** (`handleFirstTap` sets `isAudioUnlocked`); I did **not** build the TapToStart overlay (SP4) or the audio-unlock layout context (SP4).

---

## Step B — self-review (severity-tagged) + Step C fixes

- **[HIGH] a11y: `aria-label` on a `<div>` (Biome `useAriaPropsForRole` error).** FIXED — replaced with an `sr-only` full-sentence span + `aria-hidden` word-span wrapper (announce-once pattern). Verified `.sr-only` resolves in the built CSS (not a silent no-op).
- **[HIGH] TS2578 unused `@ts-expect-error` on `webkit-playsinline`.** React 19 JSX accepts lowercase-hyphenated attrs, so the directive was dead. FIXED — set the attribute via a typed `Record<string,string>` spread (`IOS_INLINE_AUDIO_ATTRS`); no `any`, no suppression. `playsInline` + `webkit-playsinline` both present (iOS no-fullscreen).
- **[MED] Biome `useExhaustiveDependencies` on the auto-play effect.** Intentional: `playAudio` is a stable `useCallback`; depending on it would re-fire on identity churn, not on the `isActive`/`isAudioUnlocked` state we care about. FIXED placement of a justified `biome-ignore` directly above the `useEffect`.
- **[MED] Biome `noImgElement` (prefers next/image).** Intentional: the poster is a heavily-blurred, `aria-hidden` decorative backdrop in a static export (`images.unoptimized`); next/image adds nothing. FIXED with a justified `biome-ignore` + `// Reason:`.
- **[LOW] Progress bar may rest at ~99% on `ended`** (last rAF sample lands just before duration). Acceptable: the caught-up overlay covers the last story; mid-feed it resets on advance. No change.
- **Purity of `computeNextReelState`:** verified pure (no I/O, no refs) — it is the unit-testable seam; the hook's `ended` handler is its only caller. ✓
- **Reduced motion:** ambient drift + caption colour transition already killed by SP1's globals.css media query; the poster `<img>` drift is additionally gated on `useReducedMotion()` in JS (no extra CSS); `KaraokeCaption` takes a `reduceMotion` belt-and-braces class. ✓
- **No `any`** anywhere; verbose entity-prefixed names; structured `@/lib/logger` events for play-reject / ended / advance / unlock / deferred-action / feed-load-failed, each error/warn with `fix_suggestion`. ✓

---

## Step D — validation (run myself; all green)

| Check | Result |
|---|---|
| `npx vitest run` | **PASS** — Test Files 4 passed, Tests 35 passed. Scope confirmed `tests/lib/**` (tokens, captionState, normalizeM0, **reel/useReelAudio** = 6 new). |
| `npx tsc --noEmit` | **PASS** — exit 0. |
| `npm run lint` (`biome check`) | **PASS** — "Checked 20 files. No fixes applied." 0 errors, 0 warnings. |
| `npm run build` | **PASS** — "Compiled successfully", "Exporting (2/2)", emits `out/` with `out/index.html` (10 KB) + bundled `out/fixtures/{audio,posters}`. Route `/` static (○). |

Built-CSS structural checks: `sr-only`, `pt-safe-t`(59px)/`pb-safe-b`(34px), `scroll-snap-type`/`scroll-snap-align` all present.

---

## Step E — Definition of done (per item)

Phase-file SP3 DoD items:

1. **Vitest on `useReelAudio` advance logic (`ended`→next index; last→caught-up).** **PASS** — `computeNextReelState`: non-last → `{nextIndex: i+1, isCaughtUp:false}`; last → `isCaughtUp:true` (clamped, never loops to 0); edges (count 1; index 0; over-the-end). 6 tests, Rule-9 WHY encoded (finite briefing must reach the finish line EXACTLY at the last story, never skip/loop).
2. **Biome passes.** **PASS** (0 issues).
3. **Build static-exports + emits `out/`.** **PASS** (structural: `out/index.html` exists; `snap-y` container in SSR HTML — per-story chrome hydrates client-side as the feed loads via `useEffect`, the production seam).
4. **Visual smoke** — captions track word-by-word, one `#FACC15`/sentence, speaker alternates w/ fixed colours, progress fills, swipe-up advances+resets, tap pauses, `prefers-reduced-motion` kills drift. **PENDING-human-smoke (Rule 9).** I cannot see a browser; the automated parts are green and structurally asserted, but the "does it feel synced?" judgment — the entire reason this phase exists — must be eyeballed.

**jsdom `<Reel>` render smoke test — deliberately SKIPPED (documented).** jsdom has no `IntersectionObserver` and no `HTMLMediaElement.play` (the exact reason the advance decision was extracted to a pure fn). A render test would mock away every interesting part and assert little beyond "mounts without throwing," violating Rule 9 (test intent, not that-it-renders) and Rule 2 (no speculative scaffolding). The pure advance test + SP2's selector tests + the build's structural assertion cover the load-bearing logic.

### Exact human-smoke steps for the orchestrator/user
Run `npm run dev` (or open `out/index.html` via a static server) and:
1. **First tap** anywhere on the reel → digest-1 audio starts (no audio before the gesture).
2. **Karaoke:** words light dim → white left-to-right in time with the narration; exactly **one `#FACC15` yellow keyword** per sentence; the current word is white.
3. **Speaker label** alternates ALEX (`#6C8CFF`) / JORDAN (`#C792EA`) per sentence; persists through digest-2's trailing tail.
4. **Progress bar** fills `currentTime / duration`; finite top bar reads **26 / 30** on story 1.
5. **Swipe up** → advances to the next story, audio resets + plays from 0, captions reset.
6. **Tap** → pauses; tap again → resumes (captions/progress freeze + resume).
7. **Auto-advance:** let a digest play to the end → it scrolls to the next; on digest-5 end → the caught-up placeholder appears.
8. **Save / Follow** toggle on/off (local only; Follow seeded on digest-1). **Ask/Voice/Share/Profile** are deferred no-ops (log `reel_action_deferred`).
9. **System reduced-motion ON** → ambient + poster drift stop; caption colour snaps without transition.

---

## Concerns SP4 must know

1. **`reelStatus` union — extend it here:** `Reel.tsx` exports `export type ReelStatus = "playing" | "caughtup"`. Add `"loading"` (initial buffer → LoadingSkeleton), `"tapstart"` (TapToStart overlay before first play), `"error"` (offline/failed load → ReelError). The machine I left: init `"playing"`; `handleAudioEnded` sets `"caughtup"` via `computeNextReelState(...).isCaughtUp`. **You wire** `loading → tapstart → playing → caughtup` and the error branch. The `"caughtup"` placeholder block (bottom of `Reel.tsx`'s JSX) is yours to replace with `<AllCaughtUp>`; also handle LEAVING `"caughtup"` on replay/scroll-back (I don't reset it).
2. **Audio element + unlock context:** each `<audio>` lives in `ReelStory.tsx` (one per story), driven by `useReelAudio`. First-tap unlock is currently `Reel.tsx`'s `isAudioUnlocked` state + `handleFirstTap` (set on the active story's tap). When you add the audio-unlock context in `layout.tsx` and the `TapToStart` overlay, replace `handleFirstTap`/`isAudioUnlocked` with the context value and call `playAudio()` inside the overlay's gesture handler. `useReelAudio` already logs `reel_audio_play_rejected` when `play()` is called pre-unlock — your overlay must gate on a real gesture.
3. **Active-index API:** `useActiveStoryObserver({ containerRef, storyCount })` returns the active index off `[data-story-index]` sections (threshold 0.6). `Reel.tsx` owns `scrollContainerRef` and programmatic scroll (`scrollTo({ top: nextIndex * clientHeight })`) for auto-advance — reuse this for replay (scroll to 0) and any state-driven jumps.
4. **Preload seam:** `ReelStory` sets `preload={isActive ? "auto" : "none"}`. I did NOT build the next-1-2 preload queue (`src/lib/reel/preload.ts` is yours). Hook point: in `Reel.tsx` compute which indices to preload off `activeIndex` and pass a `shouldPreload` prop down, or flip the inactive `<audio preload>` to `"metadata"`/`"auto"` for `activeIndex ± 1`.
5. **No commit made** — stage these 8 files at phase end.

---

## Return summary

1. **STATUS: SUCCESS**
2. **Files touched:** `src/lib/reel/useReelAudio.ts`, `src/lib/reel/gestures.ts`, `src/components/reel/KaraokeCaption.tsx`, `src/components/reel/ReelChrome.tsx`, `src/components/reel/ReelStory.tsx`, `src/components/reel/Reel.tsx`, `src/app/page.tsx`, `tests/lib/reel/useReelAudio.test.ts`
3. **Validation:** vitest **PASS** (35/35, scope `tests/lib/**`), tsc **PASS** (exit 0), lint **PASS** (Biome 0 issues), build **PASS** (exit 0, emits `out/` + `out/index.html`)
4. **DoD:** advance test **PASS** · Biome **PASS** · build-emits-`out/` **PASS** · visual smoke **PENDING-human-smoke** (Rule 9 — list above)
5. **Concerns:** the `reelStatus` seam (extend the union + wire loading/tapstart/error + replace the caughtup placeholder + reset-on-replay), audio element/unlock location, active-index API, preload seam — all detailed above.
