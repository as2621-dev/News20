# Phase 3 — Sub-phase 4 execution report: Shared voice UI (orb + waveform + transcript)

**Status:** SUCCESS
**Worktree:** `News20-sub-4`
**Scope:** SP4 only — the shared, presentational Voice-mode UI template (port-map §5.1). No Gemini/WS/audio logic (that is SP3).

---

## What was implemented

Three presentational, prop-driven components matching the prototype `.orb` / `.orb-brand` / `.orb.listening` / `.orb.responding` + waveform + `#v-transcript` contract (styles.css 303–397, app.js `voiceConversation` 587–627). Verbose prop names, explicit Props interfaces, JSDoc with examples, TS strict, double-quote/2-space biome conventions (matched to `biome.json`, which overrides the global single-quote rule — Rule 11).

### `src/components/voice/VoiceOrb.tsx`
- States `idle · listening · responding · paused` via `orb_state` prop. Emits the prototype class contract: `orb` base + optional `orb-brand` + the animation class (`listening` / `responding`).
- **Mic folded into the orb** — the orb itself is a `<button>` (≥44px, 168px disc) firing `onPauseToggle`; no separate mic button. `idle`/`paused` are still, `listening`/`responding` animate (animating = live, still = paused).
- **Reduced motion:** the pure exported `orbAnimationClass(orb_state, prefers_reduced_motion)` returns `""` for the animating states under reduced motion, so the orb emits ZERO animation classes (only the static `orb` base) — assertable in JS.
- Inner `.orb-ring` (pulse-ring host) + `Waveform`, both `pointer-events-none` so taps always land on the orb. `aria-label` (Pause/Resume), `aria-pressed` = conversation-live, `focus-visible` ring for keyboard.
- Self-sufficient visuals via inline `style` (radial gradient + box-shadow ported from styles.css) so it renders standalone without globals.css being edited; the contract class names are still emitted so phase-3b/globals.css can layer the keyframes.

### `src/components/voice/Waveform.tsx`
- Five `.wave-bar` pills (prototype `#wave`). Reacts to `amplitude_level` (0–1) via the pure exported `waveformBarHeights(amplitude)` — clamped, centre bar tallest, symmetric. Flat resting row when `is_active=false` or `prefers_reduced_motion` (prototype `animWave` early-returns under reduced).
- `accent` (per-story `--accent`) / `brand` (white) fill variants mirrored from the orb.

### `src/components/voice/TranscriptLine.tsx`
- One transcription line, `transcript_role` = `"input"` (user, full-white) / `"output"` (model answer, `white/85`) — the prototype's opacity contract. Maps 1:1 to Gemini Live `inputTranscription`/`outputTranscription`. Empty text → renders `null` (no empty shell). `aria-live="polite"` + `aria-busy` for streaming.

