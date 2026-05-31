# Prototype → Production Port Map

**Why this doc exists:** The vanilla-JS prototype in `prototype/News20 Prototype/` (`index.html`, `app.js`, `data.js`, `styles.css`, `ui-design-decisions.md`) is the locked visual + interaction spec for **blip** (repo codename "News20"), an audio-first AI news digest iPhone app. This doc maps every prototype surface, token, and hard interaction to the production build so the React/Capacitor port survives without re-deriving the design.

**Target stack (`reference/stack-notes.md`):** Next.js 15 (React 19) + Tailwind 4 + framer-motion, **static export (`output: "export"`)**, wrapped in **Capacitor** for the iOS App Store. No Next server components / API routes / image optimization — all dynamic data is **Supabase-direct client calls** + a remote API on Vercel for the RAG/voice brain.

**When to update:** When a surface ships (mark it ✅ ported), when a token is refined, or when the Supabase schema lands and a table name changes.

> **Sources of truth this doc threads together:** `reference/design-language.md` (tokens), `reference/poster-pipeline.md` (the ambient wash + safe zone), `reference/ui-design-brief.md` (deliverable shape), `reference/stack-notes.md` (Capacitor/iOS realities), `reference/reuse-map.md` (TLDW reuse — limit new code), `reference/api-contracts.md` (shared TS/Pydantic shapes), and the prototype's own `ui-design-decisions.md`.
>
> **⚠ Schema dependency:** This doc cites Supabase tables by name (`segments`, `stories`, `digests`, `caption_sentences`, `detail_chunks`, `story_trust`, `story_timeline`, `story_qa`, `story_topics`, `suggested_questions`, `follows`, `saves`, `player_signals`, `interests`, `user_interest_profile`) as the agreed production data model. The canonical column definitions belong in **`reference/supabase-schema.md`** — that file does not exist yet. **Create it before the data sub-phases start**; field names below are the proposed contract, prefixed per `reference/conventions.md` (`story_id`, never `id`).

---

## 0. The four pivots the prototype already made (context for every row below)

1. **Brand: News20 → "blip".** A *blip* is a radar/audio signal. Wordmark = lowercase `blip` (Inter 800), where the **"i" tittle is a dot + 3 white horizontal radar waves** rippling right. A standalone dot+ring `.blip-mark` exists for square/icon spots. See §4 (Blip Logo).
2. **Audio-first reel.** Karaoke captions are the hero (Playfair Display; sentence dim → current word white → exactly **one `#FACC15` yellow keyword/sentence**); the poster recedes to an ambient drifting duotone wash. **The reel is rendered LIVE client-side** from (TTS audio + word-timed caption JSON + ambient poster) — **NOT a pre-rendered MP4**. See §3.1 (caption engine).
3. **Chip-based onboarding** (replaces a checkbox grid): a tappable category → subcategory → sub-subcategory `interests` tree. *(Voice-agent onboarding was DROPPED 2026-05-30 — onboarding is chip-only, shipped in `plans/phase-1e-auth-onboarding-interest-profile.md`. §5 is now the in-news Voice-mode Gemini Live contract.)*
4. **Email-only passwordless auth** (Sign-in-with-Apple removed to dodge entitlement/review overhead). Maps to Supabase email OTP / magic link.

> **Reel rendering note — supersedes `api-contracts.md` + `reuse-map.md` where they assume MP4.** `api-contracts.md` still models `digest_mp4_url` / `digest_caption_track_url`, and `reuse-map.md` lists a Remotion video project as build-new. The audio-first pivot makes the **reel** a live client composite: `<audio>` element + caption JSON + ambient poster image. Remotion/MP4 is now only relevant for **share-out** clips, not the in-app reel. Flag for reconciliation (Rule 7): keep `digests.digest_audio_url` + `caption_sentences` as the live-reel contract; treat any `digest_mp4_url` as a separate share artifact.

---

## 1. App shell / chrome → the layer system

The prototype mounts everything into a single `#app` node and stacks **layers** (`.layer-reel` base, `.layer-detail` pushes from right `translateX(100%)`, `.layer-voice` pushes from left `translateX(-100%)`) plus full-screen `.overlay-screen`s (onboarding, loading, following, caught-up, error, profile). When a lateral layer opens, `.device.lateral-open .layer-reel` dims + scales to `scale(0.94) brightness(0.45)` as a depth cue.

