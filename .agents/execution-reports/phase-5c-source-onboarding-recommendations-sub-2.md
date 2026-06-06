# Phase 5c — Sub-phase 2 execution report

**Sub-phase:** Universal avatar + selectable source card (port + reskin)
**Status:** SUCCESS

## What I implemented

Ported (structure only) + re-skinned to News20's dark-editorial tokens (`reference/design-language.md`) the three SP2 deliverables, following the codebase's self-contained inline-style + token-constant pattern (FollowChip/OnboardingPicker), with **no globals.css/tailwind edits** (SP2 scope):

1. **`src/lib/portraitBg.ts`** — pure helpers `portraitGradient(seed)` + `initials(name)`.
   - Deterministic 32-bit rolling hash → same input always yields the same gradient (no flicker).
   - Gradient stops constrained to a 6-pair palette built **only** from design-language.md tokens (`#3B82F6`, `#E8B7BC`, `#D1D4BD`, `#020617`). TL;DW's amber/neon `FALLBACK_GRADIENTS` + the hardcoded `PORTRAIT_BG` name map were dropped.
   - `initials` → 1–2 uppercase chars; blank name falls back to `"?"` (donor returned `""`).

2. **`src/components/sources/SourceArtwork.tsx`** — universal avatar.
   - Plain `<img>` with `referrerPolicy="no-referrer"`, `loading="lazy"`, `decoding="async"`; broken-image → initials-gradient fallback via a single-shot `hasLoadError` state (the `<img>` unmounts on error, so `onError` cannot loop).
   - Shape from `SOURCE_TYPE_CONFIGS[kind].tile_shape` (single-sourced with the type layer): circle for `personality`/`x_account`, rounded square (16px = control radius) for `youtube_channel`/`podcast`.
   - Props `SourceArtworkProps { source_name, image_url?, kind, size? }` — kept general for SP3 reuse (search-result avatars).
   - Fallback tile carries `role="img"` + `aria-label={source_name}` so the accessible name survives the image failure.

3. **`src/components/sources/SourceCard.tsx`** — controlled selectable card.
   - Whole card is a real `<button>` (`aria-pressed={selected}`, `aria-labelledby` → name) = one keyboard-focusable, labelled toggle.
   - Avatar (`SourceArtwork`) + name + 2-line-clamped description + a "Follow"/"Following" toggle affordance (a styled `<span>`, not a nested button — nested buttons are invalid HTML; the card's `aria-pressed` conveys state).
   - Controlled: calls `onToggle`, persists nothing itself (SP3/SP4's job).
   - Reuses the real `ContentSource` type from `src/types/source.ts` (no redefinition).

## Files created / modified

- `src/lib/portraitBg.ts` (new)
- `src/components/sources/SourceArtwork.tsx` (new)
- `src/components/sources/SourceCard.tsx` (new)
- `tests/lib/sources/sourceCard.test.tsx` (new — RTL-style component tests)

No files outside the three (+ test) were touched.

## Divergences (+ why)

- **Palette source:** Two token conventions coexist in the repo — the picker components (FollowChip/OnboardingPicker) use a **warm cream/green** palette from the picker spec §8, while `reference/design-language.md` (the file my brief names as **mandatory**) defines the **dark-editorial** system (`#020617` canvas, `#3B82F6` primary, `#FACC15`, `#D1D4BD`, Playfair/Inter/JetBrains Mono). Per Rule 7 (pick one, don't blend) + the brief's explicit "design-language.md is mandatory", I skinned to the **dark-editorial tokens**. The picker palette divergence is flagged as a hand-off — SP4 should confirm the source screens want the dark system, not the cream picker look, for visual continuity within onboarding.
- **`initials("")` → "?"** rather than the donor's empty string — avoids an empty fallback tile (Rule 12: fail visibly, not blank).
- **Toggle affordance is a `<span>`, not a nested `<button>`** — the whole card is the button (per brief "the whole toggle must be a real `<button>`"); a nested button would be invalid HTML and create a focus trap.

## Code-review findings + fixes

- **[High] onError loop guard** — verified `hasLoadError` is single-shot: once true the `<img>` is removed from the tree, so `onError` can't re-fire. Test asserts the `<img>` is gone after error. No fix needed (designed in).
- **[Med] accessible name on fallback** — added `role="img"` + `aria-label` to the fallback tile so a broken image still announces the source name (the loaded `<img>` uses `alt`). Fixed in implementation.
- **[Med] shape single-sourcing** — used `SOURCE_TYPE_CONFIGS[kind].tile_shape` instead of re-deriving a person/channel branch, so the enum→shape mapping can't drift from the type layer.
- **[Low] no leftover amber/TL;DW tokens** — grep-equivalent test asserts the gradient never contains `#ff8a3d` and matches the News20 hex set; `var(--*)` donor tokens removed entirely.
- **TS strict / no `any`** — only `any` use is the standard `IS_REACT_ACT_ENVIRONMENT` cast in the test (matches existing test files); production files are `any`-free.

## Validation results (exact commands)

| Command | Result |
|---|---|
| `npx tsc --noEmit` | PASS (exit 0) |
| `npx biome check src/lib/portraitBg.ts src/components/sources/SourceArtwork.tsx src/components/sources/SourceCard.tsx tests/lib/sources/sourceCard.test.tsx` | PASS (exit 0, 0 fixes) |
| `npx vitest run tests/lib/sources/sourceCard.test.tsx` | PASS (9/9) |
| `npx vitest run` (full suite, regression check) | PASS (34 files, 311/311) |

Testing harness: the repo has **no `@testing-library`** but DOES have a working RTL-equivalent convention (React 19 `react-dom/client` `createRoot` + `act`, jsdom, vitest globals). I matched it exactly (mirrors `tests/lib/onboarding/followSet.test.tsx` + `tests/lib/detail/trustStrip.test.tsx`) — no new harness invented. The broken-image fallback path is exercised by dispatching a real `error` Event on the `<img>`.

## Definition of done — PASS

- [PASS] Avatar renders a thumbnail (asserts `<img>` src + `referrerpolicy="no-referrer"`).
- [PASS] Falls back to initials-gradient when the URL 404s — test dispatches `onError`, asserts the `<img>` is gone and the initials tile (`"LF"`) renders.
- [PASS] Person (`personality`/`x_account`) renders as a circle (radius = size/2 = 32px @ size 64); channel/podcast renders as a rounded square (16px).
- [PASS] Card toggles selected state and exposes `aria-pressed` (false→Follow, true→Following; `onToggle` fires; controlled, doesn't self-persist).
- [PASS] RTL-style test includes the broken-image fallback path.

No assertion skipped; all tests ran and passed (Rule 12).

## Concerns / hand-offs

1. **Palette divergence (for SP4):** picker screens use a cream/green palette; I used the design-language.md dark system. Confirm the source-rec screens should be dark (recommended for continuity with the reel) before SP4 wires them next to the picker.
2. **No type change needed in `src/lib/sources.ts`** — `ContentSource` from `src/types/source.ts` fit as-is; nothing to hand off there.
3. **SP3 reuse:** `SourceArtworkProps` are kept general (name/url/kind/size) — SP3's search modal can render result avatars with the same props without rework. `SourceCard` is `ContentSource`-shaped; if SP3 search results aren't full `ContentSource` rows, they'll either map to that shape or compose `SourceArtwork` directly.
4. **Description clamp** uses `-webkit-line-clamp` (works in WebKit/Capacitor + Chromium — fine for this iOS/web target).
