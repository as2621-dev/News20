# Product Brief — Feed Quality Reset & Source-System Removal

**Date:** 2026-07-18
**Author:** founder (Ashesh) + orchestrated 5-agent investigation of the 2026-07-07 briefing
**Status:** Approved direction. Input for `/cto` → PRD → `/to-issues`.
**Supersedes:** the YouTube/X/podcast/personality "Sources" concept in `plans/m5-m6-personalization-sources-control-surface.md` (that surface is now being removed, not extended).

---

## 1. Problem

The daily 30-reel briefing is not trustworthy. On the 2026-07-07 briefing for the founder's account (ash@gmail.com, prod data pulled and audited row-by-row):

- Only **~5 of 30 reels** genuinely matched their displayed category tag.
- **11 of 30** reels were junk/wildcard filler (venison donation, CBSE school-board news, an obscure REIT press release, "federal funding in Hawaii", a source masthead as a headline).
- Category tags contradicted content (geopolitics story tagged **sport**, Bitcoin tagged **geopolitics**, OPEC tagged **tech**, DeepMind tagged **wildcard**).
- The Build-My-30 contract ("first 5 slots = AI") *appeared* ignored.
- The 4 YouTube + X slots produced **zero** reels and their budgets silently spilled into other categories (geopolitics overfilled to 8, sport to 9, business filled 2 of 6).

This is a product-trust failure: the user configured exactly what they wanted and the product visibly did not honor it.

## 2. Root causes (all confirmed in code + prod data, 2026-07-18)

| # | Root cause | Where | Effect |
|---|---|---|---|
| RC1 | **Stale segment validation set.** `_VALID_SEGMENT_SLUGS` is still the pre-taxonomy 5-set `{geopolitics, markets, tech, sport, wildcard}` with default `"wildcard"`; never widened when the 8-root taxonomy landed (migrations 0020/0023). | `agents/pipeline/persist_helpers.py:44-47,547-586` | Every ai/business/environment/politics/arts story persists mislabeled: DeepMind→wildcard, Bitcoin→geopolitics, OPEC→tech. Prod 07-07: **0 of 67** stories tagged ai/business/arts. The TS side (`src/types/feed.ts:38-41`) already assumes wildcard/markets are "no longer emitted" — Py/TS contract break. |
| RC2 | **No notability gate exists in the live path.** Admission = ANY single anchor term regex-matching the GDELT title/entity haystack; affinity alone (0.5) clears the ranking threshold (0.20); importance floors (0.05) sit below a single-outlet story's score (0.083). The trusted authority-outlet ingestion filter (`ingest_trusted_outlets` + domain filter) is **built and tested but has zero production callers**. | `agents/ingestion/adapters/gdelt_bigquery.py:207-257`, `agents/pipeline/produce_gate.py:66-67`, `agents/pipeline/ranking.py:84,552-556`, `agents/ingestion/interest_keyed_pipeline.py:512` | One-outlet local stories and PR-wire items reach the top-30. |
| RC3 | **Keyword-match false positives.** Single generic terms admit and tag stories: "Foundation"→AI interests (venison, SCP film), "Federal"→interest-rates-fed (Hawaii), "India"→cricket.india (Indonesia port story — also the source of its sport tag), "data center"→data-center-buildout (zoning lawsuit). | same `_BATCH_SQL` term match + SP1 `story_interests` tagging | Junk fills the very slots the user cares most about (his 5 AI slots were nearly all false positives). |
| RC4 | **Headline provenance broken.** GDELT `<PAGE_TITLE>` can be a site masthead; cluster representative = *earliest-published* member (worst title wins); editorial rewrite is fail-open; no sanity check (title == outlet name, too-short). | `gdelt_bigquery.py:214`, `agents/ingestion/dedup.py:186-190,326`, `agents/pipeline/stages/editorial.py:124-132`, `agents/pipeline/persist.py:411` | Reel headlined literally "Language Magazine". |
| RC5 | **The honest niche assembler never runs.** `assemble_niche_feed` self-delegates to the old coarse `assemble_user_feed` when no allocation row has `allocation_interest_id` (guard at `feed_assembly.py:1121`); Build-My-30 saves coarse-only rows (founder decision 2026-07-05) and `build_niche_allocation` is only invoked by tests + a manual script. All migration-0026/0027 section metadata is NULL in prod. Note: the coarse path DOES honor Build-My-30 sort order — the "first 5 AI" slots were AI slots, mislabeled by RC1 and junk-filled by RC3. The niche path currently *ignores* Build-My-30 drag order (sorts by profile weight). | `agents/pipeline/feed_assembly.py:1121,575`, `agents/pipeline/niche_allocation.py:314,480,429-453` | No honest section labels ("Beyond your bubble", climb levels); mislabeled segment leaks to the reel chip via the coarse fallback in `src/lib/reel/sectionChips.ts:164`. |
| RC6 | **Chip/headline layout bug.** `.seg-chip` is `inline-flex` and the headline `<button>` defaults to inline-block → they render on ONE line ("Wildcard Language Magazine"). | `src/styles/blip-flow.css:2018`, `src/components/blip/reel/ReelStage.tsx:309-319` | Broken-looking reel header. |
| RC7 | **Source slots are dead weight.** X ingestion is dead (xAI Live Search 410s), YouTube produced zero reels this run; unfilled source budgets soft-roll into other categories, silently distorting the user's allocation. | `agents/pipeline/feed_assembly.py:601,730` (soft-roll) | 4 of 30 slots wasted; allocation contract broken. |

