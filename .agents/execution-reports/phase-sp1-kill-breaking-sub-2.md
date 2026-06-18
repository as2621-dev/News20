# Execution Report — Phase SP1, Sub-phase 2: Remove breaking from the frontend (TS)

**Date:** 2026-06-18
**Status:** SUCCESS (in-scope) — build/tests RED only on out-of-scope SP4 consumers (expected, documented below)

## Implemented
The TS twin of the Python breaking removal (SP1). Removed the `breaking` feed CATEGORY/bucket/slot/template/allocation across the 3 in-scope files; preserved the velocity signal (`CoverageMode` "breaking"|"developing"|"settled" in `src/types/detail.ts` — untouched, out of scope KEEP).

- `DesignBucketId` / `FeedCategoryEnum`: dropped `breaking` (now 8 design buckets / 8 enum values; the dead Postgres `breaking` enum value is retained per SP3 but the UI no longer writes it).
- `DESIGN_BUCKETS`: removed the `breaking` entry.
- `DESIGN_BUCKET_TO_ENUM`: removed the `breaking: "breaking"` pair (`ENUM_TO_DESIGN_BUCKET` derives from it, so it follows automatically).
- `DEFAULT_ALLOCATION_SEGMENTS`: re-split to total 30 across the 7 categories, mirroring the Python `DEFAULT_FEED_ALLOCATION` exactly — `world 5, tech 5, youtube 6, markets 4, sport 3, x 3, culture 4`.
- `ALWAYS_INCLUDED_CATEGORY_BUCKET` (the breaking force-inject): removed; `allowedBucketsForSelections` no longer force-adds it; JSDoc on `allowedBucketsForSelections` + `buildSegmentsForSelections` updated.
- `feed_slot_kind` union: `"breaking" | "interest"` → `"interest" | "source"` (faithful twin of the Python `SLOT_KIND_INTEREST`/`SLOT_KIND_SOURCE`; breaking + exploration retired). JSDoc updated.
- `DetailCategory` / `DETAIL_TEMPLATES`: dropped the `breaking` member + its template row (now 8 buckets, matching `agents/pipeline/detail_templates.py`).
- Updated module-header/JSDoc counts ("9 buckets" → "8", "5 non-breaking" → "5 topic") for accuracy.

## Files touched (only these 3)
- `/Users/asheshsrivastava/News20/News20/src/lib/feedBuckets.ts`
- `/Users/asheshsrivastava/News20/News20/src/lib/detailTemplates.ts`
- `/Users/asheshsrivastava/News20/News20/src/types/feed.ts`

## Divergences from the brief
- The brief said `feed_slot_kind` "drops breaking". I set the union to `"interest" | "source"` (not just `"interest"`) because the live Python pipeline (`agents/pipeline/feed_assembly.py`) now emits `SLOT_KIND_INTEREST` and `SLOT_KIND_SOURCE`. Keeping only `"interest"` would have been a stale twin. This is the faithful mirror.

## Self review — findings & fixes
- Reviewed `git diff` of all 3 files: no leftover functional `breaking` refs in scope; remaining grep hits in scope are my own explanatory comments (`detailTemplates.ts:25`, `feed.ts:177-178`).
- `npx tsc --noEmit`: 0 type errors originate in my 3 files. All 11 errors are in out-of-scope files (listed under Concerns).
- No fixes needed inside the 3 files.

## Validation
- `npm run build`: **RED** — fails type-checking at `src/components/blip/reel/ArticleLayer.tsx:486` (first out-of-scope consumer). NOT caused by my files.
- `npx tsc --noEmit`: 11 errors, **all in out-of-scope files** (4 production consumers + 2 test files). Zero in `feedBuckets.ts`/`detailTemplates.ts`/`feed.ts`.
- `npx vitest run tests/lib/detailTemplates.test.ts tests/lib/feedBuckets.test.ts`: 12 failed / 30 passed. **Every** failure asserts removed breaking behavior (force-include breaking, "9 detail categories", breaking template parity, 9-bucket bijection, old under-30 counts). No failure indicates a real regression in my code.

## Definition of done
**PASS for in-scope work; FAIL on the literal "build green" gate due to out-of-scope consumers (SP4).**
- `grep -rn "breaking" src/lib src/types`: no functional hits in my 3 files (only my comments). Other functional hits are in out-of-scope files (see Concerns) + the KEEP velocity signal in `src/types/detail.ts`.
- `npm run build` green: **NO** — blocked by out-of-scope stale consumers. Per the sub-phase brief these are flagged for SP4, not edited here. I did not weaken production code to make a stale build/test pass (Rule 12).

## Concerns — out-of-scope work SP4 must fix
These reference the removed breaking CATEGORY/slot and now break the build/tests. They are NOT in my 3-file scope; do NOT regress them by reverting my changes — fix them in SP4.

**Production consumers (block `npm run build`):**
- `src/components/blip/reel/ArticleLayer.tsx:486,489` — `story.feed_slot_kind === "breaking"` + `DESIGN_BUCKETS.breaking.color` ("BREAKING" chip override). Remove the breaking chip branch.
- `src/components/blip/reel/ReelStage.tsx:298,301` — same "Breaking" chip override. Remove.
- `src/lib/feed/fixtureFeed.ts:150` — `feed_slot_kind: meta.digest_id === "digest-1" ? "breaking" : "interest"`. Change to `"interest"` (or `"source"`).
- `src/lib/feed/supabaseFeed.ts:178,224,227-228` — maps DB row to `"breaking"`. Map to `"interest"`/`"source"` only.

**Stale tests (SP4 owns):**
- `tests/lib/feedBuckets.test.ts:4` imports the removed `ALWAYS_INCLUDED_CATEGORY_BUCKET`; lines 60,233-235,244 assert breaking force-include / old under-30 counts (20→14) / breaking-first seed order. The new default split is `world 5, tech 5, youtube 6, markets 4, sport 3, x 3, culture 4` (=30).
- `tests/lib/feedAllocation.test.ts:101,161,206` use `bucketId: "breaking"`.
- `tests/lib/detailTemplates.test.ts` — "9 detail categories", "breaking matches the locked Python table", "coverage only on breaking and world": update to 8 buckets, coverage now only on `world`.

**Non-functional comment hits (safe to leave or tidy in SP4):**
- `src/lib/feedAllocation.ts:115,201` — JSDoc examples still show `{ bucketId: "breaking", count: 2 }`.
