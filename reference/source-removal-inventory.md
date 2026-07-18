# Reference — Source-System Removal Inventory

**Why this doc exists:** WS-D of `plans/prd.md` removes the entire source-follow system (YouTube channels, X accounts, podcasts, personalities) across ~92 deleted files and ~27 surgically-edited ones. Re-deriving that list per slice is expensive and — because of three name collisions — genuinely dangerous. This is the authoritative delete/edit/keep list, verified against the working tree on 2026-07-18 (branch `claude/feed-source-revamp-plan-388edf`).

**When to update:** as each removal stage lands (tick items off), or if new source-touching code appears before removal completes. **Delete this doc once WS-D is done and the grep-clean test in `plans/prd.md` is green** — it has no purpose afterward.

---

## 0. Read this first — three deletion hazards

These match a "source"-ish grep and **must NOT be deleted**. A path-glob-driven removal would take out the story-clustering engine.

| Path | What it actually is |
|---|---|
| `agents/pipeline/clustering/*` | **Story** clustering (same-event dedup). Nothing to do with source follows. |
| `agents/pipeline/importance/source_tiers.py` | **Outlet authority** tiering — load-bearing for the WS-B notability gate. |
| `scripts/seed_catalog/backfill_interest_query.py`, `backfill_queryless_interests.py` | **Interest taxonomy** work that happens to live in the seed_catalog dir. |

Also **verify before deleting**: `agents/ingestion/adapters/base.py` (the `SourceAdapter` ABC is shared — GDELT implements it too) and the `archetypes` table (`0009:218` — confirm nothing else reads it).

## 1. Stage order (safe by construction)

From `plans/prd.md` decision 7. Each stage is independently shippable; the order is what makes it safe.

1. **Confirm dormancy, then stop ingestion.** Verify in writing that `RUN_SOURCES` and `/ingestion/sources` are inert (see §2), then delete the Trigger.dev cron **before** removing any worker route.
2. **UI + allocation prune — one atomic change** across all three twins (§5), rebalanced to 30 topic slots. Splitting this reserves phantom slots on every new feed.
3. **Pipeline module deletion** (§3) + their tests.
4. **DB last**, via new forward migrations only (§6).
5. **Config/env cleanup** (§7).

## 2. Dormancy findings — confirm these before treating removal as a behavior change

Two flags appear **already dead**, which makes the live blast radius far smaller than the file count suggests:

- **`RUN_SOURCES`** — referenced only in a comment at `.env.example:119`. **No code reads it.**
- **`trigger/sourceIngestion.ts:47`** POSTs every 2h to `INGESTION_SOURCES_PATH = "/ingestion/sources"`, which is **not a registered worker route**. The complete worker route set is: `/healthz`, `/pipeline/daily`, `/feed/assemble-for-user`, `/api/interview/turn`, `/api/sources/search`, `/api/story/{id}/question`, `/api/story/{id}/corpus`, `/api/voice/live-token`. The cron has been firing into a 404, or was never enabled.

**Therefore the only live source surfaces are** `/api/sources/search` and the x-theme path gated behind `RUN_X_THEMES=0`. Confirm both findings against the deployed Railway worker and the Trigger.dev dashboard before proceeding (Rule 12 — do not assume).

## 3. Python — delete (~24 modules + ~22 test files)

**Adapters** (`agents/ingestion/adapters/`): `youtube.py` (820L), `x_account.py` (583L), `x_resolver.py` (314L), `xai_client.py` (202L). `__init__.py` and `base.py` are **surgical** — they re-export/share with GDELT.

**Ingestion core:** `source_pipeline.py` (457L, `run_source_ingestion`), `scheduler.py` (175L, `CadenceScheduler` keyed by `content_source_type`), `cluster_sweep.py` (789L), `tweet_screenshot.py` (205L), `x_theme_reel.py` (123L).

**Pipeline x-theme:** `agents/pipeline/x_theme_ladder.py` (328L), `agents/pipeline/x_theme_production.py` (305L).

**Catalog** (source-cluster resolution only): `agents/catalog/` — `cluster_query.py`, `cluster_resolver.py`, `models.py`, `__init__.py`.

**Scripts:** `scripts/produce_source_reels.py` (486L), `scripts/probe_ash_sources.py`, `scripts/theme_miss_counts.py`, `scripts/seed_catalog/` (`youtube_resolve.py`, `x_resolve.py`, `itunes_resolve.py`, `seed_v2_x.py`, `seed_v2.py`, `seed_catalog.py`, `generate_candidates.py`, `candidate_validation.py`, `prompts.py`, `seed_via_pooler.py`, `data/`) — **but keep the two `backfill_*.py` files** (§0).

