# Phase 5f: Source catalog curation (populate `content_sources`)

**Milestone:** M5 — Two-axis personalization (sources + control surface)
**Status:** Not started
**Estimated effort:** XL (4 platform integrations + LLM candidate generation + ~2,400 archetype×axis memberships)
**Run in:** a fresh session (this is a standalone data-population effort).

## Goal
For **every profile (archetype)**, the `content_sources` catalog holds **≥ 50 real, thumbnail-bearing rows on each of the 4 axes** — YouTube channels, podcasts, X accounts, personalities — tagged with that archetype's slug in `personas` and with topic tags, so the onboarding **SourceSwipe** deck shows a full, relevant, recognizable set the moment a user's interest selections resolve to a profile. The data is **pre-stored** (name + thumbnail + follower label + tags + popularity); runtime only reads and recommends (that code already exists and works).

## Why this phase exists (the gap)
The recommendation engine (5c) and the schema (5b/migration 0009) are **built and wired**, but **`content_sources` has zero rows** — it is never seeded by any migration or seed file, and the TL;DW donor ships no reusable catalog. The result is the live bug: after picking topics, `SourceSwipe` finds nothing on all 4 axes and skips straight to **"0 sources followed across YouTube, Podcasts, X & People."**

This was nobody's job by accident: **5c** assumed a populated catalog; **5d** ingests *items from already-followed sources* (`content_source_items`) and explicitly punts "catalog refresh / discovery" to **Phase 6**; **Phase 6 was cut** in the Blip Flow pivot and never replaced. This phase fills that hole. **It is a hard prerequisite for the sources axis to function at all.**

> ⚠ Distinct from Phase 5d. 5d = *content items from followed sources → story pool*. 5f (this) = *the discoverable catalog of sources to recommend*. Different table, different stage.

## How the runtime consumes this data (verified — sub-agents must not re-derive)
- **One table, four axes.** All 4 swipe axes read **`content_sources`**, filtered by the `content_source_type` enum: `youtube_channel | podcast | x_account | personality`. The separate `personalities` table is for a *different* feature (cross-mention spotlights RPC) and is **NOT** read by the deck. Populate `content_sources` for **all four**, including `content_source_type = 'personality'`.
- **Surfacing rule.** `src/lib/sources.ts::listSourcesByArchetype([slug], kind, k)` returns rows WHERE `personas && ARRAY[slug]` AND `content_source_type = kind`, ordered by `popularity_score DESC`. So **a row appears for archetype A on axis K iff `A = ANY(personas)` AND `content_source_type = K`.**
- **One archetype per user.** `sourceSwipeData.ts` calls the recommender with a **single** archetype (`[match.archetype_id]`, the top‑1 cosine match). So each (archetype × axis) cell must independently hold ≥ 50; cross-archetype round-robin is not exercised in v1.
- **Display vs storage.** The deck shows `CARDS_PER_PLATFORM = 12` of those (top‑12 by popularity). Storing 50 satisfies "at least 50"; whether to raise the *shown* count is a one-line UI knob (SP4 open question), not a data requirement.
- **`balanced-generalist` is special.** The matcher's fallback (`FALLBACK_ARCHETYPE_SLUG`) **and** what the current `SKIP_AUTH=true` iOS build *always* resolves to (no session → zero interest vector → flat → balanced-generalist). **It must be the most diverse, fully-populated archetype** — it is what unblocks the simulator immediately.

## The 12 archetypes (profiles) to cover — from `supabase/seed/archetypes.sql`
`ai-frontier-tech` · `markets-macro` · `startup-operator` · `crypto-fintech` · `geopolitics-world` · `us-politics-policy` · `climate-energy` · `sports-fan` · `arts-culture` · `creator-media` · `tech-generalist` · `balanced-generalist`

The set is explicitly **not final** (archetypes.sql: "open question #1 → /cmo locks the final set"; re-seedable without schema change). SP1 must decide the **regional/cultural coverage** question your Bollywood example raises (see Open Question 1) before bulk population.

