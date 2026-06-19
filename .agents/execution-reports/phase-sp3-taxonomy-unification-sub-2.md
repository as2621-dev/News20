# Phase SP3 — Sub-phase 2 execution report: Canonical taxonomy in the TS twin

**Status:** SUCCESS (my two files + my test) — but **DoD build is RED** due to two UNASSIGNED files outside my scope (surfaced, not edited per instruction).
**Date:** 2026-06-19
**Files touched:** `src/lib/feedBuckets.ts` (rewritten taxonomy data + maps + docs), `tests/lib/feedBuckets.test.ts` (rewritten to the new taxonomy + ADDED the Python-twin-equality + identity-map assertions). `src/lib/interestVector.ts` — **NOT touched** (see §2).

---

## 1. The new TS taxonomy (mirrors the Python twin exactly)

### `DesignBucketId` / `FeedCategoryEnum` (10 ids, no fold)
`ai, geopolitics, business, environment, politics, tech, sport, arts, youtube, x`
`FeedCategoryEnum` is now a type alias of `DesignBucketId` (identity — the unfold collapsed the two axes).

### `DESIGN_BUCKETS` (ids + labels + accent hex)
| id | label | accent | kind | source of hex |
|---|---|---|---|---|
| `ai` | AI | `#3B82F6` | cat | **INFERRED** — brand primary blue (no prior dedicated AI accent; sourceSwipe used `#22D3EE` which collides with tech). FLAG for owner review. |
| `geopolitics` | Geopolitics | `#EF4444` | cat | locked legacy (interests.ts / fixtureFeed.ts / old feedBuckets `world`) |
| `business` | Business | `#22C55E` | cat | locked legacy (old `markets` green) |
| `environment` | Environment | `#34D399` | cat | sourceSwipeData `ACCENT_BY_TOPIC.environment` (emerald) |
| `politics` | Politics | `#A78BFA` | cat | sourceSwipeData `ACCENT_BY_TOPIC.politics` (purple) |
| `tech` | Tech | `#22D3EE` | cat | locked legacy (cyan) |
| `sport` | Sport | `#F59E0B` | cat | locked legacy (amber) |
| `arts` | Arts | `#E8B7BC` | cat | locked legacy (old `culture`/`wildcard` rose) |
| `youtube` | YouTube | `#94A3B8` | src (g-yt) | unchanged |
| `x` | X | `#CBD5E1` | src (g-x) | unchanged |

### `DEFAULT_ALLOCATION_SEGMENTS` (= Python `DEFAULT_FEED_ALLOCATION`, Rule-7 twin)
Ordered `[id, count]`, mirrors the Python dict insertion order, sums to 30:
`ai 4, tech 4, geopolitics 4, business 4, politics 2, environment 2, sport 3, arts 3, youtube 2, x 2` = **30**.
Verified equal to the Python twin by the new twin-equality test (keys+counts).

### Identity maps (the old fold is RETIRED)
- `DESIGN_BUCKET_TO_ENUM` — identity for all 10 ids (no rename).
- `PICKER_ROOT_TO_CATEGORY_BUCKET` — identity for the 8 roots (`ai→ai`, … `arts→arts`). Source axes absent (correct — not picker roots).
- `ENUM_TO_DESIGN_BUCKET` — derived inverse of the (now identity) forward map.
- `DESIGN_BUCKET_IDS` — the 10 ids in canonical order.

### `SOURCE_TYPE_TO_DESIGN_BUCKET` (4 source types → 2 axes)
`youtube_channel → youtube`, `x_account → x`, `personality → x` (unchanged), `podcast → youtube` (CHANGED: was `podcasts`, which no longer exists; a followed podcast now rides the youtube long-form axis instead of being dropped).

### `PODCASTS_ENUM_VALUE`
Kept exported but **retyped from `FeedCategoryEnum` to a plain string** (`"podcasts"`). It is a legacy DB sentinel the pre-SP3 feedAllocation degrade path matches on; it is NOT a current bucket. (This retype is what surfaces the dead feedAllocation degrade path as a type error — see §4.)

---

## 2. interestVector.ts — NOT touched (Rule 3)

`interestVector.ts` does **not** gate buckets by the old folded DESIGN buckets. Its `INTEREST_ROOT_TO_PINNED_KEY` / `ENTITY_ROOT_TO_PINNED_KEY` map *interest-taxonomy* roots (the `interests.sql` slugs: `world, climate, markets, crypto, science, health, …`) onto the **8 PINNED archetype keys** (`ai|geopolitics|business|environment|politics|tech|sport|arts`) — which ALREADY equal the 8 picker roots. The values are correct under the new taxonomy; the map logic is unchanged and correct. Per Rule 3 I left it alone.

**Stale-comment note (NOT fixed, Rule 3 — not load-bearing):** the module JSDoc (lines ~24-31) still describes the screen taxonomy as `world_politics|tech_science|markets|sport|culture`. That's now factually outdated but it's a comment, not behavior, and rewriting it is outside the surgical change. Flagging for a future doc-sweep.

