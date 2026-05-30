# Phase 1: Audio-first karaoke reel (against M0 fixtures)

**Milestone:** M1 — Audio-first karaoke reel MVP
**Status:** Not started
**Estimated effort:** L

## Goal
A runnable Next.js 15 static-SPA reel that plays the **5 real M0 digests** (real TTS audio + word-timed caption JSON + posters, already on disk) as local fixtures — karaoke captions tracking the real audio word-by-word over an ambient wash, finite + swipeable, ending at the "all caught up" finish line — proving the core M1 experience **before any backend exists**.

## Context the sub-agents need
- **No frontend exists yet.** This phase scaffolds it. Port TLDW's `package.json` dependency set + `src/` scaffolding pattern (`reference/reuse-map.md`: Next 15, React 19, Tailwind 4, radix-ui, framer-motion, zustand, supabase, zod) from `~/TLDW-Phase2/tldw/voice-agent-dashboard/`; read the donor before porting (Rule 8). The reel UI itself is **NEW**.
- **The design is the prototype** at `prototype/News20 Prototype/` (`app.js`, `styles.css`, `index.html`, `data.js`) and `reference/prototype-port-map.md` (the authoritative port mapping — read it). Brand is **blip**, audio-first (captions are the hero, poster→ambient wash).
- **M0 fixtures (real) to bundle:** audio `agents/m0/output/audio/digest-{1..5}.mp3`; captions `agents/m0/output/captions/digest-{1..5}.captions.json` (shape: `{digest_id, audio_duration_s, speech_end_s, sentence_count, words:[{word, start_s, end_s, sentence_index, is_highlight}]}`); posters `assets/m0/digest-{1..5}/poster.png` (768×1376 JPEG bytes despite `.png`). Story metadata (headline, segment, anchors, accent) from `prototype/News20 Prototype/data.js`.
- **Tokens are law** — `reference/design-language.md` + the inline `tailwind.config` in the prototype `index.html`. Map them verbatim so class names port.
- **Caption timing is TIME-SLICED, not true forced alignment** (M0 decision: no Whisper). The per-word `start_s/end_s` are estimates. This phase is the cheap early test of whether that feels synced enough (see Open Questions).

## Sub-phases

### Sub-phase 1: Scaffold the SPA shell + design tokens + blip logo
- **Files touched:** `package.json`, `next.config.ts` (`output: "export"`), `tsconfig.json`, `postcss.config.mjs`, `tailwind.config.ts` (every token from the prototype `index.html` `tailwind.config`: colors `primary/secondary/accent/background/surface/text-*/border/caption-highlight/bias-*/seg-*`, fonts Inter/Playfair Display/JetBrains Mono, radius `card 1px`/`control 16px`/`pill`, spacing `safe-t`/`safe-b`), `src/app/layout.tsx`, `src/app/globals.css` (port the effects from prototype `styles.css`: ambient drift, karaoke caption classes, scrims, reduced-motion), `src/components/BlipLogo.tsx` (the wordmark: dot + 3 horizontal waves, from `app.js` `blipSignal`/`blipLogo` + `.blip` CSS), `src/components/PhoneShell.tsx` (393×852 frame, simulated safe-area insets ≈59px/34px, status bar, island, home indicator), `tests/lib/tokens.test.ts`.
- **What ships:** `npm run build` produces a static export; the empty iPhone shell renders at 393×852 with correct safe-areas + the blip wordmark; every design token is a usable Tailwind class.
- **Definition of done:** `npm run build` exits 0 and emits `out/`. A Vitest test asserts `tailwind.config.ts` exposes the token keys (`seg-geopolitics`, `bias-right`, `caption-highlight`, `font-serif`→Playfair, `rounded-card`→1px). Manual visual smoke (flagged, Rule 9): shell + logo render at 393×852. Biome lint passes.
- **Dependencies:** none