## 3. Founder decisions (locked, 2026-07-18)

1. **Remove the entire source-follow system — all four types: YouTube channels, X accounts, podcasts, and personalities.** From onboarding, Build-My-30, the Sources tab, the pipeline, crons, and (last) the DB. blip is news reels only.
2. **Redirect that energy into deeper news personalization**: richer interest elicitation during onboarding/Build-My-30 (dig deeper into what the user actually cares about) and the honest niche-section feed.
3. **Quality before removal**: ship the correctness + notability fixes first and regenerate the founder's feed to prove the jump, then execute removal.

## 4. Goals

- G1. Every reel's displayed category tag matches the story's actual subject.
- G2. Nothing reaches a daily 30 unless it is plausibly *news*: multi-outlet or authority-outlet corroborated. No single-outlet PR-wire/local-notice junk.
- G3. A story lands in a user's interest slot only when it is genuinely about that interest — no generic-keyword false positives.
- G4. Headlines are informative sentences, never a masthead or 2-word fragment.
- G5. Build-My-30 is honored visibly: slot counts AND order, with honest section labels when a niche has nothing new (fallback ladder live in prod).
- G6. YouTube/X/podcast/personality surfaces are fully gone; the 30-slot allocation math is rebalanced; no dead crons, routes, or phantom slots remain.
- G7. Onboarding + Build-My-30 interview goes deeper on news interests (sub-niches, entities, mute-terms) to replace the personalization value sources were meant to provide.

## 5. Non-goals

- No new content modalities (video, podcasts-as-audio, etc.).
- No rewrite of ingestion (GDELT DOC + BigQuery stays the backbone).
- No redesign of the reel UI beyond the chip/headline layout fix and section-label surfacing.
- Entity follows and mute terms stay (they are interest-layer, not source-layer).

## 6. Scope — five workstreams (recommended sequencing)