### `tests/lib/voice/voiceOrb.test.tsx`
- 24 tests using the repo idiom (`react-dom/client` + `react`'s `act`, container queries, NO `@testing-library` — matches `tests/lib/detail/*`). Covers the three DoD assertions + happy/failure/edge per component.

---

## Files created (relative to repo root)
- `src/components/voice/VoiceOrb.tsx`
- `src/components/voice/Waveform.tsx`
- `src/components/voice/TranscriptLine.tsx`
- `tests/lib/voice/voiceOrb.test.tsx`

(No existing files modified. `node_modules` was symlinked from the main worktree to enable validation — not committed, not a source change.)

---

## Divergences + why
1. **Inline `style` for the orb gradient/box-shadow** instead of relying on `globals.css`. The scope lock forbids touching `globals.css`, where the prototype's `.orb`/`.orb-ring`/`pulse-ring`/`orb-throb`/`.wave-bar` rules currently DO NOT exist (only the `.blip*` rules were ported). To make the component visually functional standalone, the base orb visuals are inlined while the **contract class names are still emitted** so phase-3b (or a globals.css follow-up) can attach the `pulse-ring`/`orb-throb` keyframes. **Action for orchestrator:** the `@keyframes pulse-ring` / `@keyframes orb-throb` and `.orb.listening .orb-ring` / `.orb.responding` rules (styles.css 308–313) still need adding to `src/app/globals.css` for the live pulse/throb animation — out of SP4's file scope. The class contract is in place; only the keyframes are missing.
2. **Double quotes / `biome` conventions** over the global CLAUDE.md's single-quote rule — the repo's `biome.json` mandates double quotes and every existing component uses them (Rule 11). Surfaced, not blended.
3. **`prefers_reduced_motion` as an explicit prop** rather than reading `useReducedMotion()` internally. Keeps the components purely presentational/parameterized (props in, callbacks out) so phase-3b's VoiceMode owns the OS-query read once and threads it down — and makes the reduced-motion DoD deterministically testable. Mirrors `KaraokeCaption`'s `reduceMotion?` prop convention.

---

## Self-review findings + fixes
- **[medium] `aria-pressed` semantics** — initially `true` for `idle`/`paused` (ambiguous). Fixed to reflect conversation-live (`listening`/`responding`), so a SR reads the toggle correctly. Fixed.
- **[low] biome formatting + import sort** — long inline gradient string wrap, single-line `.map`, import name order. Auto-fixed with `biome check --write`. Re-checked clean.
- No stray `any`. The two intentional `// Reason:`/`biome-ignore` comments (amplitude clamp, fixed-row index key) are justified inline.
- No dead code; every export is consumed (by the component or the tests).

---

## Validation results (exact)
- **Typecheck** (`tsc --noEmit`): PASS for all in-scope code. 0 errors in `src/` or `tests/`. The only 8 errors are pre-existing in `remotion/*` (a separate sub-project with its own `tsconfig.json` whose deps are not installed at root) — identical count on a clean tree, none touch voice files. NOT introduced by SP4.
- **Biome** (`biome check src/components/voice tests/lib/voice`): PASS — Checked 4 files, 0 errors.
- **Vitest** (`vitest run tests/lib/voice`): PASS — 1 file, **24/24 tests**.
- **Full suite regression** (`vitest run`): PASS — 18 files, **160/160 tests**. No regressions.

---

## Definition of done (per item)
1. **Headless render of each `VoiceOrb` state matches the prototype class contract** (`.orb` + `.listening`/`.responding`, bare `orb` for idle/paused): **PASS** — 5 state-render tests assert exact class membership/absence + the `.orb-ring` element.
2. **Tapping the orb fires the pause-toggle callback:** **PASS** — `onPauseToggle` called once on `.click()`; also asserts the orb is a real `<button>` with a Pause/Resume `aria-label`.
3. **A `prefers-reduced-motion` render emits NO animation classes:** **PASS** — 2 reduced-motion render tests + 1 pure-function test assert `listening`/`responding` are absent while the static `orb` base remains.

All three DoD items: **PASS** (real assertions, not visual-only).

---

## Concerns for the orchestrator (esp. exported Props for phase-3b)
- **Exported surface phase-3b mounts against:**
  - `VoiceOrb` props: `orb_state: "idle"|"listening"|"responding"|"paused"`, `orb_variant?: "accent"|"brand"`, `amplitude_level?: number`, `prefers_reduced_motion?: boolean`, `onPauseToggle?: () => void`, `tap_aria_label?: string`. Exported types `OrbState`, `OrbVariant`, helper `orbAnimationClass`.
  - `Waveform` props: `amplitude_level?`, `is_active?`, `prefers_reduced_motion?`, `wave_variant?: OrbVariant`. Exported `WAVEFORM_BAR_COUNT`, `waveformBarHeights`.
  - `TranscriptLine` props: `transcript_role: "input"|"output"`, `transcript_text: string`, `is_streaming?: boolean`. Exported type `TranscriptRole`.
  - Phase-3b owns: the OS reduced-motion read (`useReducedMotion()` → `prefers_reduced_motion`), mapping the Live conversation state → `orb_state`, computing `amplitude_level` from the audio analyser, and the permission-gate / end-conversation chrome (NOT part of this template).
- **`globals.css` keyframes gap (see Divergence 1):** the `.orb.listening .orb-ring { pulse-ring }`, `.orb.responding { orb-throb }`, and base `.orb`/`.orb-ring`/`.wave-bar` rules from `prototype/.../styles.css` are not yet in `src/app/globals.css`. The components emit the contract class names + inline base visuals, but the live pulse/throb keyframes must be added to `globals.css` (out of SP4 scope) before the animation is visible in the app. No test depends on this (jsdom asserts classes, not computed style).
- **Node deps:** the worktree had no `node_modules`; I symlinked the main worktree's (identical package.json + lockfile). The orchestrator's merge target already has deps installed — no action needed.
