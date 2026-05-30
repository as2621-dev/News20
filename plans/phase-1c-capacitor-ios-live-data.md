# Phase 1c: Capacitor iOS shell + live Supabase data on device

**Milestone:** M1 — Audio-first karaoke reel MVP
**Status:** Not started
**Estimated effort:** M

## Goal
The Phase-1 reel, now fed by the Phase-1b Supabase `getFeed()`, built as a **Capacitor iOS app** that launches in the iOS Simulator and plays the 5 seeded digests **back-to-back hands-free** — safe-areas, first-tap audio unlock, and gapless auto-advance all correct on the device WebView.

## Context the sub-agents need
- Depends on **Phase 1** (the reel + `src/types/feed.ts` contract + feed-provider seam) and **Phase 1b** (`getFeed()` + seeded Supabase). The whole point is that the Phase-1 reel was built against a swappable feed provider — this phase swaps the impl and should touch the reel components **not at all**.
- **iOS/Capacitor realities** are spelled out in `reference/stack-notes.md` and `reference/prototype-port-map.md`: static export (`output:"export"`) loads inside the native shell; muted autoplay is allowed but **audio autoplay needs a user gesture** (the first tap); `playsinline`; declare `NSMicrophoneUsageDescription` (placeholder now, used by M3 voice); preload the next 1–2 digests for seamless auto-advance.
- Capacitor pins in `reference/stack-notes.md` (`@capacitor/core`, `@capacitor/ios`, `@capacitor/cli` 6.x+). NEW to this project.
- **Target the iOS Simulator** for M1 (no signing). Real-device provisioning + TestFlight is M4.

## Sub-phases

### Sub-phase 1: Swap fixture feed → Supabase feed
- **Files touched:** `src/lib/feed/index.ts` (provider selector → `supabaseFeed`), the reel's data entry wiring in `src/app/*`, `.env.local` (Supabase URL/anon key), fixture provider guarded behind a dev flag (not deleted).
- **What ships:** the reel in the browser now loads the 5 seeded digests from Supabase (audio + poster from storage), captions still tracking.
- **Definition of done:** with Supabase env set, `npm run dev` loads 5 stories from the network (verify via a test that the active provider is the Supabase impl, and via the network panel), audio/poster from storage URLs, karaoke unchanged. A `git diff` shows **only** provider/env files changed — proving the Phase-1 seam held (reel components untouched).
- **Dependencies:** Phase 1, Phase 1b

### Sub-phase 2: Add the Capacitor iOS project
- **Files touched:** `capacitor.config.ts` (`appId`, `webDir: "out"`), `package.json` scripts (`build:ios` = `next build` → `cap sync`), generated `ios/` (App), `.gitignore` for `ios/App/Pods` + build artifacts.
- **What ships:** a buildable iOS project wrapping the static export.
- **Definition of done:** `npm run build` (static export to `out/`) then `npx cap add ios` + `npx cap sync ios` complete without error; the `ios/App` workspace exists; `xcodebuild -workspace ios/App/App.xcworkspace -scheme App -sdk iphonesimulator build` compiles. ⚠ generates a native `ios/` project.
- **Dependencies:** Sub-phase 1

### Sub-phase 3: iOS WebView realities — safe-area, audio unlock, playsinline
- **Files touched:** `ios/App/App/Info.plist` (`NSMicrophoneUsageDescription` placeholder for M3 voice; ATS allowing Supabase https; status-bar/viewport config), `src/components/PhoneShell.tsx` (real `env(safe-area-inset-*)` instead of the simulated 59/34px), the `<audio>` media setup (`playsinline`, no autoplay before the first-tap gesture), Capacitor status-bar/keyboard config.
- **What ships:** the app respects real device safe-areas, unlocks audio on first tap, plays inline.
- **Definition of done:** `npx cap run ios` (or open in Xcode → run) launches in the Simulator showing the reel; safe-areas correct on an iPhone 15-class sim (manual smoke, flagged); first tap unlocks audio and playback begins (no audio before the gesture); captions track the audio.
- **Dependencies:** Sub-phase 2

### Sub-phase 4: Gapless back-to-back playback on device
- **Files touched:** `src/lib/reel/preload.ts` (extend: prefetch next 1–2 digest audios on iOS), `src/lib/reel/useReelAudio.ts` (gapless auto-advance, no re-unlock mid-session), a simulator build sanity check.
- **What ships:** the full hands-free finite loop on iOS — 5 digests play back-to-back, auto-advancing without stutter or a second audio-unlock prompt, ending at All-caught-up.
- **Definition of done:** in the Simulator, starting playback and not touching the screen plays all 5 digests consecutively with captions synced, auto-advancing, ending at the `5/5` finish line — **no mid-session audio re-prompt** (manual smoke, flagged). A Vitest test covers the preload-window + gapless-advance logic.
- **Dependencies:** Sub-phase 3

## Phase-level definition of done
A Capacitor iOS app that `xcodebuild`-compiles for the Simulator, launches showing the audio-first reel **fed live from Supabase**, respects real safe-areas, unlocks audio on first tap, and plays the 5 seeded digests back-to-back hands-free with word-synced karaoke captions to the All-caught-up finish line. **Automated:** SP1 provider/seam check + SP2 build-compiles + SP4 preload/advance unit test. **Manual smoke (flagged, requires Xcode/Simulator):** the on-device playback loop. ⚠ generates a native `ios/` project.

## Out of scope
- Real-device (non-simulator) provisioning + TestFlight (M4); App Store metadata/signing (M4).
- The daily content pipeline (Phase 1d — this phase runs on the 5 seeded digests).
- Push notifications; mic/voice activation (M3); Detail/trust/Q&A (M2).

## Open questions
1. **Apple developer signing:** simulator builds need no signing; a real device needs a team/provisioning profile — confirm before any device (vs simulator) testing. M1 targets the Simulator.
2. **Hosted Supabase reachable from the Simulator** (ATS/https) — confirm the Phase-1b project is hosted (not just local) for on-device fetch.

## Self-critique

**Product lens:** PASS. Delivers the "on a phone" half of M1's "true when" (open the app on a phone, watch back-to-back). No M2/M3 features. The risky audio-first experience (validated in-browser in Phase 1) is now confirmed on the real iOS WebView — the right place to catch iOS-specific audio/gesture issues.

**Engineering lens:** PASS. Capacitor + static export is the locked stack decision (master-plan Open Q4). DoDs are verifiable: provider-swap `git diff` check, `xcodebuild` compile, preload unit test; on-device playback is flagged manual smoke (unavoidable for a native UI surface, Rule 9). SP1 deliberately proves the Phase-1 seam held (reel files untouched). No sub-phase cements something that should stay flexible.

**Risk lens:** PASS with flags. ⚠ SP2 generates a native `ios/` project (reversible by deleting `ios/`, but flagged). File boundaries: SP3/SP4 both touch reel media files — sequenced (SP4 after SP3). Painting-into-a-corner: simulate SP1→4 — once the feed is live (SP1) and the iOS shell builds (SP2) with correct WebView realities (SP3), gapless playback (SP4) is the capstone; nothing earlier blocks it.

**Irreversible sub-phases:** none hard-irreversible (`ios/` is regenerable); SP2 flagged for the native-project generation.