### Sub-phase 2: Typed feed contract + M0 fixtures + karaoke selector
- **Files touched:** `src/types/feed.ts` (the **cross-phase contract**: `Story`, `Digest`, `CaptionSentence` with `word_tokens: [{word_text, is_highlight, start_ms, end_ms}]` — aligned to `reference/supabase-schema.md` + `reference/api-contracts.md`), `src/lib/feed/fixtureFeed.ts` (`getFeed()` loading the 5 M0 digests), `src/lib/feed/normalizeM0Captions.ts` (flat M0 `words[]` + `sentence_index` → per-sentence `word_tokens`, seconds→ms), `src/lib/captions/captionState.ts` (pure `captionStateAtTime(track, tMs)` → per-word `'dim'|'spoken'|'active'|'highlight'`), `public/fixtures/{audio,captions,posters}/*` (the 5 M0 mp3s + caption JSONs + posters), `tests/lib/captionState.test.ts`, `tests/lib/normalizeM0.test.ts`.
- **What ships:** a typed `getFeed()` fixture provider returning the 5 M0 stories in the canonical `Story` shape, plus the pure karaoke selector the UI renders from.
- **Definition of done:** Vitest — `captionStateAtTime` returns the correct spoken/active/highlight words at given timestamps against a real M0 caption track, **exactly one highlight per sentence**, no word `active` past `speech_end`; `normalizeM0Captions` preserves the verbatim word sequence (count + order) flat→sentences. `fixtureFeed` returns 5 typed stories. Tests fail if the karaoke selection logic is wrong, not merely on compile (Rule 9).
- **Dependencies:** Sub-phase 1

### Sub-phase 3: The reel surface — chrome + audio-driven karaoke + core gestures
- **Files touched:** `src/components/reel/Reel.tsx` (scroll-snap container + active-index state machine), `ReelStory.tsx` (ambient wash from the story's segment accent + scrims), `ReelChrome.tsx` (FiniteBar, `NN / 30` counter, date, BlipLogo, profile button, segment chip + headline lower-left, speaker label with **fixed** colors ALEX `#6C8CFF` / JORDAN `#C792EA`, per-story progress bar, action row), `KaraokeCaption.tsx` (renders `captionStateAtTime` against a real `<audio>` element's clock), `src/lib/reel/useReelAudio.ts` (audio controller: play/pause, `currentTime`→caption, auto-advance on `ended`), `src/lib/reel/gestures.ts` (framer-motion: swipe up/down = next/prev, tap = pause/play).
- **What ships:** a working reel that plays a fixture digest with captions lighting word-by-word **in sync with the real audio**, swipe up/down moves stories, tap pauses/plays, auto-advance fires when audio ends.
- **Definition of done:** Manual visual smoke (flagged, UI): play digest-1 → captions track the audio word-by-word with one `#FACC15` keyword/sentence, speaker label alternates with fixed colors, progress fills, swipe-up advances + resets, tap pauses; `prefers-reduced-motion` disables drift/transitions. Vitest on `useReelAudio` advance logic (`ended`→next index; last→signals caught-up). Biome passes. **Action row note:** buttons render, but Save/Follow are local state only and Ask/Voice/Detail are deferred no-ops (M2/M3) — see Out of scope.
- **Dependencies:** Sub-phase 1, Sub-phase 2

### Sub-phase 4: First-run audio unlock + finite-loop states
- **Files touched:** `src/components/reel/TapToStart.tsx` (first-tap audio-unlock overlay — iOS muted-autoplay reality), `LoadingSkeleton.tsx` (finite-bar + poster/caption skeletons), `AllCaughtUp.tsx` (`NN / NN` finish line + replay), `ReelError.tsx` (offline/failed-load), state-machine wiring in `Reel.tsx` (`loading → tapstart → playing → caughtup`), `src/lib/reel/preload.ts` (preload next 1–2 audios), `tests/lib/preload.test.ts`.
- **What ships:** the full finite loop — tap-to-start unlocks audio and begins, reaching the last story shows the signature "You're all caught up" screen, loading + error states reachable.
- **Definition of done:** Manual smoke (flagged): first tap unlocks + starts playback (no audio before the gesture); advancing past the last fixture shows All-caught-up, replay returns to story 1; loading + error render on demand. Vitest: `preload` picks the correct next indices; the state-machine transitions are covered. Biome passes.
- **Dependencies:** Sub-phase 3

