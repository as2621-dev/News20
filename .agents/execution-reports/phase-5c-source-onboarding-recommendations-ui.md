# Phase 5c — Source-Onboarding Swipe UI (execution report)

Ported the user's Claude Design "blip" Source Swipe handoff to React/TSX and wired
it to the already-shipped phase-5c logic. After the topic picker, the user swipes
through curated source recommendations across 4 platforms (YouTube → Podcasts → X →
People); swipe-right = follow, swipe-left = skip; a "building your profile" curtain
opens the flow and a final "You're all set." hands off to the reel.

## What I built (files + responsibilities)

New:
- `src/lib/sourceSwipeData.ts` — the data layer. `loadSourceSwipeDeck(client?)` runs
  the recommendation pipeline (rollUpInterestVector → getArchetypes + mapToArchetype
  → getRecommendedSources per axis) and shapes each catalog row into a
  `SourceSwipeCardModel`. Exports `SOURCE_SWIPE_PLATFORMS` (the 4 platforms ↔
  `ContentSourceType`), `computeMatchPct`, and the view-model types.
- `src/components/sources/SourceSwipe.tsx` — the deck container. Owns state (platform
  index, card index, per-platform followed sets, undo history), gestures, commit/undo,
  the per-set handoff + auto-advance, the final done CTA, and the optimistic
  follow/unfollow persistence. Props: `{ onDone: (total: number) => void }`.
- `src/components/sources/SourceSwipeCard.tsx` — one presentational card (accent-gradient
  logo header, platform glyph, % match badge, Playfair name, mono meta, coverage tags,
  why-box, FOLLOW/SKIP stamps). Single-shot broken-thumbnail fallback.
- `src/components/sources/ProfileCurtain.tsx` — the ~6.4s "Building your profile"
  interstitial (orb, picks pills, 4 ticking platform rows, progress bar, skippable).
- `src/components/sources/SignalOrb.tsx` — the brand signal orb (curtain + done screen).
- `src/components/sources/sourceSwipeGlyphs.tsx` — the inline SVG `<symbol>` sprite
  (platform + action glyphs) referenced by `<use href="#id">`.
- `tests/lib/sources/sourceSwipe.test.tsx` — deck-logic tests (5).
- `tests/lib/sourceSwipeData.test.ts` — `computeMatchPct` formula tests (3).

Edited (wiring only):
- `src/components/onboarding/OnboardingFlow.tsx` — added a `sources` step between the
  picker-persist (`loading`) and the reel; returning-user skip via
  `isSourceOnboardingComplete()`; `onDone` → `markSourceOnboardingComplete()` →
  `router.push("/")`.
- `src/app/globals.css` — appended the source-swipe + orb CSS, screen-scoped under
  `.sw-screen` (see styling below).

## How each phase-5c piece is wired

- **Archetype match / recommendations:** `loadSourceSwipeDeck` calls
  `rollUpInterestVector()` → `mapToArchetype(vector, getArchetypes())` → one
  `getRecommendedSources(kind, { archetypes:[match.archetype_id], subNiches, limit:12, client })`
  per platform axis (`youtube_channel`/`podcast`/`x_account`/`personality`). The
  vector's non-zero pinned categories double as the `subNiches` boost tags. NOTE the
  real `getRecommendedSources` signature takes `client` INSIDE the options object (the
  task brief said a positional limit) — wired to the actual signature.
- **% match formula (documented):**
  `raw = 50 + archetypeScore×30 + (popularity_score−50)×0.4`, clamped to `[60, 99]`.
  Blends the user↔archetype affinity (cosine 0–1, up to +30) with the source's
  in-archetype popularity rank (0–100, ±20 around the midpoint). The 60-floor keeps
  every shown card a real recommendation; the 99-ceiling avoids a fake "100%".
- **Curtain picks:** derived from the rolled-up interest vector — the top-3 non-zero
  pinned categories, title-cased. Empty for a new/anon user (curtain leans on the
  ticking rows). The 4 row counts are the REAL per-platform recommended-card counts
  (not hardcoded), so the curtain never promises sources the deck can't deliver.
- **Follow persistence:** swipe-right → `followSource(source_id)` (the catalog-source
  follow → `user_content_sources`), OPTIMISTIC: the card animates away immediately,
  the write runs async; a failure removes the source from the local set, logs a
  structured error with `fix_suggestion`, and surfaces a non-blocking inline notice
  (Rule 12). Undo reverts local index/count AND `unfollowSource(source_id)` if it had
  persisted.
- **"People" / personalities:** the `content_source_type` enum includes
  `'personality'` (migration 0009), so the People pass uses the SAME path —
  `getRecommendedSources("personality", …)` + `followSource(…)` — uniformly with the
  other 3 axes (no special-casing). See hand-offs for the parallel `personalities` /
  `user_personalities` tables not used here.

## Divergences from the prototype (+ why)

- **OMITTED the reviewer-only logo upload/drag-drop** (`thumbInput`, `readThumb`,
  `bindThumb`, `drophint`, localStorage thumbs) — per the brief, not production.
  Production uses real catalog `thumbnail_url`s with the portraitBg initials-gradient
  as the fallback.
- **Hardcoded `DATA` replaced** with the live recommendation pipeline.
- **Fallback treatment:** the prototype put solid-white mono initials over the accent
  header; on the no/404-thumbnail path I swap the header background to the stable
  per-name `portraitGradient` (reuse-map §5 — guaranteed legible, never flickers) and
  keep the initials solid white. Reuses `portraitBg` as the brief required.
