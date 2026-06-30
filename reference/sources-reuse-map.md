# TLDW → News20 Sources-Axis Reuse Map

**Why this doc exists:** Companion to `reference/reuse-map.md`, scoped to the **sources/personalization axis** (M5/M6 — the two interest-picker + personalization specs). TL;DW is a near-complete donor here: it already ships the YouTube/podcast/personality source model, an archetype-keyed recommendation engine, ingestion, and the source UI. `/plan-phases` and `/run-phase` MUST consult this before writing any source/recommendation/ingestion code for M5/M6.

**Evaluated:** 2026-06-04 (two sub-agent passes over the donor). **Verify file:line before lifting** — the donor may have moved since.

**When to update:** mark ✅ ported as modules land; if a NEW item turns out to have a donor analog, move it up; if the donor moves, fix the paths.

> **⚠ Feed-source revamp (2026-06-30, `plans/prd.md`).** The source axis changed in two load-bearing ways:
> - **M1 — net-new cluster layer.** Two **net-new** tables (`source_clusters` + `source_cluster_members`, migration **`0022_source_clusters.sql`**) group catalog rows into named, category-keyed bulk-select **clusters** ("Leading AI-lab researchers", "AI founders"). A cluster is **NOT** an archetype and **NOT** a `persona` (those stay a recommendation-matching key, §2). The **no-dup rule** lives in the cluster/no-dup resolver: a followable **personality** is shown once as a personality card; their individual `content_sources` YouTube/X rows are excluded from the grid and from any cluster's rendered members. Full spec: `reference/source-catalog-taxonomy.md` §Clusters.
> - **M6a — cluster onboarding supersedes the 5c SourceSwipe deck.** The category-keyed **cluster onboarding** (after the top-level category picker, filtered by `topic_tags ∩ chosen categories`, ordered by `popularity_score`, **recommended clusters pre-selected for opt-out**) is now the source-selection step — it **supersedes** the §2/§5 phase-5c **archetype/persona-keyed SourceSwipe deck** as the onboarding source picker. The §2 archetype recommendation engine and the §5 picker UI remain documented as history / reusable structure, but the live source step is cluster-driven. See `plans/prd.md` M6 / Decision #6/#7.

## Donor location

```
~/TLDW-Phase2/tldw/voice-agent-dashboard/
```
TL;DW ("Too Long; Didn't Watch") = a daily two-host AI podcast generator that ingests a user's followed YouTube channels / podcasts / personalities, transcribes + summarizes them, and produces a briefing. Do **not** edit files under TL;DW `_legacy/`.

## Legend

**PORT** = copy with minimal edits · **ADAPT** = copy then meaningfully change · **PATTERN** = copy the shape, rewrite the body · **NEW** = no donor analog, build fresh.

> ⚠ **Re-skin rule:** lift TL;DW UI for *structure/logic only*. Re-style to News20's design language (`reference/design-language.md` / `reference/ui-design-brief.md`) — do **NOT** carry TL;DW's "editorial-dark amber" palette (`--alex:#ff8a3d`) over News20's cream/Playfair system. TL;DW itself has a palette conflict (older components hardcode `#1877F2`/`#f0f0f0`; newer use CSS-var tokens) — standardize on News20 tokens when porting.

---

## 1. Data model & schema (migrations)

> ⚠ **Rename on port (naming collision):** News20 already has `outlets` (news outlets) and `story_sources` (per-story article attribution), so the donor's `sources`/`user_sources`/`content_items` become News20's **`content_sources`/`user_content_sources`/`content_source_items`** (see `plans/phase-5b-source-data-model-catalog.md`). `personalities`/`user_personalities` carry over unchanged. New migrations: `0008` (sources+personalities+archetypes), `0009` (feed prefs); the picker adds `0007` (entity registry).

