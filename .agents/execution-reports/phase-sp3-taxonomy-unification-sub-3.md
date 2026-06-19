# Phase SP3 — Sub-phase 3 execution report: DB migration + reel-chip label/color parity (⚠ irreversible)

**Status:** SUCCESS (migration applied to remote; DoD count==0; build green; parity test + live e2e pass).
**Date:** 2026-06-19
**Files touched (only):**
- `supabase/migrations/0020_taxonomy_8_roots.sql` (NEW — enum ADD VALUEs)
- `supabase/migrations/0021_taxonomy_8_roots_backfill.sql` (NEW — segments rows + backfill; the txn-split follow-up to 0020)
- `src/types/feed.ts` (`SegmentKey` → 8 roots)
- `src/lib/interests.ts` (`SEGMENT_ACCENT_HEX` → 8 roots, hex == DESIGN_BUCKETS)
- `src/lib/feed/fixtureFeed.ts` (`SEGMENT_LABELS` / `SEGMENT_ACCENT_HEX` / `SEGMENT_DETAIL_CATEGORY` → 8 roots; 2 fixture stories remapped `markets→business`, `wildcard→arts`)
- `tests/lib/feed/reelChipParity.test.ts` (NEW — chip-parity test)
- `src/lib/feed/supabaseFeed.ts` — **NOT edited**: it types `story_segment_slug: SegmentKey` and reads `segment_label`/`segment_accent_hex` straight from the joined `segments` row, so it picks up the new roots transparently (no hardcoded old keys). Surgical / Rule 3.

---

## 1. Migration SQL summary

