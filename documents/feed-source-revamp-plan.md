# Plan — Feed Importance + Source-First Personalization Revamp

**Date:** 2026-06-29
**Branch:** `claude/feed-importance-diagnosis-vnzoqq`
**Status:** Plan approved by product owner (ash@gmail.com). Not yet implemented. Build to be sequenced via `/cto` → `/plan-phases` → `/run-phase`.

> Durable handoff doc. Safe to `/compact` after this is committed — a fresh session can resume from here.

---

## 1. Background — what we diagnosed

Ash's feed surfaced minor, mis-categorized stories ("Cala Homes teaches construction safety" as TOP TECH; a retail takeover as GEOPOLITICS). Root cause, confirmed by code trace:

- Stories enter the pool **and get their category** from a single loose mechanism: did *any one* keyword/anchor term appear in an article's `title + entities`? (`agents/ingestion/adapters/gdelt_bigquery.py`, category set in `agents/pipeline/stages/ranking.py::assign_category`). No relevance check; **category is inherited from the matched keyword**, not from reading the story.
- There is **no "top stories of the day" pull** — the pool only ever contains what narrow interest-keyword queries dragged in. Hence "don't you have anything better to report?" is correct: the big story was often never fetched.
- Importance = `min(1, story_outlet_count/12)` (`agents/pipeline/produce_gate.py` ~L75-100) is sound but raw — un-weighted by source authority, un-normalized, no decay, gameable by syndication.
- Ranking `Score = (Affinity×DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2` (`ranking.py`) gives importance too little weight to lift big stories over well-matched minor ones.

Prior-art research (news aggregators, GDELT themes, salience literature) backs the fixes below.

## 2. Product direction (decided)

**Thesis: news is a shared backbone; the personalization comes from who you follow.**

1. **Interests collapse to top-level categories only.** Users pick geopolitics / tech / markets / sport / etc. No 2-layer drill-down. Existing deep selections auto-collapse to their root category.
2. **News = the day's biggest stories per selected category, from a trusted-outlet set.** Replaces keyword-first fetching. Niche depth migrates OUT of news.
3. **YouTube + X + Personalities become the primary personalization.** Already-built ingestion (`source_pipeline.py`) is reweighted to high prominence. Niche depth (e.g. deep cricket) now comes from the creators/accounts you follow, not news keywords.
4. **Source selection in onboarding** (after categories): present **YouTube channels, X accounts, and Personalities**, filtered to the chosen categories (no randoms), bulk-selectable via **clusters**.

## 3. Workstreams (file-anchored)

### WS1 — Interests → top-level categories
- Onboarding interest picker reduced to top-level categories (1 layer). Specs/UI: `onboarding_interest_picker_spec.md`, `interest_picker.html`, app onboarding under `src/`.
- Migration: collapse existing `user` deep-interest selections to their root category. Category roots live in `agents/pipeline/categories.py` (`SLUG_TO_CATEGORY`, `category_for_slug`).
- Downstream: news fetching no longer keyed on deep interests (see WS4).

### WS2 — Source catalog + clusters (data + content-ops) — *riskiest, do early*
- **Schema already supports most of this** (`supabase/migrations/0009_content_sources.sql`): `content_sources` (curated catalog) has `topic_tags` (category relevance, GIN-indexed) and `popularity_score`; `personalities` is a separate bundle entity with `user_personalities` + `personality_appearances`.
- **NEW:** a **cluster** structure — a named grouping of catalog rows within a category ("Leading AI-lab researchers", "AI founders", "AI journalists"). New table (e.g. `source_clusters` + `source_cluster_members`) or a tag convention. One cluster model for **both** X and YouTube (YouTube just has fewer members per cluster).
- **No-dup rule:** a person who is a followable **personality** is shown ONCE as a personality card; do NOT also surface their individual YouTube/X rows. Personalities bundle their handles.
- **Catalog population — editorial-first hybrid (approved):** hand-curate a seed catalog per category (several hundred accounts + cluster labels) for launch trust + "no randoms"; expand algorithmically later (co-follow graphs, bio-embedding similarity) ranked by `popularity_score`. This is a **content-operations asset**, not just code — onboarding quality = catalog quality.