**Tests:** `tests/agents/ingestion/adapters/{test_youtube,test_x_account}.py`; `tests/agents/ingestion/{test_source_pipeline,test_scheduler,test_cluster_sweep,test_tweet_screenshot,test_x_resolver,test_x_theme_reel}.py`; `tests/agents/pipeline/{test_x_theme_ladder,test_x_theme_production,test_orchestrator_source_poster,test_poster_skip_source}.py`; `tests/agents/catalog/{test_cluster_query_fixture,test_cluster_resolver}.py`; `tests/agents/qa/test_source_search.py`; `tests/scripts/test_produce_source_reels_rebuild.py`; `tests/scripts/seed_catalog/{test_seed_catalog,test_seed_v2,test_seed_v2_x}.py`; `tests/seed/test_source_clusters_seed.py`; `tests/supabase/test_migration_0022_source_clusters.py`.

## 4. Frontend — delete (~18 files + ~16 tests, ~5,900 LOC)

**`src/components/sources/` (all 10):** `SourceSwipe.tsx` (572L), `SourceClusterGrid.tsx`, `SourceClusterScreen.tsx`, `ProfileCurtain.tsx`, `ClusterCard.tsx`, `SourceCard.tsx`, `SourceArtwork.tsx`, `SourceSwipeCard.tsx`, `sourceSwipeGlyphs.tsx`, `SignalOrb.tsx`.

**Library screens:** `src/components/blip/library/{SourcesScreen,SourcesAddControls,SourceAvatarImage}.tsx`.

**Onboarding pickers:** `src/components/onboarding/{XClusterPicker,YoutubeChannelPicker}.tsx`.

**lib + types (2,592L):** `src/lib/{sources,sourceClusters,sourceSearch,sourceSwipeData,sourceRecommendations,clusterSelection,onboardingSourcePicks}.ts`, `src/types/source.ts`.

**Tests:** `tests/lib/{sources,sourceSearch,sourceClusters,sourceRecommendations,sourceSwipeData,clusterSelection,followedSourcesWithPriority}.test.ts`; `tests/lib/sources/` (5 files); `tests/lib/onboarding/{onboardingSourcePicks.test.ts,xClusterPicker.test.tsx,youtubeChannelPicker.test.tsx}`; `tests/lib/trigger/sourceIngestion.test.ts`.

**No routes to remove** — the app is 4 pages; Sources is a *tab* inside `AppShell.tsx`.

## 5. Allocation — the three twins (change together, one commit)

Current state: **26 topic slots + 4 source slots = 30**. Target: **30 topic slots, 0 source**, per PRD decision 9 → `ai 5 · tech 5 · geopolitics 5 · business 5 · politics 2 · environment 2 · sport 3 · arts 3`.

| File | Symbol | Current source content |
|---|---|---|
| `src/lib/feedBuckets.ts:164-175` | `DEFAULT_ALLOCATION_SEGMENTS` | `["youtube", 2], ["x", 2]`; `ALLOCATION_TOTAL = 30` at `:113` |
| `src/lib/feedBuckets.ts:96-97` | `DESIGN_BUCKETS` | the two `kind: "src"` rows (`youtube`, `x`) |
| `src/lib/feedBuckets.ts:359-364` | `SOURCE_TYPE_TO_DESIGN_BUCKET` | `youtube_channel→youtube`, `podcast→youtube`, `x_account→x`, `personality→x` |
| `src/lib/feedBuckets.ts:49-50, 152, ~385` | `DesignBucketId`, `PODCASTS_ENUM_VALUE`, `sourceBucketsFromFollows()` | union members + legacy sentinel + helper |
| **`agents/pipeline/categories.py:138-150`** | **`DEFAULT_FEED_ALLOCATION`** | `"youtube": 2, "x": 2` — **the twin the brief missed** |
| `agents/pipeline/categories.py:59, 82, 168-169` | `FEED_CATEGORIES`, `SOURCE_CATEGORIES`, `CATEGORY_FLOOR` | includes `youtube`/`x`; floors already 0 |
| `agents/pipeline/niche_allocation.py:206-241, 128, 55-56` | `_source_rows()` | **consumes** budgets; source rows sort **first**, so removal shifts every downstream `sort_order` |

## 6. DB — drop via new forward migrations only (last stage)

