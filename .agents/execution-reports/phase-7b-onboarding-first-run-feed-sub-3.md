# Phase 7b — Sub-phase 3 execution report: Partial-feed metadata + "past 24 hours" banner

**Status:** SUCCESS

## What was implemented
`getReelFeed` now returns `{ stories, meta }` where `meta` is the new
`ReelFeedMeta` `{ allocated_count, feed_total, is_partial, is_first_run }`. The reel
mounts a dismissible day-one banner with the exact copy
**"Showing you the past 24 hours — {n}/30. Your full 30 land tomorrow."** only when
`is_first_run && is_partial` and the user has not yet dismissed it for that feed date.

## Files created / modified
- **MODIFIED** `src/types/feed.ts` — added `ReelFeedMeta` + `ReelFeedResult` interfaces.
- **MODIFIED** `src/lib/feed/index.ts` — `getReelFeed` now returns `Promise<ReelFeedResult>`; added `readIsFirstRun(feedDate)` (SSR-guarded localStorage read via `firstRunFlagKey`) and `toReelFeedResult(stories, feedDate)` (derives meta from the live row count + `FEED_TOTAL`).
- **NEW** `src/components/blip/reel/FirstRunBanner.tsx` — dismissible banner; own dismiss key; copy rendered from `feedTotal`.
- **MODIFIED** `src/components/blip/reel/BlipReel.tsx` — captures `loadedFeed.meta` into new `feedMeta` state, derives `shouldShowFirstRunBanner` + `bannerFeedDate`, mounts `<FirstRunBanner>` gated on first-run + partial.
- **NEW** `tests/lib/reel/firstRunBanner.test.tsx` — 5 tests (3 meta-derivation + 2 banner DoD).

## The meta shape (in `src/types/feed.ts`)
```ts
interface ReelFeedMeta {
  allocated_count: number; // live resolved row count
  feed_total: number;      // FEED_TOTAL (30)
  is_partial: boolean;     // allocated_count < feed_total
  is_first_run: boolean;   // firstRunFlagKey(feed_date) === "1" (SSR → false)
}
interface ReelFeedResult { stories: Story[]; meta: ReelFeedMeta; }
```

## Dismiss key chosen
`blip:first-run-banner-dismissed:<feed_date>` (value `"1"`), exported as
`firstRunBannerDismissedKey(feedDate)` from `FirstRunBanner.tsx`. It is a SEPARATE
namespace from SP2's `blip:first-run:<feed_date>` — the banner never reads or clears
the first-run flag, so dismissing the banner does not lose the first-run signal for
the rest of the day.

## How I avoided breaking getReelFeed callers
`getReelFeed` has exactly ONE caller in the codebase: `BlipReel.tsx:137` (verified by
`grep -rn "getReelFeed" src/ tests/`). I updated its `.then((loadedFeed) => …)` to read
`loadedFeed.stories` + `loadedFeed.meta`. No other consumer (Archive/Library/tests)
imports `getReelFeed`. `tsc --noEmit` is 0-error (excluding pre-existing remotion
TS2307), and the full vitest suite (432 tests) is green, confirming nothing else
depended on the old `Promise<Story[]>` shape.

`supabaseFeed.ts` was listed as touchable but left UNCHANGED — see divergence.

## Divergences (+ why)
- **`supabaseFeed.ts` unchanged.** The mission said to "thread `allocated_count`
  through supabaseFeed.ts / index.ts", but `allocated_count` IS the resolved row
  count (`stories.length`), which `index.ts` already has in hand from
  `getDailyFeed` / `getGlobalFeed` / `getFixtureFeed`. Adding a redundant count to
  `supabaseFeed`'s return would be gold-plating and would force a second return-shape
  change on its own callers (`getFeed`/`getDailyFeed` are imported elsewhere). Per
  Rule 2 (simplicity) + Rule 3 (surgical), I derive the count in `index.ts`. Flagged
  here per Rule 12.