## Phase-level definition of done
`npm run build` static-exports cleanly and opening the app (`out/` or `npm run dev`) shows the audio-first reel playing the **5 real M0 digests** back-to-back: karaoke captions tracking each digest's real audio word-by-word (one `#FACC15` keyword/sentence), finite `NN / 30` counter + segmented bar, first-tap audio unlock, tap-pause, swipe up/down navigation, auto-advance, ending at the "all caught up" finish line. **Automated:** the Vitest suite (`captionState`, `normalizeM0`, `useReelAudio` advance, `preload`, token config) is green; Biome clean; static export emits `out/`. The "does the audio-first karaoke reel feel right against real audio + time-sliced captions?" judgment is the manual smoke this phase exists to enable.

## Out of scope
- Any backend/Supabase — **fixtures only** (Phase 1b/1c).
- Capacitor / iOS build (Phase 1c); the daily content pipeline (Phase 1d).
- Story Detail + trust layer + typed Q&A (M2); Voice mode + voice-agent onboarding + auth (M3).
- Real per-word forced alignment (M0 timings are time-sliced — Open Q1).
- Persisting Save/Follow (local in-memory state only in M1); Ask/Voice/Detail navigation targets (deferred).

## Open questions
1. **Time-sliced caption timing:** M0's caption JSON is evenly time-sliced, not true forced alignment. This phase is the cheapest test of whether karaoke feels synced enough. If it visibly drifts, raise real forced alignment (master-plan Open Q7) before Phase 1d.
2. **Fixture weight:** bundling 5 mp3s (~6 MB) + posters into `public/fixtures` is dev-only; Phase 1c replaces them with Supabase storage URLs — confirm that's acceptable in the repo (git-lfs?).
3. **Static-export SPA mode** (master-plan Open Q4) — locked here as `output: "export"`.

## Self-critique

**Product lens:** PASS. M1's MVP slice is the auto-play reel; this phase delivers exactly that and nothing from M2/M3 (Detail/trust/voice/onboarding explicitly out of scope — no creep). The brief's headline riskiest assumption (digest quality) was retired in M0; M1's own risk — *does the audio-first karaoke reel feel right against real audio?* — is front-loaded to THIS first phase via real M0 fixtures + manual smoke, the earliest it can be tested. The 90-day 3×/week metric is M4 instrumentation, correctly absent.

**Engineering lens:** PASS. All files sit inside the locked stack (Next 15 static SPA + Tailwind 4 + framer-motion). DoDs are fresh-context-verifiable: pure-function unit tests (`captionStateAtTime`, `normalizeM0`, `preload`, advance) + a token-config test + a build-emits-`out/` check; UI bits are explicitly flagged manual smoke (Rule 9). The cross-phase contract `src/types/feed.ts` is fixed deliberately in SP2 — it's the seam Phase 1b/1c must satisfy (anchored to `supabase-schema.md`), so fixing it early is correct, not premature. No two sub-phases are the same thing (scaffold ≠ data/selector ≠ reel UI ≠ states).

**Risk lens:** PASS with flags. File boundaries: SP3 and SP4 both touch `Reel.tsx` (state-machine wiring) — handled by SP4-depends-on-SP3, not parallel. Test coverage: every sub-phase carries a real unit test except the visual surface, which is flagged manual smoke. Painting-into-a-corner: simulating SP1→4, the feed contract (SP2) and audio-driven karaoke (SP3) are built so Phase 1c swaps only the feed-provider impl, leaving the reel untouched.

**Irreversible sub-phases:** none — no DB, no network writes, no native project; pure local frontend.