---

## 3. Inferred accent color (FLAG for owner review)

- **`ai = #3B82F6`** (brand primary blue) is the ONLY inferred hex. There was no prior dedicated AI accent. `sourceSwipeData.ts` used `ai: #22D3EE`, but that is identical to `tech` (cyan) — a collision that would make AI and Tech chips indistinguishable. I chose the brand primary blue to keep all 8 category accents distinct. **Owner: confirm or supply the intended AI accent.**

**Conflict surfaced (Rule 7):** `sourceSwipeData.ts` `ACCENT_BY_TOPIC` uses `geopolitics: #A78BFA` (purple), whereas the locked app-wide palette (interests.ts, fixtureFeed.ts, old feedBuckets) uses `geopolitics: #EF4444` (red). I picked the locked **red** for `geopolitics` (more widely used, it's the canonical "Geopolitics" dot color) and reused sourceSwipe's **purple `#A78BFA` for `politics`** (the newly-split root). Flag: `sourceSwipeData.ts` still maps `geopolitics → #A78BFA`; a follow-up should align it to the locked red (out of my scope).

---

## 4. Cross-file breakage (HANDOFF — build is RED because of these; I did NOT edit them per instruction)

`npx tsc --noEmit` enumerates the COMPLETE set of errors (7 total), all in **two files NOT assigned to any sub-phase** and outside my permitted scope:

### `src/lib/feedAllocation.ts` (3 errors — dead `podcasts` graceful-degrade path)
- `:259` `deferredBuckets.push("podcasts")` — `deferredBuckets: DesignBucketId[]`; `"podcasts"` is no longer a `DesignBucketId`.
- `:262` `row.allocation_category !== PODCASTS_ENUM_VALUE` — comparison of `FeedCategoryEnum` vs `"podcasts"` (no overlap).
- `:263` `savedEnumValues.delete(PODCASTS_ENUM_VALUE)` — `Set<FeedCategoryEnum>.delete("podcasts")`.
- **Root cause:** the whole pre-0010 "podcasts enum value missing" degrade block (lines ~250-280, `isPodcastsEnumMissingError` + the re-upsert-without-podcasts retry) is DEAD under SP3 — there is no `podcasts` design bucket anymore, so no row can carry it. **Minimal fix:** delete the podcasts-degrade branch entirely (the `if (isPodcastsEnumMissingError(...))` block, the `deferredBuckets.push("podcasts")`, and the now-unused `isPodcastsEnumMissingError` / `PODCASTS_ENUM_VALUE` import). The general upsert error path already surfaces real failures.

### `tests/lib/feedAllocation.test.ts` (4 errors — fixtures pinned to retired ids)
- `:102`, `:161`, `:206` use `"world"`; `:162` uses `"podcasts"` — both retired `DesignBucketId`s. **Minimal fix:** remap fixtures to the new roots (`world → geopolitics`, drop the `podcasts` case / replace with a real bucket) and delete the podcasts-degrade test if the degrade branch is removed.

**Neither file is in any sub-phase's "Files touched."** Per the orchestrator instruction ("if a caller reads a now-removed old key and it's OUTSIDE your two files, NOTE it — don't edit it here"), I did NOT edit them. **Orchestrator decision needed** to assign these (likely a quick SP4 add-on or a dedicated follow-up) so the phase build can go green.

### Decoupled (does NOT break the build, but is now stale — for a follow-up)
- `src/lib/detailTemplates.ts` — defines its OWN `DetailCategory` literal (`world|markets|tech|sport|culture|youtube|podcasts|x`); it does NOT import `DesignBucketId` (only a comment references it), so it compiles fine. It is now taxonomy-drifted from the 10 roots (its comment claims alignment to `DesignBucketId`). Not a build blocker; flag for a taxonomy-sweep. Out of my scope.

### Callers that are SAFE (compiled clean — no change needed)
- `src/components/onboarding/BuildYour30.tsx`, `OnboardingFlow.tsx`, `AppShell.tsx` — consume `DesignBucketId` / `DESIGN_BUCKETS` / the helpers by name; no hardcoded old id strings. They pick up the new 10-id taxonomy transparently (verified: zero tsc errors in them).
- `feedAllocation.ts`'s `DESIGN_BUCKET_TO_ENUM[segment.bucketId]` write path — fine; only the dead podcasts branch breaks.

---

## 5. Code-review findings + fixes (Step B/C)

- **Every place that listed 5 topic buckets now lists 8** — `DesignBucketId`, `DESIGN_BUCKETS`, `DESIGN_BUCKET_TO_ENUM`, `PICKER_ROOT_TO_CATEGORY_BUCKET`, `DEFAULT_ALLOCATION_SEGMENTS`, the JSDoc folds, and all helper examples were updated. Verified by re-grep: no `world`/`markets`/`culture`/`podcasts`/`tech_science`/`world_politics` literal remains in feedBuckets.ts.
- **`DEFAULT_ALLOCATION_SEGMENTS` total = 30** and keys/counts EQUAL the Python twin — asserted by the new test (`tsAllocation` `toEqual` `PYTHON_DEFAULT_FEED_ALLOCATION`). ✓
- **Identity maps truly identity** — asserted (`DESIGN_BUCKET_TO_ENUM[id] === id` for all 10; `PICKER_ROOT_TO_CATEGORY_BUCKET[root] === root` for all 8). ✓
- **`PODCASTS_ENUM_VALUE` retype** — chose plain string over removing it, to keep the export surface stable for feedAllocation.ts until the orchestrator retires that branch (smaller blast radius than deleting the export).
- **`SOURCE_TYPE_TO_DESIGN_BUCKET.podcast`** — was `podcasts` (now invalid); rerouted to `youtube` rather than dropping the source type, so a followed podcast still seeds a source block. Encoded in a new test.
- No critical/high issues found in my two files. The load-bearing finding is the cross-file feedAllocation breakage (§4), deferred to the orchestrator per scope.

---

## 6. Validation (Step D)

```
$ npx vitest run tests/lib/feedBuckets.test.ts
 Test Files  1 passed (1)
      Tests  32 passed (32)         ← incl. Python-twin equality + identity-map assertions

$ npx tsc --noEmit          (full project typecheck — enumerates ALL errors)
src/lib/feedAllocation.ts(259,30): TS2345  "podcasts" not assignable to DesignBucketId
src/lib/feedAllocation.ts(262,64): TS2367  comparison has no overlap
src/lib/feedAllocation.ts(263,32): TS2345  "podcasts" not assignable to DesignBucketId
tests/lib/feedAllocation.test.ts(102/161/206): TS2322  "world" not assignable
tests/lib/feedAllocation.test.ts(162): TS2322  "podcasts" not assignable
  → 7 errors, ALL in 2 UNASSIGNED files outside my scope; ZERO in feedBuckets.ts / interestVector.ts / my test.

$ npm run build
 ✗ FAILS at src/lib/feedAllocation.ts:259 (same root cause).
```

**Validation: my files PASS (32/32 tests, 0 tsc errors in them). Project build FAIL — caused solely by the two unassigned files in §4.** Surfaced, not masked (Rule 12). I did not attempt a fix in those files because the instruction explicitly scopes me to two files and says NOTE-don't-edit cross-file breakage.

---

## 7. Definition of done (Step E)

- `npm run build` green — **FAIL** (blocked by unassigned `feedAllocation.ts` + its test; NOT by my files). Needs the §4 handoff resolved.
- feedBuckets suite asserts TS default segments equal the Python default allocation (same keys+counts, total 30) — **PASS** (new test).
- feedBuckets suite asserts `PICKER_ROOT_TO_CATEGORY_BUCKET` maps each of the 8 roots to itself — **PASS** (new test).

**Definition of done: PARTIAL — the two assertion DoDs PASS; the build-green DoD is blocked by cross-file breakage I was instructed not to fix.**

---

## 8. Concerns

1. **Build is RED and SP2 cannot make it green alone.** `feedAllocation.ts` (dead podcasts degrade path) + `tests/lib/feedAllocation.test.ts` (old-id fixtures) need the §4 minimal fixes. They are unassigned. **Orchestrator must assign them** (small — ~delete one branch + remap a few fixtures) before the phase can commit on a green build.
2. **`ai = #3B82F6` is inferred** — owner should confirm the intended AI chip accent.
3. **`geopolitics` accent conflict** between sourceSwipeData (purple) and the locked palette (red) — I used red for geopolitics + purple for politics; sourceSwipeData should be realigned in a follow-up.
4. **`detailTemplates.ts`** carries a parallel, now-drifted `DetailCategory` literal (still on `world/markets/culture/podcasts`). Compiles, but is taxonomy-stale — flag for a sweep.
5. **interestVector.ts JSDoc** describes the retired screen taxonomy; comment-only, left per Rule 3.
6. Did NOT commit; touched only `src/lib/feedBuckets.ts` + `tests/lib/feedBuckets.test.ts`.

---

## Return to orchestrator
1. **STATUS: SUCCESS** (my two files + test are correct and self-consistent) **— but project build RED** from unassigned collateral.
2. **Files touched:** `src/lib/feedBuckets.ts`, `tests/lib/feedBuckets.test.ts`. (interestVector.ts intentionally untouched.)
3. **Validation:** feedBuckets suite PASS (32/32, incl. twin-equality + identity); full `tsc`/`build` FAIL — 7 errors, all in `src/lib/feedAllocation.ts` (3) + `tests/lib/feedAllocation.test.ts` (4), NONE in my files.
4. **Definition of done:** the 2 assertion DoDs PASS; build-green DoD FAIL (blocked by §4 cross-file breakage I was told to NOTE-not-edit).
5. **Concerns:** assign the §4 feedAllocation fixes (minimal) to unblock a green build; confirm the inferred `ai` accent; realign sourceSwipeData `geopolitics`; sweep `detailTemplates.ts` + interestVector JSDoc.