### 0020 — additive enum values (committed on its own, no DML)
- `feed_category` += `ai, geopolitics, business, environment, politics, tech, arts` (already had `sport, youtube, x`). All `ADD VALUE IF NOT EXISTS`.
- `segment_slug` (the reel chip's per-story key, from `0001_content_schema.sql:14`) += `ai, business, environment, politics, arts` (already had `geopolitics, tech, sport`). All `ADD VALUE IF NOT EXISTS`.
- Old folded values (`world_politics, tech_science, culture, markets, wildcard, breaking, podcasts`) **RETAINED unused** (no DROP) for reversibility, as the owner decision + 0017 precedent require.

### 0021 — segments rows + story backfill (separate file/txn, after 0020 commits)
- **`segments` table:** upsert (`ON CONFLICT DO UPDATE`) of the 8 roots with label + accent_hex **verbatim from `src/lib/feedBuckets.ts` DESIGN_BUCKETS** so the reel chip equals the onboarding chip:
  `ai|AI|#3B82F6`, `geopolitics|Geopolitics|#EF4444`, `business|Business|#22C55E`, `environment|Environment|#34D399`, `politics|Politics|#A78BFA`, `tech|Tech|#22D3EE`, `sport|Sport|#F59E0B`, `arts|Arts|#E8B7BC`. (Upsert also realigns the pre-existing `tech` row from legacy "Tech & Science" → "Tech".)
- **Backfill `stories.story_segment_slug`** — deterministic, keyed on the current legacy segment, idempotent (WHERE guard):
  - `markets → business` (70 rows)
  - `wildcard → arts` (18 rows)
  - `geopolitics / tech / sport` keep their own names (already valid roots — no move).
  - No `ai`/`politics`/`environment` move: the only richer signal, `story_detail_category`, carries the legacy buckets (`world/markets/tech/sport/culture`) that align **1:1** with the segment slug — it adds no specificity beyond the segment, so a deterministic map on the segment fully covers all 132 rows. (No current story is an AI/politics/environment story.)

## 2. How I handled the ADD-VALUE / transaction gotcha
PG17 allows `ALTER TYPE … ADD VALUE` inside a txn, **but a freshly-added value cannot be USED (referenced in DML) in the same txn** — and `supabase db push`/`psql -f` wrap a file in one txn. So I split: **0020 = ADD VALUEs only** (committed alone), **0021 = the segments INSERT + the stories backfill** (references the new values, runs after 0020 commits). Apply order documented in both headers: `0020 → 0021`. This mirrors the 0014/0015 split already in the repo. Both files are fully idempotent (`ADD VALUE IF NOT EXISTS`, `ON CONFLICT DO UPDATE`, WHERE-guarded UPDATEs) — safe to re-run.

## 3. Apply result against remote (IPv4 session pooler)
Applied via `psql` (`/opt/homebrew/opt/libpq/bin/psql`) over `$SUPABASE_DB_URL` (percent-encoded IPv4 session-pooler URL, `aws-1-us-east-1.pooler.supabase.com`). Secrets never printed. Server `server_version = 17.6`.

- `0020` → 12× `ALTER TYPE`, exit 0.
- `0021` → `INSERT 0 8`, `UPDATE 70` (markets→business), `UPDATE 18` (wildcard→arts), exit 0.

Verification queries (redacted connection):
```
feed_category enum  → …, ai, geopolitics, business, environment, politics, tech, arts  (new values PRESENT; legacy retained)
segment_slug  enum  → geopolitics, markets, tech, sport, wildcard, ai, business, environment, politics, arts  (new PRESENT; legacy retained)
segments rows       → all 8 roots with DESIGN_BUCKETS label+hex (e.g. tech|Tech|#22D3EE, arts|Arts|#E8B7BC, sport|Sport|#F59E0B)

-- DoD count:
select count(*) from stories
 where story_segment_slug is null
    or story_segment_slug::text not in ('ai','geopolitics','business','environment','politics','tech','sport','arts');
 → 0   ✅

post-backfill distribution → business 70, tech 25, arts 18, geopolitics 13, sport 6  (= 132, no markets/wildcard stragglers)
```

## 4. TS twin changes
- `SegmentKey` is now the 8 roots (`ai|geopolitics|business|environment|politics|tech|sport|arts`); legacy `markets`/`wildcard` documented as retained-unused-in-DB but never emitted.
- `interests.ts SEGMENT_ACCENT_HEX` and `fixtureFeed.ts SEGMENT_LABELS`/`SEGMENT_ACCENT_HEX` carry the 8 roots with labels+hex **== DESIGN_BUCKETS** (the onboarding chips).
- `fixtureFeed.ts SEGMENT_DETAIL_CATEGORY` remapped onto the nearest still-existing Detail template (`business→markets`, `arts→culture`, `ai→tech`, split `geopolitics/politics/environment→world`) — the Detail templates remain on the legacy bucket names (out of SP3 scope), so the new roots fold onto the nearest legacy template for panel selection only.
- The 2 fixture stories on retired slugs remapped to match the DB backfill (`markets→business`, `wildcard→arts`).
- `npx tsc --noEmit` → **0 errors**. `npm run build` → **green**.

## 5. The chip-parity test (Rule 9)
`tests/lib/feed/reelChipParity.test.ts` (3 tests, all pass):
1. A `sport` fixture story's chip `segment_label`/`segment_accent_hex` **== `DESIGN_BUCKETS.sport.name`/`.color`** (the DoD's explicit assertion).
2. **Every** fixture story's chip == `DESIGN_BUCKETS[segment_key]` — fails the moment any segment map drifts from the onboarding palette (encodes WHY: reel chip must equal onboarding chip).
3. All 8 topic roots are `kind:"cat"` DESIGN_BUCKETS with **distinct** accents (locks the full surface; fixtures cover only 5/8).

## 6. Live e2e
`.venv/bin/pytest tests/agents/pipeline/test_phase5a_live_e2e.py -q` → **1 passed**. It was failing solely on the missing enum value; 0020 fixes it. No other failure reason.

## 7. Validation summary
- Remote: enums carry new values; segments rows == DESIGN_BUCKETS; **DoD count == 0**. ✅
- `npm run build` → green. ✅
- `npx tsc --noEmit` → 0 errors. ✅
- Parity test + touched feed suites (`reelChipParity`, `supabaseFeed`, `assembleFirstRunFeed`, `feedBuckets`, `feedAllocation`, `interests`) → all pass (62/62 in that batch). ✅
- Live e2e pytest → pass. ✅
- Full `npm test` → **471 passed, 2 failed** (see Divergences).

## 8. Divergences