Apply via IPv4 session pooler on **`:6543`** (`:5432` times out — 2026-06-17 precedent) and record in `schema_migrations`.

**Drop order:** x-theme objects → `content_sources` + siblings → taxonomy roots.

| Object | Kind | Created in |
|---|---|---|
| `daily_feeds.feed_x_theme_rung`, `.feed_x_theme_attribution` | columns | `0032` (**ships a commented rollback at :70-71 — reuse it**) |
| `x_cluster_sweeps` | table | `0031:38` |
| `user_source_clusters` (+ `idx_user_source_clusters_cluster`) | table | `0033:31,41` |
| `source_clusters` (+ `idx_source_clusters_category`), `source_cluster_members` | tables | `0022:34,~55` |
| `source_clusters.cluster_subniche`, `.cluster_description` | columns | `0028:17-18` |
| `content_sources` (+3 indexes), `user_content_sources`, `content_source_items`, `personalities`, `user_personalities`, `personality_appearances`, `archetypes` | tables | `0009:92,121,140,163,186,199,218` |
| `content_source_type`, `source_priority` | enums | `0009:70,81` |
| RLS `content_sources_user_added_insert` / `_update` | policies | `0016:22,31` |
| seed/test SQL: `supabase/seed/source_clusters.sql`, `supabase/tests/0022_source_clusters_assertions.sql` | files | — |

**Do NOT drop:**
- `feed_category` enum values `'youtube'`, `'x'` (`0008:61,64`) and `'podcasts'` (`0010:28`). Postgres enum-value drops need a type swap — not worth it. Block writes at the app layer (PRD decision 9).
- `feed_slot_kind` — it is plain `text not null default 'interest'` on `daily_feeds` (`0003:131`), **not an enum**. The `'source'` value is application-level only (`SLOT_KIND_SOURCE` in `feed_assembly.py`). No migration; just stop writing it.

## 7. Config / env

`agents/shared/settings.py`: `youtube_api_key` (:58), `youtube_cookiefile` (:65), `youtube_cookies_from_browser` (:74), `youtube_pace_seconds` (:81), `youtube_pace_jitter_seconds` (:90), `xai_api_key` (:98). **Keep `serper_api_key` (:54)** — poster/image pipeline, not sources.

`.env.example`: `YOUTUBE_API_KEY` (:34), `YOUTUBE_COOKIEFILE` (:41), `YOUTUBE_COOKIES_FROM_BROWSER` (:42), `YOUTUBE_PACE_SECONDS` (:46), `YOUTUBE_PACE_JITTER_SECONDS` (:47), `RUN_X_THEMES=0` (:120), and the dead `RUN_SOURCES` comment (:119). Note `XAI_API_KEY` exists in `.env` but is absent from `.env.example` (pre-existing documentation gap). Also purge these from the Railway worker env.

## 8. Trigger.dev

Delete `trigger/sourceIngestion.ts` (~270L — `SOURCE_INGESTION_CRON = "0 */2 * * *"` at :44, task id `"source-ingestion"` at :252-254, reads `user_content_sources` via service role at :112) and `tests/lib/trigger/sourceIngestion.test.ts`. Verify whether `trigger.config.ts` globs `trigger/*.ts`. `dailyPipeline.ts` and `feedReadinessCheck.ts` stay.

## 9. Shared files — surgical edits, DO NOT DELETE (~27)

**Python (13):**
- `agents/pipeline/feed_assembly.py` — heaviest interleave: `SLOT_KIND_SOURCE` (~179-191), `AllocatedSlot.feed_x_theme_rung` (:263) / `.feed_x_theme_attribution` (:267), x-theme ladder import (:72-75), source-slot placement (:1299, :1312), persistence dict (:1461), soft-roll (:601, :730). Keep all `SLOT_KIND_INTEREST` machinery.
- `agents/pipeline/daily_batch.py` — imports (:30, :64-68), `enable_x_theme_reels` (:780, :857), gather/merge block **:1058-1176** (largest single excision).
- `agents/pipeline/orchestrator.py` — import (:45), `x_theme_candidates_by_user` (:841, :872), passthrough (:940-947).
- `agents/pipeline/categories.py`, `niche_allocation.py` — see §5.
- `agents/worker/main.py` — remove the `/api/sources/search` block and its helpers (`SourceSearchRequest/Result/Response`, `_log_source_search_error`, YouTube/podcast/X search helpers), roughly **:646-1150**, plus the route allowlist entry at `:90`.
- `agents/worker/pipeline_routes.py` — `RUN_X_THEMES` gate (:504). **GDELT ingestion at :375-441 must survive.**
- `agents/ingestion/adapters/__init__.py`, `base.py` — shared with GDELT.
- `agents/pipeline/produce_gate.py`, `summary_mode.py` — incidental refs.
- `agents/pipeline/sim/ranking_sim.py` — slot-kind mix assertions (:182-293).
- `agents/interview/constants.py:5` — comment only.
- `scripts/run_live_batch.py`.