### WS-A: Correctness hotfixes (first, small diffs)
- Widen `_VALID_SEGMENT_SLUGS` to the 8-root taxonomy; remove the `"wildcard"` default (fallback should derive from the story's best interest root, never a junk bucket). Align with `agents/pipeline/categories.py` and TS `SegmentKey`.
- Headline sanity gate at persist: reject/repair `title == outlet name`, minimum word count; choose cluster representative by best title/outlet quality, not earliest-published; make editorial-rewrite failure fail-closed for garbage titles (retry or substitute another member's title).
- Fix the chip/headline same-line CSS.
- **Validation:** regenerate ash's feed; audit 30/30 rows for label-match.

### WS-B: Notability gate
- Wire `ingest_trusted_outlets` / authority-domain filtering into the live path (`ingest_active_interests` currently passes `domains=None`).
- Raise the produce importance floor above the single-outlet score (require ≥2 distinct outlets, or authority-tier ≥ threshold as a hard cut).
- Fix ranking so affinity alone cannot clear the score threshold; fix within-category importance normalization (a lone junk story in a thin niche currently gets neutral 0.5, not a suppressive score).

### WS-C: Matching precision
- Kill single-generic-term admission: phrase-level anchors, title-match requirement for short/ambiguous terms, per-interest term hygiene (e.g. "foundation", "federal", "india", "trust" can never stand alone).
- Add an embedding-relevance check between story and matched interest (gemini-embedding-001 already wired for clustering) before a story may fill that interest's slot.
- Fix SP1 `story_interests` depth/priority noise (cricket.india must not beat geopolitics on an Indonesia-port story).

### WS-D: Source-system removal (staged; full inventory already mapped)
Stage order (safe by construction):
1. **Stop ingestion**: `RUN_SOURCES`/`RUN_X_THEMES` off; disable/delete the Trigger.dev `SOURCE_INGESTION_CRON` (2-hourly POST) *before* removing the worker route. Zero feed risk — unfilled source budgets already soft-roll.
2. **UI + allocation prune (one change)**: remove youtube/x/podcast/personality buckets from `src/lib/feedBuckets.ts` (incl. `DEFAULT_ALLOCATION` youtube:2/x:2 and the podcast→youtube, personality→x mappings at :360-363) AND `agents/pipeline/niche_allocation.py` source rows **together**, rebalancing to 30 topic slots — otherwise every new feed reserves phantom slots. Remove onboarding source-pick stages, SourcesScreen/SourcesAddControls source-type UX, source search, X-theme chips.
3. **Pipeline removal**: delete YT/X adapters, x_resolver, xai_client, tweet_screenshot, x_theme_* modules, cluster_sweep, source_pipeline, produce_source_reels, scheduler cadences, worker `/ingestion/sources` route, plus their tests. `feed_slot_kind='source'` machinery may stay inert (shared assembler).
4. **DB last, via NEW forward migrations** (never edit applied ones): delete all content_sources rows (all 4 types), drop X-theme tables (x_cluster_sweeps, user_source_clusters, source_clusters, daily_feeds x-theme columns), drop the youtube/x taxonomy roots. Leave the `content_source_type` enum type in place (Postgres enum-value drops are not worth it); block writes at the app layer.
5. Config/env cleanup: settings.py + .env.example YOUTUBE_*/XAI_* keys, Railway worker env.
- **Keep-shared (do not delete):** daily_feeds + assembler, GDELT adapters, taxonomy topic roots, entity follows, mute terms.
- README + `reference/` sync after removal.

### WS-E: Deeper personalization (the replacement bet)
- Wire the niche allocator into Build-My-30 save / batch (`build_niche_allocation` currently orphaned) — **after** fixing the niche path to respect Build-My-30 drag order (today it re-sorts by profile weight, which would break the user's explicit ordering).
- With it, the honest fallback ladder (leaf → 1-level climb → strict → "Beyond your bubble") and section labels go live in prod for real users.
- Deepen the interest interview (onboarding + RebuildFeedFlow): sub-niche drill-downs, entity-level follows, explicit "never show me" capture — replacing the removed Sources surface as the personalization control.

## 7. Acceptance criteria (product-level)

1. Regenerated founder briefing: ≥28/30 reels whose tag matches story subject; 0 masthead/fragment headlines; 0 single-outlet junk stories; slot counts and ordering match Build-My-30 exactly; honest section labels render where a niche is empty.
2. Prod story table after one batch: ai/business/environment/politics/arts counts all non-zero on a normal news day; `wildcard` and `markets` segments never emitted for new stories.
3. Grep-clean removal: no youtube/x/podcast/personality references in src/ or agents/ outside migrations history; Trigger.dev has no source cron; worker has no /ingestion/sources route; new-user onboarding never mentions sources; feed allocation sums to 30 with zero source slots.
4. No regression: existing users' feeds still assemble 30/30; users with legacy source follows see no errors.

## 8. Constraints & gotchas for the PRD

- **daily_feeds fills only from stories produced in the SAME run** (one-pass constraint) — validation requires a fresh batch, not re-assembly of stale rows.
- Migration discipline: schema changes only via new forward migrations applied after code stops reading the dropped objects; apply via IPv4 session pooler.
- Coarse vs niche assembly is selected purely by allocation-row shape (`allocation_interest_id` non-null ⇒ niche) — there is no env flag; WS-E flips users by writing niche rows, which makes the drag-order fix a hard prerequisite.
- Biome/Ruff must pass; Py/TS twins (categories map, SegmentKey, feed types) must be changed in lockstep — this drift is the class of bug behind RC1.
- Frontend `SegmentKey` is already 8-root; most RC1 work is Python-side + a data backfill decision for existing mis-slugged stories (recommend: backfill recent stories' segment slugs in the same slice, older history can stay).
- Evidence pack for reference docs: the 07-07 30-row audit, allocation table, and per-category production counts are in the 2026-07-18 investigation (memory: `news20-0707-feed-rca-2026-07-18`).

## 9. Open questions for /cto

1. Backfill depth for mis-slugged historical stories (all vs last N days vs none)?
2. Notability hard-cut calibration: min distinct outlets (2?) vs authority-tier threshold vs both — and does a thin-but-legit niche (e.g. AI-interpretability) get a relaxed corroboration rule so G2 doesn't starve G5?
3. Does WS-E's deeper interview ship as part of this effort or as the next PRD after removal lands?