- **Banner feed-date derived in `BlipReel`, not returned from `getReelFeed`.** The
  banner's dismiss key needs the resolved feed date. `getReelFeed` resolves it
  internally (`feedDate ?? new Date().toISOString().slice(0,10)`, UTC). I recompute
  the same UTC expression in `BlipReel` (`bannerFeedDate`) rather than widening the
  meta with a `feed_date` field. This matches SP2's `todayUtcFeedDate()` (also UTC
  `slice(0,10)`), so the dismiss key and the first-run flag key agree on the date.

## Review findings + fixes (Step B/C)
Self-review against the checklist — no critical/high issues:
- **Exact copy string** — PASS. `Showing you the past 24 hours — {allocatedCount}/{feedTotal}. Your full {feedTotal} land tomorrow.` Both `/30` and "full 30" render from `feedTotal` (the constant), so they stay correct if `FEED_TOTAL` changes. The "past 24 hours" is a fixed time-window phrase, not the count — left literal. Asserted by the copy test.
- **Gating** — PASS. Banner mounts only when `Boolean(feedMeta?.is_first_run && feedMeta?.is_partial)`. A full feed (`is_partial false`) or non-first-run feed (`is_first_run false`) renders nothing. Asserted via the 30-row meta test (no first-run flag, is_partial false).
- **Dismiss uses own key** — PASS. `blip:first-run-banner-dismissed:<date>`; test asserts the first-run flag (`blip:first-run:<date>`) is untouched (`toBeNull()`).
- **SSR guard** — PASS. `readIsFirstRun` (index.ts) and the banner's `readDismissed`/`persistDismissed` all `typeof window === "undefined" → return false/no-op`, and wrap `localStorage` in try/catch (private-mode safe).
- **No `any`** — PASS. Only casts are in the TEST file (`as Story`, `as never` for the session/store stub), consistent with repo test convention; production code uses typed interfaces.
- **No break to callers** — PASS (single caller, updated; tsc + full suite green).
- **Fixes applied:** none needed; biome clean on first pass.

## Validation results (Step D)
```
$ npx vitest run tests/lib/reel/firstRunBanner.test.tsx
 Test Files  1 passed (1)
      Tests  5 passed (5)
```
```
$ npx tsc --noEmit   (non-remotion "error TS" lines)
0
```
```
$ npx vitest run            # full suite — no regression from the return-shape change
 Test Files  51 passed (51)
      Tests  432 passed (432)
```
Biome: `Checked 5 files … No fixes applied.`

## Definition of done (Step E) — PASS
- 24-row first-run feed → `meta.is_partial === true` AND banner renders `24/30` — PASS (meta test + banner copy test).
- 30-row feed → `meta.is_partial === false` AND no banner (gating false) — PASS.
- Dismiss hides the banner AND persists (re-mount → still hidden) — PASS (own key persisted, first-run flag untouched, fresh mount stays hidden).
- Type-check passes with the extended `feed.ts` — PASS (0 non-remotion errors).

## Concerns / handoff
- The banner renders regardless of `reelStatus` (it is outside the status-overlay
  block), so it is visible over the loading skeleton / tap-to-start too. This is
  intentional — the day-one notice should be present as soon as the partial feed
  resolves — but if the owner wants it suppressed until `playing`, gate it on
  `reelStatus` in a follow-up.
- Pre-existing `remotion/**` TS2307 errors remain (out of scope; unchanged by this
  sub-phase).

---
**Return to orchestrator:**
1. STATUS: SUCCESS
2. Files: `src/types/feed.ts`, `src/lib/feed/index.ts`, `src/components/blip/reel/FirstRunBanner.tsx`, `src/components/blip/reel/BlipReel.tsx`, `tests/lib/reel/firstRunBanner.test.tsx`
3. Validation: PASS — `Test Files 1 passed (1) / Tests 5 passed (5)`; full suite `432 passed`; `tsc --noEmit` → 0 non-remotion errors
4. Definition of done: PASS (all 4 assertions green)
5. Concerns: banner visible over loading overlays by design; `supabaseFeed.ts` intentionally unchanged (allocated_count = row count derived in index.ts)