| TLDW path (file:line) | News20 use | Decision |
|---|---|---|
| `supabase/migrations/001_voice_agent_schema.sql:166-185` (`sources`) | Canonical source table: `source_type`, `external_id`, `source_name`, `thumbnail_url`, `subscriber_count`, `platform_metadata jsonb`, `last_fetched_at`; unique `(source_type, external_id)` | **PORT** |
| `001_voice_agent_schema.sql:198-208` (`user_sources`) | Follows junction: PK `(user_id, source_id)`, `is_active`, `priority int`, `added_via` | **PORT** (extend `priority`→3-state for the control surface, D2) |
| `001_voice_agent_schema.sql:234-257` (`content_items`) | Per-item store: `raw_content` (transcript), `summary`, `key_points`, `published_at`, `processing_status`; global (no user_id) | **PORT** |
| `006_personalities.sql:23-58` (`personalities`, `user_personalities`) | Named-creator catalog + follow junction: `display_name`, `aliases text[]`, `bio`, `photo_url`, `youtube_channel_ids text[]` | **PORT** |
| `006_personalities.sql:72-144` (`personality_appearances` + `user_personality_spotlights` RPC) | Link personality↔content_item (host/guest/mention) + last-24h spotlight aggregation | **ADAPT** (optional; for "personality spotlight" digest segments) |
| `010_recommendation_seeds.sql:23-36` | LLM-enumerated catalog tagged with `topic_tags` + `popularity_score`; GIN index | **PORT** (re-tag taxonomy to the 8 categories) |
| `014_catalog_personas.sql:34-76` (`personas text[]` cols + `personality_sources`) | Per-archetype catalog columns + personality↔source editorial links | **PORT** (re-author personas → News20 archetypes) |
| `007_prune_source_types.sql:18,36,44` | History: `twitter_account` source_type was added then **deleted**. | **NEW** — re-add an `x_account` source_type for News20 |
| `agents/ingestion/models.py:436-484` (`TwitterContentMetadata`) | Tweet metadata shape (`tweet_id`, `author_username`, thread IDs) | **PATTERN** — shape survives; the adapter does not (build fresh) |

---

## 2. Recommendation engine (the archetype → sources matcher)

