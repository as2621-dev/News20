# Phase SP3 — Sub-phase R3 execution report: TS collateral remediation (feedAllocation)

**Status:** SUCCESS — `npm run build` is GREEN, feedAllocation suite 10/10 PASS.
**Date:** 2026-06-19
**Scope:** Surgical taxonomy alignment of the 2 unassigned collateral files SP2 surfaced. Touched ONLY `src/lib/feedAllocation.ts` and `tests/lib/feedAllocation.test.ts`. Did NOT edit `feedBuckets.ts` or any other file. Did NOT commit.

---

## 1. The 7 errors fixed

All 7 stemmed from the SP3 unfold (retired bucket ids `world`/`podcasts`/`world_politics`/`tech_science`):

- `feedAllocation.ts` (3): the dead `podcasts` graceful-degrade path referencing the retired `podcasts` bucket id (`deferredBuckets.push("podcasts")`, `row.allocation_category !== PODCASTS_ENUM_VALUE`, `savedEnumValues.delete(PODCASTS_ENUM_VALUE)`).
- `feedAllocation.test.ts` (4): fixtures pinned to retired ids `world` (×3) and `podcasts` (×1).

---

## 2. Changes per file

### `src/lib/feedAllocation.ts` — removed the dead `podcasts` degrade machinery

Dead code DELETED (Rule 12 — no slop left behind):
- The `PODCASTS_ENUM_VALUE` import.
- The entire `isPodcastsEnumMissingError(error)` helper (SQLSTATE 22P02 matcher) — only the degrade branch called it.
- The `if (isPodcastsEnumMissingError(...)) { … } else { … }` block in the upsert error path — collapsed to the plain error-surface (the former `else` branch): log + throw. There is no longer any `podcasts` enum value to degrade around, so any upsert error is a real failure and must surface (Rule 12).
- The local `deferredBuckets: DesignBucketId[]` accumulator and its `.push("podcasts")`.
- The `rowsWithoutPodcasts` re-upsert retry.

Kept for caller stability (Rule 3 — these two files are my only scope):
- `SaveAllocationResult.deferred_buckets` field is RETAINED, now always `[]`. It is read by `src/components/onboarding/BuildYour30.tsx:325` (`result.deferred_buckets.length`) and mocked in `tests/lib/onboarding/buildYour30FirstRun.test.tsx` — both OUTSIDE my scope. Removing the field would have broken those files (out of scope to edit). I retyped its purpose in the JSDoc (always-empty under SP3; surface kept ready if a future bucket ever ships ahead of its enum migration) rather than deleting it.

Doc/example alignment (no behavior change):
- Module JSDoc: replaced the "podcasts enum value (migration 0010)" paragraph with the SP3 taxonomy note (10 buckets, identity enum, no degrade case).
- `saveUserFeedAllocation` JSDoc: dropped the podcasts-degrade paragraph; example fixture `bucketId: "world"` → `"geopolitics"`; `@throws`/`@returns` updated.
- `getUserFeedAllocation` JSDoc example: `bucketId: "world"` → `"geopolitics"`.

### `tests/lib/feedAllocation.test.ts` — remapped fixtures + repointed the degrade test

- `HAPPY_SEGMENTS`: `world → geopolitics`. Expected upserted enum values updated to the new IDENTITY map: `world_politics → geopolitics`, `tech_science → tech` (and the stale-prune `listValue` `(sport,world_politics,tech_science)` → `(sport,geopolitics,tech)`).
- Read-test fixtures: `world_politics → geopolitics`, `tech_science → tech`; expected mapped-back `bucketId: "world"` → `"geopolitics"`.
- `nonThirty` fixture: `world → geopolitics`.
- **Degrade test repointed (Rule 9 — translated intent, did not gut):** the old "degrades gracefully when the podcasts enum value is missing" test exercised a code path that no longer exists. Replaced with "persists every bucket with no deferral under the SP3 taxonomy (no degrade case)" — it asserts the surviving business contract: a clean multi-bucket save upserts ONCE, drops NO bucket, and returns `deferred_buckets: []`. This still fails if a bucket were ever silently dropped (the "Build your 30 persists short" risk) — meaningful, not a tautology.
- Removed the now-unused `makePodcastsEnumMissingError()` helper. `PostgrestError` import retained (still used by `makeAllocationClient` options type + the generic-error throw test).
- Updated the suite WHY-header and the `makeAllocationClient` JSDoc note to drop stale podcasts-degrade references (Rule 12 — no stale docs).

---

## 3. Self-review (Step B)

- `grep -nE '"world"|"podcasts"|world_politics|tech_science|isPodcastsEnumMissingError|PODCASTS_ENUM_VALUE|makePodcastsEnumMissingError|deferredBuckets'` over both files → **NONE FOUND**.
- No NEW dead code introduced: the removed branch left no orphaned locals; the retained `deferred_buckets: []` is read by a live external caller, so it is not dead.
- Test assertions stay meaningful: the repointed test asserts single-upsert + no-drop + no-defer (Rule 9). All other assertions (sort-order, owner-scoped prune, throw-on-error, signed-out, non-30 warn, read mapping) unchanged in intent.
- Severity: all changes LOW-risk (mechanical id remap + dead-branch deletion); no logic semantics changed beyond removing an unreachable path.

---

## 4. Validation (Step D)

```
$ npx vitest run tests/lib/feedAllocation.test.ts
 Test Files  1 passed (1)
      Tests  10 passed (10)

$ npm run build
 ✓ Compiled successfully in 4.4s
 ✓ Linting and checking validity of types
 ✓ Generating static pages (6/6)
 ✓ Exporting (2/2)
 → BUILD GREEN (full project typecheck clean)
```

No fix-and-rerun loop needed (passed first attempt).

---

## 5. Concerns

1. **`SaveAllocationResult.deferred_buckets` is now vestigial** (always `[]`). I retained it because two out-of-scope files read it. A future cleanup could drop the field and the `deferred_count` log in `BuildYour30.tsx` together — out of scope here.
2. **Stale taxonomy drift elsewhere (not my scope, flagged by SP2):** `src/lib/detailTemplates.ts` `DetailCategory` literal still on `world/markets/culture/podcasts`; `src/lib/interestVector.ts` JSDoc describes the retired screen taxonomy; `sourceSwipeData.ts` `geopolitics` accent conflict. None block the build. Candidates for an SP4 doc/taxonomy sweep.
3. Did NOT commit; touched only the two assigned files.

---

## Return to orchestrator
1. **STATUS: SUCCESS**
2. **Files touched:** `src/lib/feedAllocation.ts`, `tests/lib/feedAllocation.test.ts`.
3. **Validation:** `npm run build` **PASS** (green, full typecheck clean, no residual errors); `feedAllocation` suite **PASS** (10/10).
4. **Concerns:** vestigial `deferred_buckets` field retained for external-caller stability; unrelated taxonomy drift in `detailTemplates.ts`/`interestVector.ts`/`sourceSwipeData.ts` flagged for a future sweep (out of scope).