**Frontend (9):**
- `src/components/app/AppShell.tsx` — drop `SourcesScreen` import (:30), the `"sources"` tab (:117), `getFollowedSources`/`sourceBucketsFromFollows` (:34, :37, :71, :77), `thirtyBackedBuckets.source` (:56, :81, :129). **Tab bar goes 5 → 4.**
- `src/lib/onboardingTerminal.ts` — **51 hits, densest frontend interleave.** Remove `persistSourceFollows()` (:98-140), `PersistSourceFollowsResult` (:79-87), imports (:49-50), `sourceFollows` field (:75).
- `src/lib/feed/supabaseFeed.ts` — `XThemeRung` coercion (:34-38), select columns (:259-260, :305), row mapping (:344).
- `src/lib/reel/sectionChips.ts` — `xThemeChip()` (:59-68), `X_RUNG_CHIP_LABEL`, the `feed_slot_kind === "source"` branch (:94), dispatch (:142-144). Keep interest/section chips.
- `src/types/feed.ts` — remove `feed_x_theme_rung` (:242), `feed_x_theme_attribution` (:249), `XThemeRung` (:253), `XThemeAttribution` (:261); narrow `feed_slot_kind` (:187) from `"interest" | "source"` → `"interest"`.
- `src/types/interview.ts` — `InterviewClusterPick` (:80-93), `InterviewSourceFollows` (:103-107), `source_follows?` (:142).
- `src/components/onboarding/BuildYour30.tsx` — 29 hits; `followedSourceBuckets` prop, source-axis chips.
- `src/lib/feedBuckets.ts` — see §5.
- `src/lib/portraitBg.ts` (7 hits), `src/lib/interestVector.ts` (4 incidental hits).

## 10. Docs to delete after removal

`reference/source-catalog-taxonomy.md`, `reference/source-reels-spec.md`, `reference/sources-reuse-map.md`, and this file. Rewrite `reference/interview-onboarding-spec.md` (5→3 phases) as part of WS-E; sync `reference/supabase-schema.md`, `reference/api-contracts.md`, and README at the end of WS-D.

## 11. Post-inventory additions (WS-A slices #45 / #62, 2026-07-18)

This inventory predates WS-A. Slices #45 (`5012c70`) and #62 (`5ae3493`) each shipped a **source-origin exemption** so the new headline gates would not drop YouTube/X reels while those still existed. Every one of them is dead once removal lands, and none appear in §3/§9 above — §55's "lean on the inventory, do not re-derive" would otherwise miss them.

The greppable removal criterion: `agents/ingestion/dedup.py::is_source_origin_domain` becomes a **constant-`False` predicate** once source ingestion is gone, so every branch it guards is dead code. Delete the branch, not just the call.

- `agents/pipeline/orchestrator.py:338` and `agents/pipeline/persist_helpers.py:710` — write-gate exemptions (#45). Remove the guard; the gate then applies unconditionally.
- `agents/shared/persisted_headline_gate.py` (**new file**, #62) — `load_source_origin_story_ids()` (:73) deletes outright; drop the `source_origin_ids` parameter from `persisted_headline_rejection_reason()` (:34). The module itself **stays** — it is the read-side gate, not source machinery.
- `agents/worker/pipeline_routes.py:633, :674, :681` — import, lookup call, and pass-through of the exemption.
- `scripts/retire_unpublishable_headlines.py` (**new file**, #62) — exemption threaded throughout (:59 import, :76/:88 param, :101 `exempt`, :224-232 two-pass lookup, :245 report line). The two-pass structure exists *only* to serve the exemption; collapse it to one pass.
- `agents/shared/headline_quality.py` (**new file**, #45) — **keep, no source references.** Listed here so it is not swept up by a `grep -l source` pass.

Note for the §55 grep-clean test: `is_source_origin_domain` also has a **pre-existing** caller at `agents/pipeline/summary_mode.py:66` that is not from WS-A — classify it against §0 before touching it.