| TLDW path (file:line) | News20 use | Decision |
|---|---|---|
| `scripts/seed_catalog/` (`data/{type}.{persona}.json` → resolve → upsert) | Per-archetype curated catalog seeder; file position = popularity rank; persona-union across files | **ADAPT** (re-author the 6 personas → News20's 10–15 archetypes, `reference/archetypes.md`) |
| `scripts/seed_catalog/youtube_resolve.py`, `itunes_resolve.py` | Resolve a channel handle → channel id + thumbnail; resolve a podcast → iTunes id + feed url | **PORT** |
| `agents/shared/taxonomy.py:15-62` (41-tag taxonomy) | Topic-tag vocabulary for tagging sources + users | **ADAPT** (map/align to the 8 categories — C1) |
| `src/app/api/onboarding/analyze/route.ts:259-579` | Tag user (from YT subs) → aggregate clusters → **Jaccard-rank** the catalog (`computeMatchScore` `:450-465`, `rankRecommendations` `:486-579`) | **PORT** (this is the interest-vector→sources matcher) |
| `src/app/api/sources/recommended/route.ts:102-189` | Persona-filtered catalog browse with **round-robin merge** across multiple personas (balanced grid) | **PORT** |
| `scripts/seed_recommendations.py:112-171` | LLM enumerates ~200 podcasts + ~200 channels, tagged | **PATTERN** (M6 research agent replaces the "just ask an LLM" step with community-signal crawl) |

---

## 3. Ingestion (followed sources → digest pool)

| TLDW path (file:line) | News20 use | Decision |
|---|---|---|
| `agents/ingestion/adapters/youtube.py:62-477` | Fresh-upload detection (`"UU"+channel_id[2:]` uploads playlist; `playlistItems.list` = 1 quota unit; `videos.list` batch enrich) + **captions-only** transcription (`youtube-transcript-api`, no Whisper) + traction score | **PORT** |
| `agents/ingestion/podcast_audio.py:289-411` | RSS episode → stream-download → ffmpeg/pydub → ≤24MB chunks → **OpenAI Whisper** → concat; cost estimate ($0.006/min) | **PORT** |
| `agents/ingestion/adapters/podcast.py:127-200` | RSS via `feedparser` (`feed_utils` for macOS SSL) + duration cap + **daily transcription budget** | **PORT** |
| `agents/ingestion/adapters/personality.py:570-697` | Personality "hunt": `search.list` per personality + alias-regex filter + own-channel exclusion → `personality_appearances` | **ADAPT** (note: its X leg is absent — YouTube + optional Podcast Index only) |
| `agents/ingestion/adapters/{base,feed_utils}.py`, `__init__.py` (`get_adapter()`) | Adapter base + dispatch | **PORT** |
| `agents/ingestion/scheduler.py:32-36` (`CadenceScheduler`) | Per-source-type poll cadence (YouTube 6h / podcast 12h / personality 6h) | **PORT** |
| `agents/ingestion/pipeline.py:74+` (`run_global_ingestion`) | Per-user flow: fetch sources → cadence filter → adapter fetch → dedup → upsert `content_items` → process | **ADAPT** (drop Pinecone embed step per [[news20-qa-incontext-grounding]]; wire output into News20's deduped story pool, C4) |
| `src/app/api/sources/search/route.ts:102-280` | YouTube 2-step search (`search.list`→`channels.list`) + iTunes podcast search | **PORT** (the X-handle search path is **NEW**) |
| **X/Twitter adapter** | Poll followed handles for posts | **NEW** — no donor; reuse `TwitterContentMetadata` shape only |

---

## 4. Scheduling (Trigger.dev v4 crons)

| TLDW path (file:line) | News20 use | Decision |
|---|---|---|
| `trigger/ingestion-cron.ts:51-119` | `cron: "0 */2 * * *"` (2h) → list active users → fan out per-user `contentIngestionTask.triggerAndWait` | **PORT** |
| `trigger/refresh-recommendation-seeds.ts:279-284` | `cron: "0 3 1 1,4,7,10 *"` (quarterly) → regenerate catalog seeds | **PATTERN** (M6 research agent; tune cadence) |

---

## 5. UI components (re-skin to News20 tokens)

| TLDW path (file:line) | News20 use | Decision |
|---|---|---|
| `src/components/shared/source-artwork.tsx:74-139` + `src/lib/portrait-bg.ts` | **Universal avatar** — `<img>` w/ broken-image→initials-gradient fallback; `kind` drives circle(person)/square(channel,podcast); `referrerPolicy="no-referrer"` | **PORT** (lift first; covers all axes) |
| `src/types/source.ts:100-119` (`SOURCE_TYPE_CONFIGS`) | Per-axis display config (label, icon, search placeholder, pill) | **PORT** (add a 4th `x_account` type) |
| `src/components/onboarding/picker-screen.tsx` | Image-grid picker: square/circle tiles, debounced live search w/ stale-guard, **"+ Add custom" tile** | **PORT** (pick this OR `SourceCard` — Rule 7; recommend this) |
| `src/components/onboarding/source-card.tsx` | Alt list-style selectable card (avatar+name+desc+check+badge, `aria-pressed`) | **PATTERN** (alternative to PickerScreen) |
| `src/components/sources/source-search-modal.tsx` | Search-and-add modal: 300ms debounce → Add/Adding/Added states + `is_already_added` badge + skeletons | **PORT** |
| `src/components/personalities/personality-grid.tsx:89-209` | **Optimistic Follow/Following** (variant swap) + **toast rollback on failure** | **PORT** (gold-standard toggle) |
| `src/components/sources/{sources-client,source-section,source-item-row}.tsx` | Settings "manage sources" surface: grouped sections + active/paused `Switch` + hover-to-remove (two-tap confirm) | **ADAPT** (extend `Switch`→3-state priority for D2) |
| `src/components/shared/{chip,source-glyph,platform-logos}.tsx` | Source-type pill + hand-drawn glyphs + inline YouTube/Apple-Podcasts SVG logos | **PATTERN** (re-style) |
| `src/components/onboarding/{step-youtube-creators,step-podcasts,step-personalities}.tsx` | Per-axis step screens with `FALLBACK_*` lists when recs are empty (resilience pattern) | **PATTERN** |
| `src/lib/stores/onboarding-store.ts` | Zustand wizard state (`SourceSelection`, `toggleSource`, per-axis arrays) | **PORT** |
| `src/app/(app)/onboarding/page.tsx` | Wires 3 sequential `PickerScreen`s (channels/podcasts/people) fed by `/api/sources/recommended` | **PATTERN** |

---

## 6. Build-fresh checklist (no donor analog)

- [ ] **X/Twitter adapter** + handle-resolver search backend (`agents/ingestion/adapters/`, `api/sources/search`). Reuse `TwitterContentMetadata` shape only.
- [ ] **Community-signal research agent** (M6 / Phase 6A) — crawl Reddit/X/podcast directories → candidate sources per niche → write to `recommendation_seeds`/`personalities`. Reuses the §2 matcher.
- [ ] **Control surface** (M5 / Phase 5E) — master dial, 30-cell allocation ribbon, presets, **pinned-sources-fill-first** allocation, live preview. See `reference/control-surface-spec.md`.
- [ ] **News20 archetype set** (10–15) + 8-category vectors. See `reference/archetypes.md`.
- [ ] **Recursive interest-picker React port** + entity registry (`/api/entities/list`+`/search`). See `onboarding_interest_picker_spec.md`.

## 7. Cross-cutting decisions carried from the plan

- **C1** Taxonomy pinned to the **8 categories** (AI · Geopolitics · Business · Environment · Politics · Tech · Sport · Arts).
- **C2** Gesture usage is **NOT** a learning signal (swipe = navigation).
- **C3** Follow-as-filter (topic/entity → ranking) and follow-as-source (`user_sources`/`user_personalities` → ingestion) are **separate** in the schema.