**Production mapping:**
- The iPhone-frame chrome (`.device`, `.island`, `.home-indicator`, `.statusbar` in `styles.css`) is **prototype-only scaffolding** — it exists to render in a browser. **Drop it** in the Capacitor build; the real device + status bar replace it. Keep only the **safe-area inset values** (§3.3, §6).
- `src/app/layout.tsx` — root layout: fonts, Tailwind globals, `<SafeAreaProvider>`, the reduced-motion media query, the audio-unlock context (§6).
- `src/app/(reel)/page.tsx` — the reel route is the app's home; detail/voice are **lateral layers over it**, not separate routes (preserves the "story opens, not a new tab" feel from `ui-design-brief.md` §6). Model them as framer-motion `AnimatePresence` panels in a `<LayerStack>` shell component, not Next route transitions (route transitions would unmount the reel + kill audio).
- `src/components/shell/LayerStack.tsx` — owns the z-order, the reel dim/scale-back, and the lateral translateX (§3.3).
- **No persistent bottom tab bar** (`ui-design-brief.md` §5.1) — navigation is gesture-first; the one discreet entry is the top-right profile button.

---

## 2. Surface table (every screen `app.js` builds)

Function names in the "Prototype source" column are the `app.js` builders. Component names are proposed; paths assume `src/app/` (routes) and `src/components/` (everything else). "Tables" are the Supabase reads the surface needs.