## `content_sources` columns to populate (migration 0009)
| column | how to fill |
|---|---|
| `content_source_type` | the axis enum (`youtube_channel`/`podcast`/`x_account`/`personality`) |
| `external_id` | per-axis stable id (YT `channelId`; podcast iTunes `collectionId` or feed URL; X lowercased handle; personality slug) — the upsert key half |
| `source_name` | channel/show/account/person display name |
| `source_description` | one-line "why/what" (the card's why-text fallback) |
| `thumbnail_url` | **real** image URL (see per-axis enrichment) — null only when genuinely unavailable, and counted |
| `subscriber_count` | subscribers/listeners/followers (bigint); null allowed where no API |
| `platform_metadata` | jsonb (e.g. YT `{uploads_playlist, country}`, X `{handle}`) |
| `personas` | `text[]` of **archetype slugs** this source serves (multi-tag; this is the surfacing key) |
| `topic_tags` | `text[]`; **`topic_tags[0]` MUST be one of the 8 pinned category keys** (`ai/geopolitics/business/environment/politics/tech/sport/arts`) so the card accent resolves, followed by finer sub-niches |
| `popularity_score` | `numeric` 0–100, drives in-archetype ranking (see Open Question 4) |
| `is_curated` | `true` |

Idempotent upsert key: **`unique (content_source_type, external_id)`** → `on conflict … do update` (mirror `archetypes.sql`, which uses `do update` on purpose for re-seedable tuning data).

## Context the sub-agents need
- **Write path:** `content_sources` is RLS **public-read, no write policy** → only the **service-role key** (bypasses RLS) may seed. Apply via the **IPv4 session pooler** (`aws-1-us-east-1`, `db push`/`psql --db-url`); the direct host is IPv6-only (see memory `news20-supabase-ddl-connection`). Service-role key + DB URL via env, **never hardcoded/logged** (CLAUDE.md, Rule: env-var safety).
- **Anti-hallucination is mandatory.** LLM-proposed names are *candidates only*. Every row must be **resolved against the real platform API**; any candidate that doesn't resolve to a real entity is **dropped** (logged, not silently). Over-generate (~70–80/cell) so ≥ 50 survive resolution.
- **Existing patterns to match:** seed style + idempotency from `supabase/seed/archetypes.sql`; Python agent conventions (Pydantic models, `httpx` async, `structlog`, `pydantic-settings`) from `CLAUDE.md §Agent Stack` and `agents/shared/`. Keep tool files < 500 lines, modular.
- **Where the code lives (proposed):** `agents/catalog/` — `candidates.py` (LLM prompts → candidate names per archetype×axis), `enrich_youtube.py` / `enrich_podcast.py` / `enrich_x.py` / `enrich_personality.py` (API resolve + thumbnail + metadata), `merge.py` (dedupe + multi-persona union + topic_tag + popularity normalization), `seed_writer.py` (emit `supabase/seed/content_sources.sql` **and** optional direct service-role upsert), `models.py` (Pydantic `CatalogSource`). Intermediate JSON under `agents/catalog/data/<axis>.json` for diff-able review. Mirrors `CLAUDE.md` agent layout.
- **Secrets/env (add to `.env.example`):** `YOUTUBE_API_KEY` (new), `SUPABASE_SERVICE_ROLE_KEY` (exists), `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` for candidate generation (exists). **No X API key** if using the no-API avatar approach (Open Question 2).
- **Reproducibility:** commit the generated `supabase/seed/content_sources.sql` (idempotent) and the `agents/catalog/data/*.json` so the catalog is reviewable and re-appliable, not a one-off black box.

## Sub-phases

### Sub-phase 1 — Curation harness + taxonomy lock + one proven cell
- **Files touched:** `agents/catalog/{models.py,candidates.py,merge.py,seed_writer.py,prompts.py,__init__.py}`, `agents/catalog/enrich_youtube.py` (first axis as the reference enricher), `agents/shared/settings.py` (add `YOUTUBE_API_KEY`), `.env.example`, `reference/source-catalog-taxonomy.md` (new).
- **What ships:** the end-to-end pipeline skeleton — LLM candidate generator (prompts per archetype×axis, over-generating ~70–80), the per-axis enricher *interface* + the YouTube implementation (Data API v3 `search.list`→`channels.list`: real `thumbnails.high`, `statistics.subscriberCount`, `contentDetails` uploads playlist; drop unresolved), the dedupe+multi-persona merge, popularity normalization, topic_tag assignment, and the idempotent `content_sources.sql` writer. Plus **two decisions locked in `reference/source-catalog-taxonomy.md`:** (a) the final archetype set + the **regional/cultural coverage policy** (Open Q1), (b) the controlled `topic_tags` vocabulary (8 keys + curated sub-niches).
- **Definition of done:** running the harness for **one** cell (`ai-frontier-tech` × `youtube_channel`) produces **≥ 50** verified rows with **real** thumbnails + subscriber counts into a generated SQL seed; unresolved candidates are dropped with a logged count; the taxonomy doc is committed. Unit tests (Rule 9) for `merge.py` (dedupe collapses a channel proposed under 2 archetypes into one row with both personas) and `candidates.py` parsing, with the LLM + YouTube API **mocked** (CLAUDE.md mocking rule). `ruff` clean.
- **Dependencies:** none (schema already exists).

### Sub-phase 2 — YouTube + Podcasts axes (full population)
- **Files touched:** `agents/catalog/enrich_podcast.py`, `agents/catalog/data/youtube.json`, `agents/catalog/data/podcast.json`, `supabase/seed/content_sources.sql` (append YT + podcast rows).
- **What ships:** the harness run across **all 12 archetypes** for `youtube_channel` (YouTube Data API v3) and `podcast` (**iTunes Search API** — free, no key: `term` search → `collectionName`, `artworkUrl600`, `feedUrl`, `collectionId`). Each cell ≥ 50 rows with real thumbnails; multi-archetype sources carry all matching persona slugs; `popularity_score` normalized per Open Q4; `balanced-generalist` populated with a cross-category spread.
- **Definition of done:** a SQL assertion proves **every** archetype has **≥ 50** `youtube_channel` rows and **≥ 50** `podcast` rows whose `personas` contains that slug; **thumbnail coverage ≥ 95%** per axis; per-cell counts are logged (no silent shortfall — Rule 12). `topic_tags[0] ∈` the 8 keys for every row (assertion).
- **Dependencies:** SP1.

### Sub-phase 3 — X + Personalities axes (full population)
- **Files touched:** `agents/catalog/enrich_x.py`, `agents/catalog/enrich_personality.py`, `agents/catalog/data/x.json`, `agents/catalog/data/personality.json`, `supabase/seed/content_sources.sql` (append X + personality rows).
- **What ships:** `x_account` rows (handle as `external_id`; **avatar via `unavatar.io/x/<handle>`** — no X API key; follower counts approximate/flagged or null per Open Q2) and `personality` rows (**Wikipedia REST summary / Wikidata P18** for photo + bio; `external_id = slug(name)`; popularity from Wikipedia pageviews or LLM estimate). All proposed entities verified to exist (drop the unverifiable). `balanced-generalist` spread across categories.
- **Definition of done:** SQL assertion: **every** archetype has **≥ 50** `x_account` and **≥ 50** `personality` rows; **thumbnail coverage ≥ 90%** (X/personality are lower-fidelity — the realistic floor is stated, not faked); all data-quality caveats (approx/null follower counts, missing avatars) are **logged and listed** in the SP report, never hidden. `ruff` clean.
- **Dependencies:** SP1 (harness). Parallelizable with SP2 (disjoint axes, disjoint files) if run in a worktree.

### Sub-phase 4 — Apply to remote + end-to-end verification
- **Files touched:** `supabase/seed/content_sources.sql` (final, with apply-time assertions like `archetypes.sql`), `plans/phase-5f-source-catalog-curation-progress.md` (report); **optional** `src/lib/sourceSwipeData.ts` (`CARDS_PER_PLATFORM`) if raising the shown count is chosen.
- **What ships:** the full seed applied to **remote** via service-role/session-pooler; the in-SQL assertions (every archetype × axis ≥ 50; thumbnail coverage; `topic_tags[0]` validity) run on the real DB and **fail loud** if unmet; an in-app check that `SourceSwipe` now shows full decks (incl. `balanced-generalist`, which is what the `SKIP_AUTH` build hits) and **no longer shows "0 sources."** Optionally raise the display cap.
- **Definition of done:** on the **remote** DB, all assertions pass; rebuilding the static export (`npm run build` + `npx cap sync ios`) and reopening the simulator shows **≥ 12 (or the chosen cap) recognizable cards per axis** with thumbnails on the topic-selection → source-swipe flow; the "0 sources" screen is gone. Follow-persistence is **smoke-tested with one real signed-in session** OR explicitly deferred with the `SKIP_AUTH` note (Rule 12 — state which).
- **Dependencies:** SP2, SP3.

## Phase-level definition of done
The remote `content_sources` table holds **≥ 50 thumbnail-bearing, archetype-tagged rows for every one of the 12 profiles on every one of the 4 axes** (verified by in-SQL assertions, per-cell counts logged), and the live onboarding flow shows full, recognizable, relevant source decks per profile — **including the `balanced-generalist` fallback the current build always lands on** — instead of "0 sources." The catalog is reproducible from committed seed + JSON.

## Out of scope
- **Content-item ingestion** from followed sources → story pool — that's **Phase 5d** (`content_source_items`).
- The **control-surface allocation** (how many feed slots each followed source claims) — **M5/M6** (`plans/m5-m6-personalization-sources-control-surface.md`).
- **Periodic catalog refresh / auto-discovery** of new sources over time — a later phase (the original cut Phase 6 intent); this phase is the **initial** curated seed.
- The **auth/persistence** fix (turning off `SKIP_AUTH`, magic-link deep-linking in Capacitor) so follows actually save in the simulator — related but separate; flagged in SP4, not solved here.
- The `personalities` table / spotlights feature.

## Open questions (resolve in SP1 unless noted)
1. **Regional/cultural coverage (your Bollywood example).** The 12 archetypes are generic/Western; "Bollywood" maps to `arts-culture`, "cricket" to `sports-fan`. **Decision:** keep the 12 for v1 and guarantee *intra-archetype diversity* (each archetype's 50 include regional/Indian sources where relevant), **or** add regional archetypes (e.g. `india-entertainment`, `cricket`). Recommend **keep 12 + diversity** for v1, revisit via `/cmo`. The picker taxonomy (`PICKER_TREE`) should be cross-checked so every leaf cluster maps to a profile with a populated catalog.
2. **X axis fidelity.** X API v2 is paid/gated. Recommend **`unavatar.io` avatars + null/approximate follower counts (flagged)** for v1; real X API enrichment is a later upgrade.
3. **Thumbnail hosting.** Hot-link platform CDNs (YouTube/iTunes art, unavatar, Wikipedia) vs. mirror to Supabase Storage. Recommend **hot-link for v1**; mirror only if a CDN rate-limits/breaks.
4. **`popularity_score` derivation.** Per-axis: log-normalized subscriber/listener count → 0–100, or rank-within-archetype → a 50–99 band, or LLM estimate where no count exists. Recommend **log-normalized count where available, rank-band fallback**; keep it monotonic so the deck's top-12 are the genuinely biggest.
5. **Display cap.** Keep deck at 12 shown (store 50) or raise `CARDS_PER_PLATFORM`. Product call — default keep 12.
6. **LLM over-generation factor.** ~70–80 candidates/cell to clear ≥ 50 after drops; tune in SP1 from the observed YouTube resolution rate.

## Self-critique
**Product lens:** PASS — directly delivers the requested behavior (pick categories → assigned a profile → see ≥50 each of YouTube/podcasts/X/people with thumbnails → choose). Prioritizes `balanced-generalist` so the *current* build is unblocked first, not last.
**Engineering lens:** PASS — targets the **verified** consumption path (one table, persona-overlap + type filter, single-archetype, top‑12 of 50), reuses the existing recommender/UI unchanged (Rule 8), mirrors `archetypes.sql` idempotency, holds to the Python-agent + env-safety + mocking conventions (CLAUDE.md). Anti-hallucination (API-resolve-or-drop) is a first-class DoD, not an afterthought.
**Risk lens:** PASS with flags. Additive data only — no schema change, no drops. ⚠ Real outward API calls (YouTube/iTunes/unavatar/Wikipedia) + LLM cost during generation — bounded by over-generation factor + free APIs where possible; X fidelity and regional coverage surfaced as **open questions, not hidden defaults**. Thumbnail hot-linking is a known fragility (flagged, mirrorable later). Per-cell **≥50 counts are asserted and logged** so a silent shortfall fails loud (Rule 12).
**Irreversible sub-phases:** none (additive, idempotent upsert; reversible by deleting `is_curated` catalog rows on a backed-up DB).