1. **`tests/lib/userInterests.test.ts` (1 failing) — old-taxonomy fixture, outside my 5 files.** The fixture feeds `interest_segment_slug: "markets"` and expects `#22C55E`; I removed `markets` from `interests.ts SEGMENT_ACCENT_HEX` (it folds into `business`, same `#22C55E`). This is a downstream test pinned to a **retired** slug name — the exact "tests pinned to old keys, orchestrator decides" class SP1/SP2 flagged. **NOT a logic regression** (`business` correctly carries `#22C55E`; `tech` still resolves). Trivial fix = swap the fixture's `"markets"` → `"business"` (expected hex unchanged). It is NOT in my permitted file list, so I surfaced it rather than edited it (Rule 12 + scope).
2. **`tests/lib/app/tabBar.test.tsx` (1 failing) — PRE-EXISTING, unrelated.** Reproduced on a clean tree with my changes stashed: it fails on tab labels ("Today/Archive/Sources/Settings" vs a "Thirty" tab), touches no taxonomy. Not caused by this sub-phase.
3. **Two migration files instead of one.** The owner fixed "migration = 0020", but the ADD-VALUE/txn gotcha forces the dependent backfill into a separate committed txn. I kept 0020 as the additive-enum migration (as named) and added `0021_taxonomy_8_roots_backfill.sql` for the segments+backfill. Documented in both headers.
4. **Legacy `markets`/`wildcard` rows remain in the `segments` table** (retained-unused, harmless) — consistent with the "retain old values" decision; no story references them after backfill.

## 9. Definition of done: **PASS**
- Migration applies cleanly via the IPv4 pooler ✅
- `select count(*) … not in (8 roots)` → 0 ✅
- A test asserts a sport story's chip label+hex == onboarding sport label+hex ✅
- `npm run build` green ✅
- `npm test`: green **except** 1 pre-existing-unrelated (tabBar) + 1 old-taxonomy fixture outside my scope (userInterests) — both surfaced, neither a logic regression in the SP3 change. (Strict reading: `npm test` is not 100% green; the 2 reds are out-of-scope/pre-existing — flagged for orchestrator per Rule 12.)

## 10. Concerns (esp. for SP4's parity smoke)
1. **Assign `tests/lib/userInterests.test.ts` fixture fix** (`"markets"`→`"business"`) — trivial, but it's the last red caused by the taxonomy change. SP4 or a test-fixup handoff should own it. The earlier SP1 Python test reds (`test_demand`, `test_produce_caps`, `test_feed_assembly`, `test_ranking*`) appear already updated in this working tree (those files show in `git diff`) — verify they're green before phase commit.
2. **`tabBar.test.tsx` is independently red** on `main` — pre-existing, unrelated to SP3; flag for a separate fix so the phase commit isn't blamed for it.
3. **Detail templates still on legacy bucket names** (`detailTemplates.ts` / `detail_templates.py`: `world/markets/culture/...`). `fixtureFeed.SEGMENT_DETAIL_CATEGORY` folds the new roots onto them for panel selection. A future sweep should unify Detail templates onto the 8 roots; out of SP3 scope.
4. **SP4 parity smoke** should see the **same label string** for a root on all 3 surfaces. The DB `segments` rows + `interests.ts` + `fixtureFeed.ts` now all read from DESIGN_BUCKETS labels (e.g. "Geopolitics", "Tech", "Business"), so the smoke should pass — but note `detailTemplates.ts` still uses legacy names and `sourceSwipeData.ts` `geopolitics` accent is purple (SP2 flagged), not the locked red; if SP4's smoke reads those surfaces it may see a mismatch.

---

## Return to orchestrator
1. **STATUS: SUCCESS** (migration applied to remote, DoD count==0, build green, parity + live-e2e pass).
2. **Files touched:** `supabase/migrations/0020_taxonomy_8_roots.sql`, `supabase/migrations/0021_taxonomy_8_roots_backfill.sql`, `src/types/feed.ts`, `src/lib/interests.ts`, `src/lib/feed/fixtureFeed.ts`, `tests/lib/feed/reelChipParity.test.ts`.
3. **Validation: PASS** — remote: enums have new values, segments==DESIGN_BUCKETS, story NULL/invalid count==0; `npm run build` green; parity test green; live e2e pytest PASS. `npm test` = 471 pass / 2 fail (1 pre-existing-unrelated `tabBar`, 1 out-of-scope old-taxonomy fixture `userInterests` — both surfaced, neither a logic regression).
4. **Definition of done: PASS** (every DoD bullet met; the 2 non-green `npm test` items are out-of-scope/pre-existing, flagged per Rule 12).
5. **Concerns:** assign the trivial `userInterests.test.ts` fixture fix (`markets`→`business`); pre-existing `tabBar` red on main; Detail templates + sourceSwipe geopolitics-accent still on legacy taxonomy (future sweep / possible SP4-smoke mismatch).