| # | Surface | Prototype source (`app.js`) | Proposed React component(s) | File path | Data it needs (Supabase) | Key states |
|---|---|---|---|---|---|---|
| 1 | **Onboarding splash** (value prop: "30 stories. 30 minutes. Caught up.") | `startOnboarding` → `onbStep(step 0)` | `OnboardingSplash` | `src/components/onboarding/OnboardingSplash.tsx` | none (static); `interests` prefetched for next step | first-run only; CTA `Get started` |
| 2 | **Chip interest onboarding** (tappable category tree → interest profile) | replaces `voiceProfileStep`/`VP_TURNS` (scripted) | `OnboardingFlow` + `InterestChips` + `CustomInterestChip` | `src/components/onboarding/OnboardingFlow.tsx` | `interests` (read: category→subcategory→sub-sub tree); **writes** `user_interest_profile` | chips lazy-expand · strict toggle · custom interest. **Voice-agent onboarding DROPPED 2026-05-30 — chip-only (shipped in `plans/phase-1e-auth-onboarding-interest-profile.md`).** |
| 3 | **Email sign-in** (passwordless magic link) | `onbStep(step 2)` | `EmailSignIn` | `src/components/onboarding/EmailSignIn.tsx` | Supabase auth (`signInWithOtp({ email })`) | empty · invalid-email (blush inline err) · sending · sent ("check your inbox") |
| 4 | **Loading skeleton** (digest buffering) | `enterReelWithLoading` | `ReelSkeleton` | `src/components/reel/ReelSkeleton.tsx` | `digests` count for the finite bar | shimmer skeleton; `BUFFERING TODAY'S DIGEST…` |
| 5 | **Reel (home)** | `mountReel`, `renderStory`, `renderFiniteBar`, `renderActions`, `tick`/`paintCaption` | `Reel`, `ReelStory`, `FiniteBar`, `KaraokeCaption`, `ActionRow`, `SpeakerLabel`, `TapToStart`, `CoachPeek` | `src/app/(reel)/page.tsx` + `src/components/reel/*` | `digests` (audio url, duration) JOIN `stories` (headline, segment) JOIN `segments` (label, accent); `caption_sentences` (word tokens + `start_ms`/`end_ms`); `follows`/`saves` for action state | tap-to-start · playing · paused · advancing · all-caught-up · per-story progress |
| 6 | **Story Detail** (swipe right) + trust strip + expandable timeline + key figure + opposing view + pinned Q&A | `openDetail`, `buildDetail`, `biasBar`, trust drawer toggle | `StoryDetail`, `TrustStrip`, `BiasBar`, `StoryTimelineDrawer`, `KeyFigureCard`, `OpposingViewCard`, `QaThread`, `QaComposer`, `SuggestedQuestionChips` | `src/components/detail/*` | `stories` + `detail_chunks` (Playfair body) + `story_trust` (coverage L/C/R, outlet_count, blindspot, opposing_view) + `story_timeline` (when/what) + key-figure fields on `stories`/`detail_chunks` + `suggested_questions` + `story_qa` (persisted turns) | reveal-staggered entrance · timeline collapsed/expanded · Q&A idle/thinking/grounded/refusal · focus-Ask (deep-link from reel) |
| 7 | **Voice mode** (swipe left) | `openVoice`, `voicePermission`, `voiceMicDenied`, `voiceConversation` | `VoiceMode`, `VoicePermissionGate`, `VoiceConversation`, shared `VoiceOrb`/`Waveform`/`TranscriptLine` | `src/components/voice/*` | `stories` (the active `story_id`'s source set scopes RAG); writes `story_qa` + `player_signals` (`signal_swiped_left_voice`) | permission-prompt · listening · responding · mic-denied · ended |
| 8 | **Profile sheet** | `openProfile` (+ `row()` menu builder) | `ProfileSheet`, `ProfileMenuRow` | `src/components/profile/ProfileSheet.tsx` | `follows` count, `saves` count, streak from `player_signals`; auth user | open/close sheet; menu rows route to Following / Saved / replay-onboarding / voice / error |
| 9 | **Following / "what's new since you last watched"** | `openFollowing` | `FollowingView`, `FollowedStoryCard` | `src/components/following/FollowingView.tsx` | `follows` JOIN `stories` JOIN `story_timeline` (latest item = the "new development"); `has_update_since_last_watch` per `api-contracts.md` `FollowState` | empty ("nothing followed yet") · list with `● NEW` / `NO CHANGE` markers |
| 10 | **All-caught-up 30/30** (signature finish line) | `showCaughtUp` | `AllCaughtUp` | `src/components/reel/AllCaughtUp.tsx` | `digests` total (the `N/N`); `follows` with updates for the "while you were out" card | the reward screen; CTAs `following-update`, `replay` |
| 11 | **Error / offline** | `showErrorScreen` | `ConnectionErrorScreen` | `src/components/shell/ConnectionErrorScreen.tsx` | cached/downloaded `digests` count (for "continue with N downloaded") | offline · retrying · continue-with-cached |
| — | **Toast** (shared) | `toast()` | use **`sonner`** (already in TLDW `package.json` per `reuse-map.md`) | `src/components/shell/` (provider in layout) | none | transient |

**Segment colour-coding** survives the port verbatim via the `seg-*` tokens (§3.4): `segments.segment_accent_hex` is set on a CSS variable `--accent` per story (exactly as `setAccent()` does), cascading to the wash, segment chip, speaker dot, progress bar, and key-figure card. Keep the per-story single-accent rule (`poster-pipeline.md` law 3).

---

## 3. Port notes — the hard interactions

### 3.1 Karaoke caption engine — drive off REAL audio, not a fake timer

**Prototype (`app.js`):** `tick(ts)` runs a `requestAnimationFrame` loop against a fixed `S.duration = 13000ms`. `sentenceBounds()` invents per-sentence time windows *proportional to word count* (`(acc/total) * S.duration`), and `paintCaption()` interpolates a fake `active` word index inside each window. There are no real timestamps — it's a plausible mock.

**Production — bind to the `<audio>` element's clock + forced-alignment timestamps:**
- The reel is a **live composite**: one `<audio src={digest_audio_url} playsinline preload="auto">` + the ambient poster + the caption overlay. **Do not** pre-render to MP4 for the in-app reel.
- Drive the karaoke loop off `audioRef.current.currentTime` (×1000 → ms), **not** a wall-clock RAF delta. Use `requestAnimationFrame` only to *sample* `currentTime` each frame; never to *accumulate* elapsed time (that drifts vs. the audio).
- Replace `sentenceBounds()` (computed) with the **real per-word `start_ms` / `end_ms`** from `caption_sentences.word_tokens` (the forced-alignment output — `reuse-map.md` ports TLDW `forced_alignment.py`). Each token: `{ token_text, start_ms, end_ms, is_highlight_keyword }`.
- The current word = the token whose `[start_ms, end_ms)` contains `currentTime_ms`. Words before it → `.spoken` (white/50%); the current → `.active` (white); the one keyword per sentence → `.hl` (`#FACC15`), unchanged by spoken/active state (see `.caption .w.hl.active` in `styles.css`). The dim base stays at `rgba(255,255,255,0.30)`.
- The current **sentence** = the `caption_sentences` row whose `[sentence_start_ms, sentence_end_ms)` contains `currentTime_ms`. On sentence change, swap the rendered tokens and the **speaker label** (`ALEX`/`JORDAN`, alternating identity colour — prototype uses `ALEX:#6C8CFF`, `JORDAN:#C792EA`).
- **Per-story progress bar** = `currentTime / duration`. **Auto-advance** fires on the audio `ended` event (replaces the `S.elapsed >= S.duration` branch in `tick()`), preloading the next story's audio for gap-free advance (§6).
- **Pause/play** toggles `audio.pause()` / `audio.play()`; the RAF caption sampler stops/starts with it.
- Proposed shape: a `useKaraoke(audioRef, captionSentences)` hook returning `{ currentSentenceIndex, currentTokenIndex }`; `KaraokeCaption` consumes it. Keep the CSS classes `.w`, `.spoken`, `.active`, `.hl` (or their Tailwind equivalents) so the visual contract is byte-identical.

### 3.2 Gestures — port threshold-swipes to framer-motion drag-to-follow + scroll-snap

**Prototype (`attachGestures` + `attachBackSwipe`):** raw `pointerdown/move/up`. It (a) locks an axis once movement exceeds 10px, (b) on `pointerup` classifies: `tap` (`ax<12 && ay<12 && dt<280`) → `togglePlay()` / first-tap `firstStart()`; vertical `ay>56` → `next()`/`prev()`; horizontal `ax>56` → `openDetail()`/`openVoice()`. `ui-design-decisions.md` §10 **explicitly calls threshold-gestures a prototype shortcut** and recommends finger-following drag in React.

**Production:**
- **Reel vertical = next/prev:** use **CSS scroll-snap** (`snap-y snap-mandatory`, one full-height `snap-start` story per viewport) for native momentum (`ui-design-brief.md` §6 "snap-scroll reel"), **or** a framer-motion `drag="y"` with `dragSnapToOrigin` + an `onDragEnd` velocity/offset threshold that commits to the next/prev story. Pick one (Rule 7) — scroll-snap is simpler and feels most native on iOS WebView; reserve framer drag for the lateral layers. Keep the `bounce()` rubber-band when already at the first story.
- **Lateral right = Detail, left = Voice:** **framer-motion `drag="x"` drag-to-follow.** The layer tracks the finger (`x` motion value), and `onDragEnd` commits open/closed by offset + velocity (`offset.x > 70 || velocity.x > threshold`). This replaces the binary `ax>56` threshold and the CSS-transition-only open/close — it's the §10 upgrade. Mirror `.layer-detail` (`translateX(100%)→0`) and `.layer-voice` (`translateX(-100%)→0`) as the drag constraints; the reel dim/scale-back (§3.3) animates off the same drag progress.
- **Tap = pause/play + first-tap audio unlock:** a tap handler on the reel surface that (first time) runs the audio-unlock (§6) then `play()`, and thereafter toggles play/pause. framer-motion distinguishes tap from drag via its own threshold — wire `onTap` for play/pause and `onDragEnd` for navigation so they don't fight (the prototype hand-rolled this with the `ax/ay/dt` check).
- **Back-swipe from Detail** (`attachBackSwipe`: `dx>70 && scrollTop<10`) → the same framer drag-to-close on `.layer-detail`, gated on the scroll container being at the top so it doesn't fight vertical reading scroll.
- All hit targets ≥44px (`act-btn` is `44×44` in `styles.css`); respect title-safe insets (§6).

### 3.3 Lateral transitions, staggered reveal, reduced motion

- **Lateral layer transitions:** `.layer-detail`/`.layer-voice` use `transform: translateX(...)` with `transition 420ms cubic-bezier(0.22,0.61,0.36,1)`. In framer-motion, this is the panel's `animate`/`exit` (`x: "100%"` / `x: 0`) with `transition={{ duration: 0.42, ease: [0.22,0.61,0.36,1] }}`. The **reel dim + scale** (`.device.lateral-open .layer-reel { scale(0.94) brightness(0.45) }`) is a sibling `motion.div` driven by the lateral panel's open state (or its live drag progress, §3.2).
- **Staggered `.reveal` entrance** (Detail): prototype adds `.in` to `.reveal` items at `120 + i*70` ms. Port to a framer-motion **stagger container** (`staggerChildren: 0.07`, `delayChildren: 0.12`) with child variants `{ hidden: { opacity:0, y:14 }, in: { opacity:1, y:0, transition:{ duration:0.52, ease:[0.22,0.61,0.36,1] } } }`. Keep the `.fade-up` keyframe for the caught-up / following cards as a simple `initial/animate`.
- **`prefers-reduced-motion`:** `styles.css` disables every animation/transition under the media query (ambient drift, orb pulse, reveals, lateral transitions, caption colour transition, blip glow, skeleton shimmer). In React, read it once (`useReducedMotion()` from framer-motion) and: snap layers instantly, skip stagger, paint captions without colour-transition, render the ambient wash static, drop the typewriter in voice onboarding. `swapStory()` already has a `reduced` fast-path — preserve that behaviour (instant story swap, no translateY).

---

## 4. Token mapping — inline `tailwind.config` → real `tailwind.config.ts`

The prototype's inline `tailwind.config` (in `index.html`) is the bridge: every design-language token is a theme key so class names port **unchanged**. Below is the full set to reproduce in `tailwind.config.ts` (Tailwind 4: also expose via `@theme` in `globals.css` if using the CSS-first config). Every listed class must keep working: `bg-background`, `text-caption-highlight`, `font-serif`, `rounded-card`, `seg-geopolitics`, `bias-right`, `pt-safe-t`, etc.

```ts
// tailwind.config.ts — mirrors prototype index.html inline config (design-language.md tokens)
import type { Config } from "tailwindcss";

const config: Config = {
  theme: {
    extend: {
      colors: {
        primary: "#3B82F6",            // actions, active, follow
        secondary: "#D1D4BD",          // muted sage surface
        accent: "#E8B7BC",             // soft blush highlight
        background: "#020617",         // near-black canvas — app base
        surface: "#D1D4BD",            // light detail-view cards (sparing)
        "text-primary": "#FFFFFF",
        "text-secondary": "#A1A1AA",
        border: "#D1D4BD",
        "caption-highlight": "#FACC15", // the ONE yellow keyword/sentence
        "bias-left": "#3B82F6",
        "bias-center": "#A1A1AA",
        "bias-right": "#E8B7BC",
        "seg-geopolitics": "#EF4444",
        "seg-markets": "#22C55E",
        "seg-tech": "#22D3EE",
        "seg-sport": "#F59E0B",
        "seg-wildcard": "#E8B7BC",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        serif: ['"Playfair Display"', "Georgia", "serif"],
        mono: ['"JetBrains Mono"', "monospace"],
      },
      borderRadius: { card: "1px", control: "16px", pill: "9999px" },
      spacing: { "safe-t": "59px", "safe-b": "34px" }, // see §6 — switch to env(safe-area-inset-*)
    },
  },
};
export default config;
```

**Token notes:**
- **Fonts:** load Inter / Playfair Display / JetBrains Mono via `next/font` (self-host for the static export — Capacitor has no network guarantee at first paint). `font-sans` = chrome/headlines, `font-serif` = **reel hero captions + Detail reading body** (the prestige move), `font-mono` = all trust stats/counters/timestamps. The caption-as-Playfair choice is a **flagged deviation** from `design-language.md` (which implies Inter for captions) — `ui-design-decisions.md` §5 asks for sign-off; keep it unless overruled.
- **Radius:** `rounded-card` = `1px` (sharp/editorial), `rounded-control` = `16px`, `rounded-pill` = `9999px`.
- **`--accent` CSS variable:** the prototype overrides `--accent` per story on `.device` (`setAccent`). In React, set it on the reel root via `style={{ "--accent": segment.segment_accent_hex }}`; the wash, `seg-dot`, progress, `act-btn.follow-on`, and key-figure card all read `var(--accent)`. The static `seg-*` Tailwind tokens are for fixed colour-coding (chips/labels); `--accent` is for the dynamic per-story cascade. Keep both.
- **`bias-*`** drive the coverage bar (`biasBar()` reads `BIAS.{left,center,right}` in `app.js` → `data.js`). In production these come from `story_trust` counts; the colours stay the tokens above.
- **`#FACC15` and `#3B82F6` are reserved** (caption highlight / actions) and must never be a segment accent (`poster-pipeline.md` §8). `bias-right` and `seg-wildcard` are both `#E8B7BC` — intentional (the blush). Flagged contrast caveat (`ui-design-decisions.md` §6): `#E8B7BC` is fine for large text/fills/the bias bar but **not** small thin mono on `#020617`.

---

## 5. In-news Voice-mode Gemini Live contract  — *(voice-agent onboarding DROPPED 2026-05-30)*

> **⚠ Voice-agent onboarding was DROPPED 2026-05-30 — onboarding is chip-only (`plans/phase-1e-auth-onboarding-interest-profile.md`).** The onboarding *behaviours* in this section (the two-way voice interview, niche-down-by-voice, the live profile panel, function-call profile extraction) are **obsolete**, kept only for history. What remains live is the **Gemini Live transport contract** below — reused by **in-news Voice mode** (§2 row 7, `plans/phase-3b-m3-in-news-voice-mode.md`), which keeps the prototype's orb + waveform *visual* template (only the brain changes). The scripted prototype `VP_TURNS`/`voiceProfileStep` is fully superseded by the chip onboarding.

**Behaviour to build:**
1. **Mic folded INTO the orb — no separate mic button.** Tap the orb to pause/resume the conversation. **Orb animating = listening; orb still = paused.** This removes the prototype's separate `#vp-mic` "tap to answer" button. The same `VoiceOrb` (brand-white variant `.orb-brand`) + `Waveform` template is shared with in-news Voice mode (§2 row 7) — **one component, two mounts.** The `.orb.listening` pulse-ring and `.orb.responding` throb classes carry over.
2. **On-screen tappable category chips driven by the hierarchical `interests` table** (category → subcategory → sub-subcategory). These are a **scaffold**, not a checkbox grid: they hint what blip can cover and let a user tap to seed/confirm a branch while talking. Render from `interests` (the prototype's flat `INTERESTS`/`VP_LABEL` arrays become a real 3-level read). Tapping a chip is equivalent to saying it — it feeds the same profile extraction.
3. **Niche-down follow-ups.** When a top-level interest is detected (e.g. *Sport*), blip asks the narrowing question (*which team?*) and drills into the `interests` hierarchy (Sport → subcategory league → sub-subcategory team). This is the two-way that a checkbox grid can't do.
4. **~30–60s, genuinely two-way.** Not 3 fixed turns — the conversation adapts to what the user says, bounded to ~30–60s so onboarding stays fast.
5. **Live interest-profile panel.** Keep the prototype's lighting-up tags (`.interest-tag` → `.in` → `.lit`) and the header *"YOUR PROFILE · TOP WORLD STORIES ALWAYS IN"* (the guaranteed-world-tier promise, `ui-design-decisions.md` §2). Tags light up as the agent extracts signals.
6. **Production wiring — Gemini Live + function-calling extraction.** Wire to the **same Gemini Live agent** as in-news Voice mode (one orb template, one WSS brain). Extract the interest profile from the transcript via **function-calling** (e.g. a `record_interest(category_path, weight)` tool the model calls as it detects interests), then persist to `user_interest_profile`. This replaces the canned `VP_TURNS`/`extract()` entirely.

**Gemini Live contract (port verbatim — see memory `news20-gemini-live-tts-contract.md`):**
- Frontend uses a **raw WebSocket** (no JS SDK). Model `gemini-2.5-flash-native-audio-preview-12-2025`, voice `Charon`.
- **Mint an ephemeral token** server-side: `POST v1alpha/auth_tokens`, header `x-goog-api-key`, body `{uses:1, expireTime, newSessionExpireTime}` **only** (do NOT lock setup via `bidiGenerateContentSetup`). Token in `.name`.
- WS endpoint MUST be `…GenerativeService.BidiGenerateContentConstrained` (NOT `BidiGenerateContent`) when using an `auth_tokens/…` token; pass via `?access_token=` query param; v1alpha.
- Client still sends a `setup` frame (model `models/<id>`, `responseModalities:["AUDIO"]`, voice, `systemInstruction`, `tools.functionDeclarations` = the interest-extraction tool, input/output transcription `{}`) and waits for `{setupComplete}` before audio.
- **Greeting nudge:** auto-VAD waits for user audio — force blip's first line with a `clientContent` user turn (`turnComplete:true`).
- Audio: input **16kHz** mono PCM16 LE (downsample in JS — AudioContext ignores the requested rate); output **24kHz** mono PCM16 (ring-buffer ≥2 chunks).
- Server frames arrive as string | Blob | ArrayBuffer — normalize to text before `JSON.parse`. Route on `serverContent.modelTurn.parts[].inlineData.data` (audio), `.inputTranscription/.outputTranscription.text`, `.turnComplete`, `toolCall.functionCalls[]`, `goAway`, `error`.
- `uses:1` token → **guard double-connect** (React 19 StrictMode mounts twice). Function round-trip reply: `{toolResponse:{functionResponses:[{id,name,response:{result}}]}}`.
- **iOS:** request mic via Capacitor + declare `NSMicrophoneUsageDescription` **before** opening the WSS (§6).

**Onboarding flow order** (replacing `onbStep`): splash → email sign-in → **interest chips** → loading skeleton → reel — **chip-only; voice onboarding dropped 2026-05-30** (`plans/phase-1e-auth-onboarding-interest-profile.md`). *(The Gemini Live contract above is for in-news Voice mode, not onboarding.)*

---

## 6. iOS / Capacitor realities (`reference/stack-notes.md`)

- **Static SPA export:** `output: "export"` → no server components / API routes / `next/image` optimization. All reads are **Supabase-direct client calls**; the RAG/voice brain is a **remote API on Vercel** (the Python worker), reached over HTTPS. Keep the binary thin.
- **Safe-area insets:** the prototype hard-codes `~59px` top (Dynamic Island) and `~34px` bottom (home indicator) as `safe-t`/`safe-b` spacing and `.safe-top`/`.safe-bottom`. In the real shell, **switch these to `env(safe-area-inset-top/bottom)`** (with the prototype values as fallbacks) so it adapts across devices. Add `viewport-fit=cover` to the meta viewport. Keep the **title-safe inset** discipline (`poster-pipeline.md` §2: ~54px L/R, ~96px top/bottom keep-out for overlay glyphs + hit targets).
- **Muted-autoplay → first-tap audio unlock:** iOS WebView allows muted autoplay but **audio autoplay needs a user gesture**. The prototype's `TapToStart` overlay (`#tap-start` / `firstStart()`) is exactly this gate — port it as `TapToStart`, and on first tap call `audio.play()` *inside* the gesture handler to unlock the element (then the karaoke loop + auto-advance run freely). Store the unlocked state in the audio-unlock context.
- **`playsinline`:** every audio/media element gets `playsinline` (+ `webkit-playsinline`) so it never goes fullscreen.
- **Mic permission:** declare `NSMicrophoneUsageDescription` in `Info.plist`; request via Capacitor (`@capacitor/microphone` or the Permissions API) **before** opening the Gemini Live WSS. The prototype gates this with `voicePermission` → `localStorage("n20-mic")`; production uses the real Capacitor permission result. The `voiceMicDenied` calm fallback ("read & ask by text instead") ports as-is.
- **Gemini Live over WSS:** see §5. The ephemeral-token mint happens on the Vercel API (keeps the key off-device); the WS connects directly from the WebView.
- **Audio preloading for gap-free auto-advance:** preload the **next 1–2** stories' `digest_audio_url` (`<audio preload="auto">` or a small prefetch queue) so `ended` → next story has no audible gap (`stack-notes.md` Capacitor gotcha, retargeted from video to audio).

---

## 7. Grounded Q&A + refusal — in-context grounding, identical visual contract

> **⚠ Re-scoped 2026-05-31 (supersedes the "real RAG" framing below).** No vector store. A News20 story's grounding corpus is tiny (per-story, single-source, "<100s read" — `s1` = 3 `detail_chunks`), so the whole corpus is loaded **directly into the LLM context** and the model is constrained to answer only from it; the verification stage still gates every claim. PORT `agents/pipeline/stages/verification.py` + adapt `agents/chat/*`, but **NOT** `agents/rag/*` / Pinecone. The endpoint contract + the visual contract below are unchanged. See `plans/phase-2b-m2-grounded-interrogation.md`.

**Prototype (`resolveAnswer`):** canned. Exact suggested-question match → `st.answers[q]`; free text matched against a curated per-story `topics` list → closest canned answer; off-topic → the mandatory refusal string. `askQuestion()` renders a thinking state, then a grounded bubble **with citation chips** or the **`⌀ CAN'T ANSWER FROM SOURCE` blush refusal** card.

**Production (`reference/reuse-map.md` + `news20-gemini-live-tts-contract.md`):**
- Replace `resolveAnswer()` with **in-context grounding + the verification stage** — load the active `story_id`'s whole corpus (`detail_chunks` + `story_timeline` + digest + any single-source body) into the prompt, constrain the model to answer **only** from it, then PORT `agents/pipeline/stages/verification.py` to gate the claim. Scoped to the **single active `story_id`** (a per-story DB read — never the whole briefing). Returns HTTP 200 + fallback so the conversation never breaks; the system prompt **forbids answering without the provided context**. Prompt/context-cache the per-story corpus block for cheap repeat questions. *(No embeddings / Pinecone / topK retrieval — the corpus is small enough to pass whole.)*
- Response maps to `api-contracts.md` `QuestionAnswer`: `{ answer_text, answer_citations[], answer_is_grounded }`. **Contract rule (Decision #5):** `answer_is_grounded === false` → render the refusal state, never an ungrounded guess.
- **Keep the visual contract byte-for-byte:** grounded answer → `.qa-bubble-a` with `.cite-chip` citation chips (one per `answer_citations` source); off-source → `.qa-refusal` blush card with the mono header `⌀ CAN'T ANSWER FROM SOURCE`. Keep the `.dot-typing` thinking state before every answer. This visual distinction is *how users learn to trust the system* (`ui-design-decisions.md` §7) — do not redesign it.
- Same brain powers typed Q&A (Detail) and Voice mode; voice just streams the same grounded answer via Gemini Live audio.
- Persist turns to `story_qa`; the suggested chips come from `suggested_questions`.

---

## 8. Blip logo → `<BlipLogo>` + App Store icon

**Prototype:** `blipSignal` (an IIFE that builds the 3-arc radar SVG with `viewBox="0 0 11 14"`, arcs at radii `[3.2, 6.2, 9.2]`, opening right at 46°) + `blipLogo(px, cls)` (the `<span class="blip">bl<span class="bi">ı<i class="tittle"><b class="bdot"></b>{sig}</i></span>p</span>`). Styled by `.blip` / `.blip-sig` / `.bw` (the wave strokes, opacities `1 / 0.88 / 0.74`) / `.bdot` in `styles.css`, with a `.glow` variant that pulses the waves (`blipGlow` / `blipWave` keyframes). A standalone `.blip-mark` (white dot + ring) covers square/icon spots.

**Production:**
- `src/components/brand/BlipLogo.tsx` — `<BlipLogo size={20} glow={false} />`. Render the same markup (the em-scaled `font-size:{size}px` keeps the tittle dot + waves proportional). Keep the 3-arc SVG as an inline component (the arc maths is load-bearing — copy `blipSignal`'s radii/angle constants verbatim so the waves stay identical). The `glow` prop toggles the `blipGlow`/`blipWave` animations (used on splash + onboarding); honour reduced-motion (no pulse).
- `src/components/brand/BlipMark.tsx` — the standalone dot+ring (`.blip-mark`, optional `.pulse`), for square/icon spots and the loading splash.
- **App Store app icon:** generate a **flattened raster** from `.blip-mark` (or a tightened wordmark lockup) on the `#020617` field — white dot + concentric radar ring, centered, no transparency (App Store icons must be opaque, no alpha, square). Produce the full iOS icon set (1024×1024 marketing + the `AppIcon.appiconset` sizes) and drop it into the Xcode asset catalog. **Do not** ship the live SVG/CSS as the icon — bake a PNG. Keep the glow subtle/baked (no animation in the static icon). A small build script (sharp/Pillow) rendering the mark → the size matrix is the cleanest path; wire it as a one-off asset task, not runtime.

---

## 9. Build order (align the port to milestones)

Reel first (the highest-risk, habit-driving surface), then the moat (Detail + grounded Q&A), then voice + onboarding. This mirrors `ui-design-brief.md` (the reel is "the highest-risk surface") and lets each milestone ship a usable slice.

1. **M-Reel — the audio-first loop.** Token config (§4) + `BlipLogo` (§8) + `LayerStack` shell (§1) + `Reel`/`ReelStory`/`FiniteBar`/`KaraokeCaption` driven by the **real `<audio>` clock + `caption_sentences` timestamps** (§3.1) + scroll-snap/framer gestures (§3.2) + `TapToStart` audio unlock (§6) + auto-advance with preloading + the `AllCaughtUp` finish line. Loading skeleton + error/offline. **DoD:** a finite, audio-driven, swipeable reel that captions word-by-word off real timestamps and ends at 30/30.
2. **M-Detail+Q&A — the moat.** `StoryDetail` (staggered reveal, §3.3) + `TrustStrip`/`BiasBar`/`StoryTimelineDrawer`/`KeyFigureCard`/`OpposingViewCard` + pinned `QaComposer`/`QaThread` wired to the **real RAG retriever + verification** with the **exact citation-chip / `⌀ CAN'T ANSWER FROM SOURCE` refusal** contract (§7). Drag-right-to-open / drag-to-close (§3.2). **DoD:** swipe-right opens a readable trust-laden detail; grounded answers cite sources; off-source questions refuse cleanly.
3. **M-Voice + follow — hands-free + follow.** Shared `VoiceOrb`/`Waveform` template; in-news **Voice mode** (§2 row 7) over Gemini Live (mic-in-orb, listening/responding, RAG-grounded). Email-only passwordless sign-in. Profile sheet + Following / "what's new". *(Voice-agent onboarding dropped 2026-05-30 — onboarding is chip-only, shipped in M1 `plans/phase-1e-auth-onboarding-interest-profile.md`.)* **DoD:** a user can sign in by magic link and talk to any story hands-free.

> **Next:** Create `reference/supabase-schema.md` defining the cited tables (`segments`, `stories`, `digests`, `caption_sentences`, `detail_chunks`, `story_trust`, `story_timeline`, `story_qa`, `story_topics`, `suggested_questions`, `follows`, `saves`, `player_signals`, `interests`, `user_interest_profile`) so the surface-table data columns are concrete — then `/plan-phases` to turn M-Reel / M-Detail+Q&A / M-Voice+follow into phases. *(Onboarding is chip-only — voice-agent onboarding dropped 2026-05-30.)*
