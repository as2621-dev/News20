# Phase 1c: Capacitor iOS shell + auth-gated per-user live feed

**Milestone:** M1 — Personalized audio-first karaoke reel MVP
**Status:** Not started
**Estimated effort:** M

## Goal
The Phase-1 reel, now **gated behind auth/onboarding** and fed by a **per-user `daily_feeds` read**, built as a **Capacitor iOS app** that launches in the iOS Simulator and plays *that signed-in user's* personalized daily feed **back-to-back hands-free** — safe-areas, first-tap audio unlock, and gapless auto-advance all correct on the device WebView.

## Re-scope context
M1 is now personalized end-to-end. This phase no longer "swaps fixtures → a single global Supabase feed"; it (1) gates the app on auth + onboarding (from Phase 1e) and (2) reads the per-user feed Phase 1d precomputes into `daily_feeds`. The reel components and the `Story` contract are unchanged — only the routing shell and the feed *query* change. Sub-phases are ordered so the gate (depends only on 1e) ships first and the per-user read (depends on 1d) ships last.

## Context the sub-agents need
- Depends on **Phase 1** (the reel + `src/types/feed.ts` contract + feed-provider seam), **Phase 1e** (auth + onboarding + the `users`/`daily_feeds` schema), and **Phase 1d** (the pipeline that populates `daily_feeds`). The Phase-1 reel was built against a swappable feed provider — this phase swaps the impl (to a per-user `daily_feeds` join) and should touch the reel components **not at all**.
- **`Story` does not change** (`src/types/feed.ts` is authoritative). Only the query changes: from a global `stories` select to a `daily_feeds`→`stories`→current `digests`→`caption_sentences` per-user select ordered by `feed_position`. Prove the seam held with a `git diff` boundary check (reel/karaoke files untouched).
- **iOS/Capacitor realities** (`reference/stack-notes.md`, `reference/prototype-port-map.md`): static export (`output:"export"`) loads inside the native shell; muted autoplay is allowed but **audio autoplay needs a user gesture** (the first tap); `playsinline`; declare `NSMicrophoneUsageDescription` (placeholder now, used by M3 voice); preload the next 1–2 digests for seamless auto-advance.
- Capacitor pins in `reference/stack-notes.md` (`@capacitor/core`, `@capacitor/ios`, `@capacitor/cli` 6.x+). NEW to this project.
- **Target the iOS Simulator** for M1 (no signing). Real-device provisioning + TestFlight is M4.
- **Auth session must persist:** Phase 1e SP2 flips `src/lib/supabase/client.ts` to `persistSession:true` — this phase's per-user reads rely on a live `auth.uid()`.

## Sub-phases

### Sub-phase 1: Auth/onboarding routing gate
- **Files touched:** `src/app/page.tsx` (replace always-mount-reel with the gate), `src/lib/auth/routeGuard.ts`, `src/components/AppRouter.tsx`.
- **What ships:** a client-side gate — unauthenticated → email sign-in (Phase 1e); authed + not onboarded (`users.user_onboarded_at` null) → interest chips (Phase 1e); authed + onboarded → the reel.
- **Definition of done:** a signed-out session routes to sign-in; an authed-not-onboarded session routes to chips; an authed-onboarded session routes to the reel (asserted against a mocked session + `user_onboarded_at`); there is **no flash of the reel** before the gate resolves (Rule 12). A `git diff` shows reel components untouched.
- **Dependencies:** Phase 1e (auth + `users.user_onboarded_at`).

### Sub-phase 2: Add the Capacitor iOS project
- **Files touched:** `capacitor.config.ts` (`appId`, `webDir: "out"`), `package.json` scripts (`build:ios` = `next build` → `cap sync`), generated `ios/` (App), `.gitignore` for `ios/App/Pods` + build artifacts.
- **What ships:** a buildable iOS project wrapping the static export.
- **Definition of done:** `npm run build` (static export to `out/`) then `npx cap add ios` + `npx cap sync ios` complete without error; the `ios/App` workspace exists; `xcodebuild -workspace ios/App/App.xcworkspace -scheme App -sdk iphonesimulator build` compiles. ⚠ generates a native `ios/` project.
- **Dependencies:** Sub-phase 1.

### Sub-phase 3: iOS WebView realities — safe-area, audio unlock, playsinline
- **Files touched:** `ios/App/App/Info.plist` (`NSMicrophoneUsageDescription` placeholder for M3 voice; ATS allowing Supabase https; status-bar/viewport config), `src/components/PhoneShell.tsx` (real `env(safe-area-inset-*)` instead of the simulated 59/34px), the `<audio>` media setup (`playsinline`, no autoplay before the first-tap gesture), Capacitor status-bar/keyboard config.
- **What ships:** the app respects real device safe-areas, unlocks audio on first tap, plays inline.
- **Definition of done:** `npx cap run ios` (or open in Xcode → run) launches in the Simulator showing the reel; safe-areas correct on an iPhone 15-class sim (manual smoke, flagged); first tap unlocks audio and playback begins (no audio before the gesture); captions track the audio.
- **Dependencies:** Sub-phase 2.