### WS3 — Source-selection onboarding UI
- After category selection, three surfaces: **YouTube channels, X accounts, Personalities**, each filtered by `topic_tags` ∩ selected categories, ordered by `popularity_score`.
- **Clusters** for easy multi-select (bulk-select a cluster = follow its members). Especially for X (many accounts).
- **Targets:** ~30–40 YouTube channels, ~40–50 X accounts, ~4 personalities per user.
- **Friction mitigation:** pre-select recommended clusters; user **deselects** (opt-out) rather than hand-picking ~90 accounts.
- Writes to `user_content_sources` / `user_personalities`.

### WS4 — News ingestion revamp
- **Trusted-outlet top-stories fetch per category.** Prefer the existing **`agents/ingestion/adapters/gdelt_doc.py`** (GDELT DOC 2.0 API supports `domainis:` domain filtering) over keyword matching. Curate ~10–15 authority domains.
- **Category from GDELT themes**, not the matched keyword — whitelist `V2Themes`/`V2EnhancedThemes` codes per category (confirm BigQuery row exposes themes; DOC API alternative). Replaces keyword-inherited category in `ranking.py::assign_category`.
- **Importance upgrade** in `produce_gate.py`: authority-weighted outlet count (source-tier table) + volume normalization + time-decay + syndication/burst dampening. Replaces raw `outlet_count/12`.
- Retire / down-scope `interest_keyed_pipeline.py` keyword fetching for news (kept only if a hybrid is later wanted).

### WS5 — Feed assembly reweighting (YT/X prominence)
- Followed-source items already **bypass the importance gate** (`source_pipeline.py` promotes them gate-exempt). Now give them **priority slots** in `agents/pipeline/feed_assembly.py` + ranking.
- **Default composition (confirm in /cto):** all fresh followed-source items get guaranteed slots first, then category top-stories fill the remainder of the ~30-reel feed.
- Revisit `agents/pipeline/produce_caps.py` (per-category 1.5× headroom) for the new mix.

### WS6 — Summarization quality (long vs short)
- YT/X already summarized via shared scripting (`agents/pipeline/stages/scripting.py`, `prompts.py`, `detail_enrichment.py`). Refine prompts: long-form video → key-points summary; short-form / tweets → tight summary. No new ingestion.

### WS7 — Documentation
- Update: `onboarding_interest_picker_spec.md`, `personalization-and-source-curation-spec.md`, `README.md`, reference docs under `reference/`, and PRD/Technical-Foundation via `/cto`.

## 4. Sequencing (proposed phases)

1. **Phase A — Catalog & clusters (WS2):** schema for clusters, seed editorial catalog + tags. *Unblocks everything.*
2. **Phase B — News ranking fix (WS4):** themes-based category + authority-weighted importance (offline unit-testable first), then trusted-outlet fetch.
3. **Phase C — Onboarding (WS1 + WS3):** top-level categories + source/cluster selection UI.
4. **Phase D — Feed mix + summaries (WS5 + WS6):** reweight YT/X, refine summaries.
5. **Phase E — Docs (WS7).**

## 5. Risks & open questions

- **Catalog quality is the #1 risk.** Thin/mis-tagged catalog → "random people" returns. Editorial seed mitigates.
- **News commoditization:** users with the same categories get the same news. Accepted by design — YT/X carries uniqueness. Watch that the personalization layer is strong enough.
- **Exact feed composition** (news vs YT/X ratio, guaranteed-slot rules) — pin in `/cto`.
- **Trusted-outlet list** per category — to define.
- **Personality count** ("the same four personalities" → ~4 assumed; confirm).
- **Cluster authoring** — manual at seed; auto-generation (co-follow/embeddings) is a later enhancement.

## 6. Verification constraints (important)

This environment **cannot run the pipeline live**: GDELT is egress-blocked (`api.gdeltproject.org` → policy 403) and there are **no DB/GDELT creds** (`.env` absent). Therefore:
- Code + **offline unit tests** can be written and verified here (e.g. theme→category mapping, authority-weighted scoring, cluster filtering).
- **Live end-to-end** (real GDELT pull, real DB) must run in a credentialed environment. Do NOT claim end-to-end "works" from this sandbox.

## 7. Next step

Run `/cto` against this plan to produce the PRD + Technical Foundation + reference docs, then `/plan-phases` to break Phases A–E into 4-sub-phase units, then `/run-phase` to build (offline-verifiable slices first).
