# M5–M6 — Two-Axis Personalization: Sources, Archetypes & the Control Surface

**Date:** 2026-06-04
**Status:** Proposed (feature list + implementation plan). Not yet `/plan-phases`-expanded.
**Source specs:**
- `personalization-and-source-curation-spec.md` (umbrella: topics ⨉ sources, control surface)
- `onboarding_interest_picker_spec.md` + `interest_picker.html` (topics axis, recursive picker)
**Donor codebase:** TL;DW at `/Users/asheshsrivastava/TLDW-Phase2/tldw/voice-agent-dashboard/` (evaluated 2026-06-04; file:line citations below).

---

## 0. The one finding that shapes everything

TL;DW is not "slightly similar" — it is a **near-complete donor for the entire sources half** of these specs. It already ships, in production:

- a three-axis source model (**YouTube channels / podcasts / personalities**) with a clean follows junction (`sources` / `user_sources` / `personalities` / `user_personalities`);
- a **persona/archetype-keyed curated catalog** + an **LLM-tagged recommendation matcher** (Jaccard overlap of interest tags → ranked sources) — exactly the spec's "map user to archetype → instant recommendations";
- **ingestion**: YouTube fresh-upload detection (1-quota-unit trick) + caption transcription; podcast RSS → Whisper with a daily cost budget;
- the **UI components**: avatar primitive with broken-image→initials fallback, selectable source cards, an image-grid picker with debounced live search + "+ Add custom", a search-and-add modal, optimistic Follow/Following with toast rollback, and a settings "manage sources" surface;
- **Trigger.dev v4 crons** for upload polling and quarterly catalog refresh.