### Sub-phase 4: Per-user `daily_feeds` read + gapless back-to-back playback
- **Files touched:** `src/lib/feed/supabaseFeed.ts` (add `getDailyFeed(userId, feedDate)` — join `daily_feeds`→`stories`→current `digests`→`caption_sentences`, order by `feed_position`, return the **same `Story[]` contract**), `src/lib/feed/index.ts` (provider selector → per-user feed for an authed user; fixture provider stays behind a dev flag, not deleted), `src/lib/reel/preload.ts` (prefetch next 1–2 digest audios on iOS), `src/lib/reel/useReelAudio.ts` (gapless auto-advance, no re-unlock mid-session).
- **What ships:** the reel now loads the **signed-in user's** personalized `daily_feeds` (audio + poster from storage), captions still tracking, and plays it back-to-back hands-free.
- **Definition of done:** two seeded users with different interests each load their **own** `daily_feeds` ordered `01..N` (a per-user contract test on `getDailyFeed` returns `Story[]` shape-identical to `src/types/feed.ts`, and the existing `tests/lib/feed/supabaseFeed.test.ts` contract stays green); in the Simulator, starting playback and not touching the screen plays the user's feed consecutively with captions synced, auto-advancing, ending at the `N/N` All-caught-up finish — **no mid-session audio re-prompt** (manual smoke, flagged). A Vitest test covers the preload-window + gapless-advance logic.
- **Dependencies:** Sub-phase 3, **Phase 1d** (`daily_feeds` populated).

## Phase-level definition of done
A Capacitor iOS app that `xcodebuild`-compiles for the Simulator, **gates on auth/onboarding**, reads the signed-in user's **`daily_feeds`**, respects real safe-areas, unlocks audio on first tap, and plays *that user's* personalized finite feed back-to-back hands-free with word-synced karaoke captions to the All-caught-up finish line. **Two seeded users see two different feeds.** **Automated:** SP1 routing-gate test + seam `git diff` check + SP2 build-compiles + SP4 per-user `getDailyFeed` contract test + preload/advance unit test. **Manual smoke (flagged, requires Xcode/Simulator):** the on-device playback loop. ⚠ generates a native `ios/` project.

## Out of scope
- Real-device (non-simulator) provisioning + TestFlight (M4); App Store metadata/signing (M4).
- The daily pipeline itself (Phase 1d populates `daily_feeds`; this phase only reads it).
- Auth/onboarding UI (Phase 1e builds it; this phase only gates on it).
- Push notifications; mic/voice activation (M3); Detail/trust/Q&A (M2).

## Open questions
1. **Apple developer signing:** simulator builds need no signing; a real device needs a team/provisioning profile — confirm before any device (vs simulator) testing. M1 targets the Simulator.
2. **Hosted Supabase reachable from the Simulator** (ATS/https) — confirm the Phase-1b/1e project is hosted (not just local) for on-device fetch.
3. **Feed date / timezone:** which `feed_date` the client requests (device-local vs UTC) when the daily batch may run at a fixed hour — confirm with Phase 1d's schedule.
4. **Empty-feed UX:** a brand-new user whose first `daily_feeds` hasn't been computed yet — show a "building your feed" state vs the recency fallback (`ranking-spec.md` §3 sparse fallback). Confirm.

## Self-critique

**Product lens:** PASS. Delivers the "on a phone, personalized" half of the re-scoped M1 "true when" — open the app, sign in, and watch *your* feed back-to-back. The risky audio-first experience (validated in-browser in Phase 1) is confirmed on the real iOS WebView. No M2/M3 features (no detail, no voice).

**Engineering lens:** PASS. Capacitor + static export is the locked stack decision (master-plan Open Q4). The `Story` seam is preserved — only the query changes to a per-user `daily_feeds` join — proven by the unchanged-reel `git diff` check and the green existing contract test (Rule 9). DoDs are verifiable: routing-gate test, `xcodebuild` compile, `getDailyFeed` contract test, preload unit test; on-device playback is flagged manual smoke (unavoidable for a native UI surface). Sub-phase order resolves the cross-phase dependency: the gate (1e-only) ships first, the per-user read (needs 1d) ships last.

**Risk lens:** PASS with flags. ⚠ SP2 generates a native `ios/` project (reversible by deleting `ios/`, flagged). File boundaries: SP3/SP4 both touch reel media files — sequenced (SP4 after SP3). The per-user read depends on Phase 1d having populated `daily_feeds` — flagged as the one cross-phase wait; the fixture provider stays behind a dev flag so SP1–3 can be built/tested before 1d lands. Painting-into-a-corner: SP1→4 — once gated (SP1) and the iOS shell builds (SP2) with correct WebView realities (SP3), the per-user `daily_feeds` read + gapless playback (SP4) is the capstone.

**Irreversible sub-phases:** none hard-irreversible (`ios/` is regenerable); SP2 flagged for the native-project generation.