- **Drag intensity divisor unified to 92** (the commit threshold) vs the prototype's
  90-for-intensity / 92-for-commit split — a ≤2px cosmetic difference, more consistent.
- **Graceful degradation:** a new/anon user (zero interest vector →
  `balanced-generalist`) gets whatever the catalog surfaces; empty platform sets
  auto-advance (the handoff effect re-keys on `platformIndex` so chained empty sets
  each schedule their own advance); a hard load failure shows a "couldn't load → see
  my briefing" escape that calls `onDone(0)`.

## Styling approach + scoping

The codebase convention is globals.css porting prototype CSS with byte-compatible
class names ("Class names kept BYTE-COMPATIBLE with the prototype"), plus inline
styles, no CSS modules. I appended the source-swipe + orb CSS to `globals.css`, with
EVERY selector scoped under a root `.sw-screen` class and the dark tokens declared as
local vars on `.sw-screen` (NOT `:root`), so the dark palette + the generic class
names (`.card`, `.orb`, `.actions`, `.stamp`, …) cannot leak into the cream topic
picker or collide with the existing in-news Voice `.orb` (which stays global,
untouched). Fonts bind to the existing next/font CSS variables
(`--font-playfair`/`--font-jetbrains-mono`/`--font-inter`). A scoped reduced-motion
block disables the orb/arrow animations. The 3 `noDescendingSpecificity` warnings on
the faithfully-ported selector order are suppressed with explanatory biome-ignore
comments (no real cascade conflict).

## Self-review findings + fixes

- Drag commit threshold (92px), rotation (±14deg), translate (±120%), stamp opacity
  (= min(|dx|/92, 1), mutually exclusive by sign): all match the design. ✓
- Auto-advance ~1.7s; final-only "You're all set." CTA; sets 1–3 show the handoff hop:
  verified. ✓
- Curtain skippable + ref-guarded against double-reveal. ✓
- % match in 60–99 (tested). ✓
- Dark tokens screen-scoped, no `:root` leak; existing `.orb` untouched. ✓
- Follow persists + undo unfollows (tested); optimistic with non-swallowed error
  surface. ✓
- Returning-user skip via `isSourceOnboardingComplete()`. ✓
- portraitBg fallback on broken thumbnail (tested). ✓
- No `any` (one `as keyof` narrowing, justified inline); no swallowed errors; no
  leftover prototype cruft / console.logs. ✓
- Fixed during review: removed an over-engineered gradient-text-clip fallback (risked
  invisibility) → stable portraitGradient header + solid-white initials.

## Validation (exact commands + results)

- `npx tsc --noEmit` → clean (no errors).
- `npx biome check <my files>` → clean (0 errors, 0 warnings) after suppressions.
  (Repo-wide `biome check .` shows 16 pre-existing format errors in
  `trigger/sourceIngestion*.ts` — concurrent phase-5d work, NOT mine.)
- `npx vitest run tests/lib/sources/sourceSwipe.test.tsx tests/lib/sourceSwipeData.test.ts`
  → 2 files / 8 tests passed.
- `npx vitest run` (full suite) → 40 files / 366 tests passed. Baseline at task start
  was 36/339; the delta is my +8 plus tests landed by concurrent agents. NO
  regressions.

## Definition of done (swipe UI)

PASS — the swipe UI satisfies: curtain → 4-platform swipe deck → per-set
auto-advance → final done → reel hand-off; archetype-driven recommendations per axis;
optimistic follow + undo-unfollow persistence; % match badge; portraitBg fallback;
screen-scoped dark styling; returning-user skip; new/anon graceful degradation; tests.

## Concerns / hand-offs

1. **personalities table not used.** Migration 0009 also ships separate
   `personalities` / `user_personalities` tables with their own catalog + follow
   junction. The People pass instead uses `content_sources` rows of type
   `'personality'` + `followSource` (uniform with the other axes). If the curated
   catalog seeds personalities ONLY into `personalities` (not into `content_sources`
   as `'personality'` rows), the People deck will be EMPTY (degrades gracefully, no
   crash). Decide whether to (a) seed `content_sources` personality rows, or (b) add
   a personality-specific recommend+follow path against `personalities`/
   `user_personalities`. Flagged, not blocking.
2. **Source-onboarding-complete marker is localStorage** (per the shipped
   `onboardingProfile.ts` SP4a note) — a UX skip-gate only; the follows persist to the
   DB. Promote to a `users` column when a migration is added (getter/setter contract
   unchanged).
3. **No toast util exists** in the codebase; the optimistic-persist failure surfaces
   via a small inline `.sw-persist-notice` (role="alert") + structured log. Swap for a
   shared toast if/when one lands.
4. **Flow wiring assumption:** `OnboardingFlow` now uses `TopicTree` (the picker was
   swapped from `OnboardingPicker` by concurrent work mid-task); I wired the `sources`
   step into the current `TopicTree`-based flow. The `onboarding/page.tsx`
   returning-user gate is reads the EXISTING `user_onboarded_at` skip; the source-step
   skip is handled inside `handleComplete` (post-picker) via
   `isSourceOnboardingComplete()`.
5. **Single archetype for recommendations (v1):** `mapToArchetype` returns one match,
   so the deck recommends for `[match.archetype_id]` only. The recommender's
   round-robin multi-archetype balancing is available if a future top-2 match is wired.