**Build-fresh (does NOT exist in TL;DW):**
1. **X/Twitter monitoring** — only a `TwitterContentMetadata` Pydantic shape survives (`agents/ingestion/models.py:436`); the adapter + source_type were deleted (`007_prune_source_types.sql`).
2. **Community-signal research agent** — TL;DW's recommendations come from a hand-curated catalog + LLM enumeration, *not* from crawling Reddit/X/podcast directories. The spec's "research agent that mines who each niche actually recommends" is net-new (but can write straight into TL;DW's `recommendation_seeds`/`personalities` schema and reuse the existing matcher).
3. **The control surface** — master dial, per-source priority, the 30-cell allocation ribbon with live preview, presets, and the pinned-sources-fill-first rule. TL;DW has no equivalent allocation UI.

Implication: **most of M5 is a port + re-skin, not a green-field build.** Lift TL;DW's structure/logic; re-skin to News20's design language (`reference/design-language.md`) — do **not** carry TL;DW's "editorial-dark amber" palette over the News20 cream/Playfair system.

---

## 1. Conflicts to resolve before building (decided here)

The three specs don't fully agree. Resolving up front so phases don't blend contradictions (Rule 7).

| # | Conflict | Decision |
|---|---|---|
| C1 | **Taxonomy mismatch.** Interest picker locks **8** categories (AI · Geopolitics · Business · Environment · Politics · Tech · Sport · Arts, Health removed). The personalization spec §2.1 illustrates them as "Tech, Markets, Sports, Geopolitics, Entertainment…". | The interest picker is canonical (richer, owner-approved, already has the full seed dataset). **Pin all archetype vectors, the topic ribbon's color map, and source taxonomy tags to the 8 categories.** Re-write §2.1's example list when amending the master plan. |
| C2 | **Gestures-as-signal contradiction.** Both specs lock "swipe = navigation, **not** preference" (picker §1; pers. spec §8). But pers. spec §6 lists **"gesture usage"** among signals to "learn the best ordering." | **Drop gesture usage as a learning signal.** Ordering learns only from watch-completion, questions-asked, and explicit follow/unfollow. Amend §6 of the spec. This keeps the cross-cutting rule intact. |
| C3 | **Entity-as-topic vs entity-as-source overlap.** The picker already follows *people* (e.g. `Patrick Mahomes kind:person`) as topic filters; the source axis also follows *people* (personalities) as ingestable feeds. | **The data model must separate them** — and TL;DW already does: topic/entity follows → the `follows`/ranking tables (filter signal); ingestable sources → `user_sources` / `user_personalities` (feed signal). A person can exist in both with no collision. Carry this separation forward verbatim. |

---

## 2. Changes required to the master plan (`plans/master-plan.md`)

These specs push **past current v1 scope**. The master plan today explicitly defers all of this. Proposed delta (apply on confirmation — not yet applied):

1. **Out of scope → In scope (new milestone).** Line 126 — "YouTube channel + podcast ingestion (explicit later phase per brief; news-only for v1)" — move to **M5**. Add **X/Twitter ingestion** as in-scope too.
2. **Loosen Decision #8 (personalization).** Today: "category prioritization from engagement signals, not ML." Extend to the **two-axis model** (topics *what* ⨉ sources *who*), **archetype profiles**, and the **control surface** (master dial + per-source priority + allocation ribbon + pinned-sources-fill-first). Still heuristic, still no ML — but no longer "just category prioritization."
3. **New Decision #11 — "Sources are a first-class second axis."** Record the topics/sources split, the pinned-sources-fill-first conflict rule, and the C3 data-model separation (follow-as-filter vs follow-as-source).
4. **New Decision #12 — "Inherit TL;DW's source stack wholesale"** (mirrors Decision #2 for the news stack). Lists the donor tables/adapters/components and the two build-fresh gaps (X monitoring, research agent).
5. **Onboarding note.** The 3-level chip onboarding shipped in `phase-1e` is **superseded by the recursive interest picker** (arbitrary depth, follow-sets, entity registry). Note whether this is an in-place upgrade of `phase-1e`'s onboarding or an M5 replacement (recommendation: M5 replacement; keep `phase-1e` auth/profile schema, swap the picker UI + add the registry).
6. **Architecture diagram.** Add: source ingestion adapters (YouTube/podcast/X) → the existing 30-story pipeline; the recommendation engine (archetype map + Jaccard matcher); the allocation layer (pinned-first) feeding `daily_feeds`.
7. **Two new milestones** appended after M4 (see §4). Add to the Milestones + Phases sections.
8. **New reference docs** to add under `reference/`: `sources-reuse-map.md` (the TL;DW file:line port map, §5 below), `archetypes.md` (the 10–15 profiles + 8-category vectors), `control-surface-spec.md` (allocation math for pinned-first + live preview).
9. **Resolve C1/C2** in the master-plan body where the taxonomy and the gesture rule are stated.

---

## 3. Feature list

Grouped by axis. ✅ = substantially reusable from TL;DW; 🟡 = partial reuse; 🆕 = build fresh.

### A. Topics axis — recursive interest picker (upgrade of `phase-1e` onboarding)
- **A1** Recursive follow-set engine: arbitrary depth, Category→Sub→items, nested reveal. 🆕 (port `interest_picker.html`)
- **A2** Topic vs Entity model + entity registry (`/api/entities/list`, `/api/entities/search`). 🟡 (registry tables/seed are new; TL;DW's `sources` search routes are a pattern)
- **A3** Per-set **Select all** / **Show more** (registry pagination) / **Add your own** (registry search + freetext). 🆕
- **A4** Nested reveal: lazy mount, preserve child selections across collapse. 🆕
- **A5** Selection tray: total + per-category counts + review panel + export. 🆕 (exists in prototype)
- **A6** Follows payload (§7) → ranking/personalization (`followId`, `path[]`, `type`, `kind`, `ticker`, `source`). 🟡 (wire to existing `ranking-spec.md`)
- **A7** Dedupe entities reachable via multiple paths (one entity id, multiple `path`s). 🆕
- **A8** Editorial design-system port (cream/green/rust tokens, Fraunces/Spline). 🆕

### B. Sources axis — recommendation & onboarding (mostly TL;DW)
- **B1** Source data model: `sources` / `user_sources` / `personalities` / `user_personalities` / `content_items`. ✅ (`001_voice_agent_schema.sql:166-208`, `006_personalities.sql`)
- **B2** Archetype profiles (10–15 named) + map user interest-vector → nearest archetype. 🟡 (TL;DW has 6 personas + persona-keyed catalog; News20 needs its own 8-category archetype set)
- **B3** Source-recommendation screens in order: YouTube channels → X handles → personalities (+ podcasts). ✅ structure (`onboarding/page.tsx`, `PickerScreen`)
- **B4** Per-entity UI: avatar/thumbnail + display name + follow toggle + **search-and-add**. ✅ (`SourceArtwork`, `SourceCard`, `SourceSearchModal`)
- **B5** Recommendation engine: tag user → aggregate clusters → Jaccard-rank curated catalog; persona round-robin merge. ✅ (`api/onboarding/analyze/route.ts`, `api/sources/recommended/route.ts`, `taxonomy.py`)
- **B6** Background **research agent**: crawl Reddit/X/podcast directories for "who this niche recommends," write to `recommendation_seeds`/`personalities`. 🆕
- **B7** Periodic catalog refresh (cron). ✅ pattern (`refresh-recommendation-seeds.ts`)

### C. Ingestion — followed sources into the 30-story pipeline
- **C1** YouTube fresh-upload detection + caption transcription (1-unit `playlistItems.list`, `"UU"+id` trick, captions-only). ✅ (`adapters/youtube.py`)
- **C2** Podcast RSS → download → chunk → Whisper, duration cap + daily cost budget. ✅ (`podcast_audio.py`, `adapters/podcast.py`, `itunes_resolve.py`)
- **C3** **X/Twitter monitoring** (poll followed handles for posts). 🆕 (only `TwitterContentMetadata` schema reusable, `models.py:436`)
- **C4** Route source-derived content into the existing per-story digest pipeline (script→TTS→reel). 🟡 (the pipeline exists; wire source `content_items` as an ingestion source feeding the deduped story pool)
- **C5** Per-source cadence scheduler + ingestion cron fan-out. ✅ (`scheduler.py`, `trigger/ingestion-cron.ts`)

### D. Control surface — the settings/allocation screen (all fresh)
- **D1** Master dial: **My Sources ←→ Discovery** (how many of 30 slots from followed sources vs topics). 🆕
- **D2** Followed-sources list + per-source priority: `Off · Only their big stuff · Everything they post`. 🟡 (TL;DW `SourceItemRow` has active/paused `Switch`; extend to 3-state + the "big stuff" threshold)
- **D3** 30-cell color-coded topic-allocation ribbon (drag boundaries). 🆕 (note: spec calls this "existing design" — locate that artifact)
- **D4** Presets: Power Feed / Balanced / Wide Lens. 🆕
- **D5** Conflict rule: **pinned sources fill first; topics fill the rest** (no double-counting). 🆕
- **D6** Live preview: ribbon re-renders on every drag — source cells show avatar, topic cells show color. 🆕

### E. Ordering & learning (phase 2 of the spec → M6)
- **E1** v1 manual order. 🆕
- **E2** Engagement-learned ordering from watch-completion + questions + follow/unfollow (**not** gestures — see C2). 🟡 (reuse the M1 signal→weight loop pattern from `ranking-spec.md`)

### F. Cross-cutting (decisions, not screens)
- **F1** Pin taxonomy to the 8 categories (C1).
- **F2** Remove gesture usage as a learning signal (C2).
- **F3** Enforce follow-as-filter vs follow-as-source separation in the schema (C3).

---

## 4. Implementation plan (milestones → phases)

Sequenced by dependency. **M5 = ship the two axes + control surface; M6 = the research agent + learned ordering.** Each phase below is milestone-level; run `/plan-phases` to expand each into the standard 4 sub-phases when ready.

> Placement: M5/M6 come **after M4 (App Store ship)** in the master plan's milestone list. The recursive picker (Phase 5A) is the one piece that *could* be pulled earlier (it upgrades M1 onboarding) if you'd rather ship the better picker before the sources work.

### M5 — Sources, archetypes & control surface

**Phase 5A — Recursive interest picker (topics axis).**
Goal: replace `phase-1e`'s 3-level chip onboarding with the recursive follow-set engine from `interest_picker.html`, port the §7 payload to ranking, add the entity registry.
- Lift: the seed dataset + recursive render logic from `interest_picker.html` (re-author as React per spec §9: `OnboardingPicker`/`Category`/`Subcategory`/`FollowSet`/`Chip`/`SelectionTray`).
- Build: entity registry tables + `/api/entities/list` & `/search` (pattern from TL;DW `api/sources/search/route.ts`); lazy nested mount; dedupe (A7).
- Reuse from existing: wire follows to `reference/ranking-spec.md`; keep `phase-1e` auth + profile schema.
- DoD: all 8 categories render; the four §4 marquee nested cases work; payload POSTs the §7 shape; skippable → breaking-news-only feed; ≥44px targets + `aria-pressed`.

**Phase 5B — Source data model + curated catalog seed.**
Goal: stand up the sources backbone and a News20 archetype catalog.
- Lift wholesale: `sources` / `user_sources` / `content_items` (`001_voice_agent_schema.sql:166-257`) + `006_personalities.sql` + `010_recommendation_seeds.sql` + `014_catalog_personas.sql`. Add an `x_account` source_type + a `TwitterContentMetadata`-shaped metadata column now (cheap, avoids a later migration).
- Build: the News20 **archetype set** (10–15, vectors over the 8 categories — `reference/archetypes.md`) and the per-archetype seed JSON (`scripts/seed_catalog/` pattern: `{type}.{persona}.json` → resolve via Wikipedia/YouTube/iTunes → upsert). Re-map TL;DW's 6 personas to News20 archetypes.
- DoD: a seeded catalog of channels/podcasts/personalities tagged to archetypes + the 41→8-aligned taxonomy; `(source_type, external_id)` upsert dedup verified.

**Phase 5C — Source onboarding + recommendation screens.**
Goal: after the picker, map user→archetype and walk the 3 (+podcast) recommendation screens.
- Lift: `PickerScreen` (image-grid, debounced live search, "+ Add custom") **or** `SourceCard` list (pick one — Rule 7; recommend `PickerScreen`); `SourceArtwork` + `portrait-bg.ts` (universal avatar); `SourceSearchModal` (search-and-add); `PersonalityGrid`'s optimistic Follow/Following + toast rollback; `/api/sources/recommended` round-robin merge; the `analyze/route.ts` Jaccard matcher.
- Build: archetype mapping (interest-vector similarity); re-skin all lifted components to News20 tokens; the X-handle resolver search backend (the YouTube/iTunes resolvers are reusable; X is not).
- DoD: a new user with an interest profile sees archetype-matched channels/podcasts/personalities, each with avatar+name+follow toggle+search-add; "+ Add custom" resolves or stores freetext.

**Phase 5D — Ingestion of followed sources.**
Goal: followed YouTube/podcast/X sources flow into the 30-story pipeline.
- Lift: `adapters/youtube.py` (upload detection + caption transcription), `podcast_audio.py` + `adapters/podcast.py` (RSS→Whisper + cost budget), `agents/ingestion/scheduler.py` (`CadenceScheduler`), `trigger/ingestion-cron.ts` (2h fan-out).
- Build fresh: the **X/Twitter adapter** (poll followed handles; reuse `TwitterContentMetadata` shape); wiring of source `content_items` into News20's deduped story pool / digest pipeline (C4).
- DoD: a followed channel's new upload becomes a digest-eligible story within one cron cycle; podcast cost budget enforced; X posts ingested.

**Phase 5E — Control surface (settings/allocation).**
Goal: the screen where the user balances the two axes across 30 slots.
- Build (all fresh): master dial (D1); 3-state per-source priority (D2, extend TL;DW `SourceItemRow`); the 30-cell ribbon (D3 — first locate the "existing design" artifact the spec references); presets (D4); the **pinned-sources-fill-first allocation function** (D5) feeding `daily_feeds`; the live-preview re-render (D6).
- Decide first (spec §7 open Qs): hard split vs soft bias when sources are sparse; how "Only their big stuff" is thresholded (engagement? duration? topic match?).
- DoD: dragging dial/priority/ribbon re-renders the 30-cell preview instantly; allocation respects pinned-first with no double-counting; presets set sane defaults.

### M6 — Discovery agent & learned ordering

**Phase 6A — Community-signal research agent.**
Goal: keep per-archetype source lists fresh from what communities actually recommend.
- Build fresh: a crawl/LLM agent over Reddit threads, X conversations, podcast directories, forums → candidate sources per niche → write to `recommendation_seeds` / `personalities` (reuses 5B schema + 5C matcher).
- Lift: the cron pattern from `refresh-recommendation-seeds.ts` (quarterly → tune cadence).
- DoD: the agent surfaces newly-rising voices into the catalog without manual curation; refresh runs on schedule.

**Phase 6B — Learned ordering (spec §6).**
Goal: move from manual order to engagement-learned order.
- Build: ordering learned from watch-completion + questions-asked + follow/unfollow (**not** gestures — C2), reusing the M1 bounded/decayed signal→weight loop (`ranking-spec.md`).
- DoD: per-user feed order adapts to where engagement concentrates; order is never hard-coded.

---

## 5. TL;DW reuse map (consolidated — for `/run-phase` lift)

All paths under `/Users/asheshsrivastava/TLDW-Phase2/tldw/voice-agent-dashboard/`.

### Data / backend
| Need | TL;DW source | Status |
|---|---|---|
| Source tables (sources/user_sources/content_items) | `supabase/migrations/001_voice_agent_schema.sql:166-257` | ✅ lift |
| Personalities catalog + follows + appearances | `supabase/migrations/006_personalities.sql` | ✅ lift |
| Recommendation seeds + taxonomy | `010_recommendation_seeds.sql`, `agents/shared/taxonomy.py:15-62` | ✅ lift (re-tag to 8 cats) |
| Persona/archetype catalog columns | `014_catalog_personas.sql:34-76` | ✅ lift |
| Per-archetype catalog seeder | `scripts/seed_catalog/` (`{type}.{persona}.json`) | ✅ lift, re-author personas |
| Recommendation matcher (Jaccard + clusters) | `src/app/api/onboarding/analyze/route.ts:259-579` | ✅ lift |
| Persona round-robin recommend browse | `src/app/api/sources/recommended/route.ts:102-189` | ✅ lift |
| YouTube upload detect + transcription | `agents/ingestion/adapters/youtube.py:62-477` | ✅ lift |
| Podcast RSS + Whisper + cost budget | `agents/ingestion/podcast_audio.py`, `adapters/podcast.py` | ✅ lift |
| Personality hunt adapter | `agents/ingestion/adapters/personality.py` | ✅ lift |
| Cadence scheduler + ingestion cron | `agents/ingestion/scheduler.py:32-36`, `trigger/ingestion-cron.ts:51-119` | ✅ lift |
| Catalog refresh cron | `trigger/refresh-recommendation-seeds.ts:279-284` | ✅ lift pattern |
| Twitter metadata shape (only) | `agents/ingestion/models.py:436-484` | 🟡 shape only — adapter is 🆕 |

### Frontend / UI (re-skin to News20 tokens)
| Need | TL;DW source | Status |
|---|---|---|
| Universal avatar (img→initials fallback) | `src/components/shared/source-artwork.tsx:74-139` + `src/lib/portrait-bg.ts` | ✅ lift first |
| Per-axis display config | `src/types/source.ts:100-119` (`SOURCE_TYPE_CONFIGS`) | ✅ lift |
| Selectable source card | `src/components/onboarding/source-card.tsx` | ✅ lift |
| Image-grid picker + live search + "+Add custom" | `src/components/onboarding/picker-screen.tsx` | ✅ lift (pick this OR SourceCard) |
| Search-and-add modal | `src/components/sources/source-search-modal.tsx` | ✅ lift |
| Optimistic Follow/Following + toast rollback | `src/components/personalities/personality-grid.tsx:89-209` | ✅ lift |
| Settings "manage sources" surface | `src/components/sources/{sources-client,source-section,source-item-row}.tsx` | ✅ lift (extend priority to 3-state) |
| Search backends (YouTube 2-step, iTunes) | `src/app/api/sources/search/route.ts:102-280` | ✅ lift (X path is 🆕) |
| Onboarding wizard state | `src/lib/stores/onboarding-store.ts` | ✅ lift pattern |

### Build fresh (no donor)
- X/Twitter adapter + handle resolver (Phase 5D / 5C).
- Community-signal research agent (Phase 6A).
- Entire control surface: master dial, 30-cell ribbon, presets, pinned-first allocation, live preview (Phase 5E).
- News20 archetype set + 8-category vectors (Phase 5B).
- Recursive interest-picker React port + entity registry (Phase 5A).

---

## 6. Open questions (carry into `/plan-phases` / `/cmo`)
1. **Archetype set:** the exact 10–15 profiles + their 8-category vectors (spec §7). Seed from TL;DW's 6 personas + News20's 8 categories.
2. **Re-rank strength:** how hard sub-niche picks re-weight an archetype's default source lists (spec §7).
3. **"Only their big stuff" threshold:** engagement? duration? topic match? (spec §7, D2).
4. **Master dial semantics:** hard split or soft bias when a source is sparse on a given day (spec §7, D5).
5. **The "existing" 30-cell ribbon design** (spec §5.3) — locate that artifact before building D3.
6. **Picker placement:** ship the recursive picker (5A) inside M1 as an onboarding upgrade, or hold for M5?
7. **Onboarding length:** topics drill-down + 3–4 source screens is long — does it need a skip-to-feed at each step (the picker is already skippable; mirror for sources)?

---

## 7. Next
Two decisions unblock the build:
1. **Apply the master-plan delta in §2?** (I'll edit `plans/master-plan.md` + create the three new `reference/` docs.)
2. **Picker placement** (Q6): M1 upgrade now, or M5?
Then run `/cmo` on this doc to lock the archetype set + the §6 open questions, → `/cto` → `/plan-phases plans/m5-m6-personalization-sources-control-surface.md`.
