# PRD — Feed Importance + Source-First Personalization Revamp

**Date:** 2026-06-30
**Source:** documents/feed-source-revamp-plan.md (approved by product owner ash@gmail.com, 2026-06-29)
**Branch:** claude/feed-source-revamp-plan-388edf
**Status:** Ready for /plan-phases

> **Master-plan role (read this first).** `/plan-phases` defaults to `plans/master-plan.md`, but that file is a **stale, unrelated** 2026-05-28 doc — do NOT read it for this work and do NOT modify it. **This PRD is the self-sufficient master plan for the revamp.** The milestones are enumerated as a flat list in the Technical Foundation (`## Milestones`). When invoking `/plan-phases`, point it at `plans/prd.md` and pass the M1..M7 list explicitly.

> **Scope guard.** This PRD covers ONLY the feed-importance + source-first-personalization revamp. It does not re-spec the reel, audio, detail/trust, Q&A, voice, auth, or iOS shell — those shipped (M1–M5) and are unchanged here.

---

## Problem Statement

Ash opens his daily briefing and the feed surfaces minor, mis-categorized stories — a local construction-safety item labelled "TOP TECH", a retail takeover labelled "GEOPOLITICS" — while the day's genuinely big story is often missing entirely. His reaction ("don't you have anything better to report?") is correct on the facts:

1. **No "top stories of the day" pull.** The news pool only ever contains what narrow interest-keyword queries dragged in. If a keyword query didn't fetch the big story, it was never in the pool to rank.
2. **Category is inherited from the matched keyword, not from reading the story.** A story enters the pool and gets its category because *one* anchor term appeared in its title-or-entities haystack — there is no relevance/topic check, so a keyword coincidence yields a wrong category.
3. **Importance is raw and gameable.** `Importance = min(1, story_outlet_count / 12)` is un-weighted by source authority, un-normalized across categories, has no time-decay, and is trivially inflated by syndication.
4. **Importance is under-weighted in ranking.** `Score = (Affinity×DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2` lets a well-matched minor story beat a genuinely big one.

Underneath the bug is a product question the diagnosis forced: *what actually makes blip's feed yours?* Keyword-fetched "niche news" is a weak, noisy personalization signal. The answer the owner committed to: **news is a shared backbone; personalization comes from the creators and accounts you follow.**

## Solution

From the user's perspective:

- **Onboarding asks for top-level interests only** (geopolitics / tech / markets / sport / …). No 2-layer drill-down. Any existing deep selections collapse to their root category.
- **News = the day's biggest stories per chosen category**, pulled from a trusted set of outlets — not from keyword scrapes. Two users who pick the same categories see broadly the same news. That's intentional: news is the shared backbone.
- **Your personalization is the people you follow.** After categories, onboarding presents **YouTube channels, X accounts, and Personalities**, filtered to your chosen categories, bulk-selectable via **clusters** ("Leading AI-lab researchers", "AI founders", "AI journalists"). You opt out of pre-selected clusters rather than hand-pick ~90 accounts. Niche depth (deep cricket, a specific founder's takes) now comes from these follows, not from news keywords.
- **Your feed leads with what you follow.** Fresh items from followed sources get guaranteed slots first; the day's category top-stories fill the rest of the ~30-reel feed.
- **The big story is correctly sized and correctly labelled.** Importance is authority-weighted, normalized within category, and time-decayed; category comes from what the story is *about*, not which keyword happened to match.

## Technical Foundation

*This section is the durable technical north star — there is no separate master plan for this revamp.*

### Tech stack (fixed — already live; one-line rationale each)

- **Frontend:** Next.js 15 (static export, `output: "export"`) + React 19 + Tailwind 4, packaged in a **Capacitor** iOS shell — chosen so the binary stays thin and all dynamic data flows via the Supabase client. *Unchanged by this revamp; onboarding UI reuses existing design tokens.*
- **Backend / data layer:** **Supabase** (Postgres + email-OTP auth + storage/CDN). Public-read content tables, per-`auth.uid()` user tables, service-role-only pipeline writes — the contract every table in this revamp follows.
- **Agent / pipeline layer:** **Python** daily batch (`agents/pipeline/*`, `agents/ingestion/*`) — the ranking/allocation/clustering is a heavy multi-pass best unit-tested in Python; the static-export client has no server runtime, so there is **no live ranking RPC**. Feeds are precomputed into `daily_feeds`.
- **Background jobs:** **Trigger.dev v4** crons (daily batch + source-ingestion cadence). *Unchanged.*
- **Hosting:** Supabase (data/auth) + Vercel (any remote API) + Trigger.dev (jobs); iOS via Capacitor/TestFlight. *Unchanged.*
- **Languages:** TypeScript (app) + Python 3.12 (pipeline). *Unchanged.*

### Live-pipeline verification constraint (HARD — applies to every milestone DoD)

This sandbox **cannot run the pipeline live.** GDELT egress is blocked (`api.gdeltproject.org` → policy 403) and there are **no DB/GDELT/BigQuery credentials** (`.env` absent). Therefore:

- **Verifiable here (OFFLINE):** all pure functions and their unit tests — theme→category mapping, authority-weighted/normalized/decayed importance scoring, cluster filtering + no-dup rule, allocation/slot math, SQL migrations as static artifacts (parse/lint, not apply), and any component rendered against fixtures. Every milestone below names an **offline de-risking check** that must pass here.
- **Deferred to a credentialed environment (LIVE-E2E):** anything that requires a real GDELT pull (BigQuery GKG or DOC 2.0) or a real Supabase write/read — trusted-outlet fetch returning real domains, end-to-end batch producing a real `daily_feeds`, live theme coverage, catalog seeding against the live DB. **No milestone may claim "works end-to-end" from this sandbox** (Rule 12). Each milestone marks its LIVE-E2E residual explicitly.

### Architecture (data flow)

```
 ONBOARDING (Capacitor/Next, reuses existing tokens)
   top-level category picker ──► user_interest_profile (roots only; deep picks collapsed)
   source/cluster picker ───────► user_content_sources / user_personalities
        ▲ filtered by topic_tags ∩ chosen categories, ordered by popularity_score,
        │ bulk-selected via source_clusters (no-dup: a personality hides its own YT/X rows)
        │
 CATALOG (content-ops asset, Supabase)
   content_sources (topic_tags, popularity_score) · personalities · NEW source_clusters/_members
        │
 ===================  DAILY PYTHON BATCH (Trigger.dev v4)  ===================
        │
   (B) INGEST ─┬─ News: trusted-outlet top-stories per category
        │      │     GDELT GKG (add V2Themes to SELECT) / DOC (domainis: filter)
        │      │     → category from THEME whitelist, not matched keyword
        │      └─ Followed sources: source_pipeline (YouTube/X) → same pool, gate-exempt
        │
   (C) CLUSTER once (existing agents/pipeline/clustering/*) → story_clusters
        │
   (E1) IMPORTANCE  story_importance = breadth + AUTHORITY + velocity + recency + entity,
        │           normalized WITHIN category   [replaces min(1, outlet_count/12)]
        │
   (RANK) ranking.py Score = (Affinity×DepthMatch)·α + Importance·β + Freshness·γ + EntityBonus
        │           (raise β so big stories lift; α/β/γ are config constants)
        │
   (ASSEMBLE) feed_assembly.py — followed-source items take guaranteed slots FIRST,
        │           category top-stories fill the remaining ~30
        ▼
   daily_feeds  ──►  client reads its own rows (RLS) ──►  reel (unchanged Story[] contract)
```

### Key design decisions

1. **News is a shared backbone; personalization is source-follows.** *Why:* keyword-fetched niche news is a noisy, weak personalization signal and the root of the mis-categorization bug; followed creators/accounts are an explicit, high-signal preference. *Rules out:* per-user keyword-scraped "niche news" as the personalization layer (down-scoped, not deleted — a hybrid stays possible later).
2. **Interests collapse to top-level categories (1 layer).** *Why:* the depth tree fed the keyword-fetch machinery that caused the bug, and source-follows now carry depth. *Rules out:* the 2-layer onboarding drill-down for *news fetching* (the hierarchical `interests` table and `DepthMatch` term **remain** for scoring source-tagged and followed-entity content; only the onboarding picker and news-fetch keying collapse to roots). Existing deep `user_interest_profile` rows migrate to their root via `category_for_slug`.
3. **Category from GDELT themes, not matched keyword.** *Why:* the matched keyword is a coincidence, not a topic; `V2Themes`/`V2EnhancedThemes` is GDELT's own topic signal. *Rules out:* keyword-inherited category at ingestion. Note: `ranking.py::assign_category` already classifies from the lowest-`match_depth` `story_interests` tag — so the fix lands at **ingestion-time tagging** (which tag a story gets), via a theme→category whitelist, not in `assign_category` itself.
4. **Trusted-outlet top-stories fetch replaces keyword-first fetch for news.** *Why:* guarantees the day's big story is *in the pool* and raises baseline trust ("no randoms"). *Rules out:* relying on interest-keyword queries to surface big stories. Prefer the existing `GdeltDocAdapter` (DOC 2.0 supports `domainis:` domain filtering — net-new query construction) or add a curated-domain filter + `V2Themes` to the `GdeltBigQueryAdapter` GKG SELECT (themes are **not** selected today). Curate ~10–15 authority domains per category.
5. **Importance upgrade = implement the shared-pool E1 model, not a parallel one (Rule 7 — conflict surfaced).** The **shared-pool rework** (`reference/shared-pool-pipeline.md` §4, `plans/shared-pool-rework-master-plan.md`, **Active**; M3a shipped, migration `0018_story_clusters.sql` applied 2026-06-19) already defines `story_importance = W_breadth·breadth + W_authority·authority_and_diversity + W_velocity·velocity + W_recency·recency + W_entity·entity`, **normalized within category**. The revamp's WS4 "authority-weighted outlet count + normalization + decay + syndication dampening" **is** that E1 — so this revamp **implements/finishes E1's authority + within-category-normalization + syndication-dampening terms** and the source-tier table, rather than authoring a second importance formula. *Rules out:* a competing `produce_gate.compute_importance_score` design. The raw `min(1, outlet_count/12)` is the term E1 replaces.
6. **Clusters are a NEW grouping layer in the catalog (net-new).** *Why:* onboarding must bulk-select ~90 accounts without ~90 taps; a cluster is a named grouping of catalog rows within a category. *Rules out:* reusing `archetypes`/`personas` for this — verified: no cluster abstraction exists today; archetypes are a single-level recommendation key, not a user-facing bulk-select grouping. One cluster model serves **both** X and YouTube (YouTube clusters just have fewer members).
7. **No-dup rule: a followable personality is shown ONCE as a personality card.** *Why:* a person who is both a `personality` (bundling handles via `youtube_channel_ids`/aliases) and present as individual YouTube/X catalog rows must not appear twice. *Rules out:* surfacing a personality's individual source rows alongside their personality card.
8. **Followed-source items get guaranteed slots first; category top-stories fill the rest.** *Why:* the product thesis is that follows are the personalization — they must be visible, not buried. *Rules out:* treating source items as ordinary importance-gated candidates. They already bypass the produce gate (`produce_gate.evaluate_story_for_production`, source-origin exemption) and flow through source slots (`feed_assembly._fill_source_slots`); this revamp gives them **priority** in assembly + ranking and revisits `produce_caps` headroom for the new mix.
9. **Editorial-first hybrid catalog (content-ops asset).** *Why:* onboarding quality == catalog quality; a thin/mis-tagged catalog returns "random people." *Rules out:* a launch that depends on algorithmic discovery. Hand-curate a seed catalog (several hundred accounts + cluster labels) per category for launch; algorithmic expansion (co-follow graphs, bio-embedding similarity, ranked by `popularity_score`) is later (out of scope here).
10. **Summaries differentiate long-form vs short-form.** *Why:* a 90-min podcast and a tweet need different summary shapes. *Rules out:* one prompt for all source content. Refine existing `agents/pipeline/stages/scripting.py`/`prompts.py`/`detail_enrichment.py` prompts only — no new ingestion.

### Module contracts (one per deep module — prose, NOT tests)

**Source catalog + clusters (`content_sources` / `personalities` / NEW `source_clusters` + `source_cluster_members`; migrations under `supabase/migrations/`).**
- *Responsibility:* be the single curated, category-tagged, popularity-ranked catalog of followable sources and the named clusters that group them within a category.
- *Requirements:* every catalog row carries `topic_tags` (∩ the 8 categories) and `popularity_score`; a cluster belongs to exactly one category and lists ordered member rows; one cluster model covers both X and YouTube; public-read RLS, service-role writes.
- *Edge cases:* a personality whose handles also exist as individual catalog rows (no-dup: hide the rows); a cluster whose member was deleted/un-curated (skip, don't error); an empty cluster (don't surface); a row tagged to multiple categories (appears under each, deduped per category); a member appearing in two clusters of the same category (allowed; dedup at selection).

**Onboarding selection (top-level category picker + source/cluster picker; `src/components/...`, writes `user_interest_profile` / `user_content_sources` / `user_personalities`).**
- *Responsibility:* capture root-category interests and pre-select recommended clusters the user opts out of, then persist follows.
- *Requirements:* picker shows top-level categories only; source surfaces are filtered by `topic_tags ∩ chosen categories`, ordered by `popularity_score`, never "randoms"; selecting a cluster follows all its members; pre-select recommended clusters so the user *deselects*; honor the no-dup rule in the rendered grid.
- *Edge cases:* user picks zero clusters (allowed — they still get shared-backbone news; feed must not be empty); a chosen category with an empty catalog cell (show graceful fallback, never randoms); deselecting a cluster after individually keeping one member (member stays followed); existing deep-interest user migrating (collapse to roots, no dupes); target volumes ~30–40 YT, ~40–50 X, ~4 personalities per user.

**News ingestion — trusted-outlet top-stories + theme category (`agents/ingestion/adapters/gdelt_doc.py` / `gdelt_bigquery.py`, `agents/ingestion/interest_keyed_pipeline.py`).**
- *Responsibility:* pull the day's biggest stories per selected category from a curated authority-domain set, and assign each story's category from its GDELT theme, not the matched keyword.
- *Requirements:* fetch is keyed on category + trusted domains (DOC `domainis:` or GKG `SourceCommonName ∈ curated set`), not narrow interest keywords; category derives from a `V2Themes`/`V2EnhancedThemes` → category whitelist (GKG SELECT must add `V2Themes`; DOC has no themes param, so GKG is the theme source); ingestion stays per-interest-resilient (one source failing skips that cell, never the batch).
- *Edge cases:* a story matching multiple category themes (pick the dominant/whitelisted-best, deterministic tiebreak); a story with no whitelisted theme (fallback heuristic, never dropped silently — fail loud per cell); GDELT rate-limit/plaintext-throttle on the DOC path (≤1 req/5s backoff, non-fatal); syndicated reprints inflating outlet count (deduped before importance, see clustering + E1 dampening); a trusted domain returning nothing for a category (gap-fill widens window, logs under-fill).

**Importance scoring — E1 (the `story_importance` computation; supersedes `produce_gate.compute_importance_score`, aligns to `reference/shared-pool-pipeline.md` §4).**
- *Responsibility:* compute one intrinsic, category-normalized importance per clustered story from breadth + source authority + velocity + recency + entity prominence.
- *Requirements:* authority-weight outlet count via a source-tier table (high-authority, ideologically varied > N content farms); normalize **within category** (a big sport story competes with sport); apply ~24h recency decay (reuse the Freshness half-life); dampen syndication/burst (cluster-deduped breadth, not raw reprint count); be a single config-driven function, no scattered constants.
- *Edge cases:* single-outlet followed-source item (importance is irrelevant — it's gate-exempt, slotted by follow); a category with one story (normalization must not divide-by-zero / must not inflate it to 1.0 spuriously); a syndication burst from one wire (authority+dedup caps its lift); a future-dated `seendate` (clamp recency at 1.0); empty category (no candidates → no rows, not a crash).

**Feed assembly reweighting (`agents/pipeline/feed_assembly.py`, `agents/pipeline/produce_caps.py`).**
- *Responsibility:* assemble the ~30-slot feed so fresh followed-source items take guaranteed slots first, then category top-stories fill the remainder, totalling 30.
- *Requirements:* all fresh followed-source items get priority slots before topic fill; remaining slots fill from category top-`Score` candidates in the user's sequence; preserve don't-repeat (prior `daily_feeds`) + within-feed dedup; feed still totals `FEED_SLOT_BUDGET = 30`; revisit per-category produce-cap headroom for the new mix.
- *Edge cases:* more fresh source items than the feed budget (cap/spill by recency+importance, document the rule); zero followed sources (full feed is category news, never empty); source budget set but no source items today (existing soft-roll into topic categories by sequence — preserve); a story qualifying for both a source slot and a topic slot (place once, source wins); user with no allocation rows (balanced fallback, unchanged).

### Milestones

> These M1..M7 are the planner input. Each has a crisp "what's true when done" and an **OFFLINE de-risking check** verifiable in this sandbox plus the **LIVE-E2E residual** that must run in a credentialed env. (The plan's Phases A–E map here: A→M1, B→M2+M3, C→M4+M5, D→M6, E→M7.)

- **M1 — Catalog clusters + no-dup (data + content-ops).** *Done when:* a `source_clusters` + `source_cluster_members` schema exists; a seed of named clusters per category over existing `content_sources`/`personalities` is authored; a query returns, for a category, its clusters with ordered members **honoring the no-dup rule** (a personality's own handles are excluded from individual rows). *Riskiest assumption lives here* (catalog quality = the #1 risk). *Offline check:* unit tests over the cluster/no-dup resolver against fixture catalog rows (cluster membership, empty cluster, personality-dedup, multi-category row); migration parses/lints. *Live-E2E residual:* seeding clusters into the live DB and confirming real catalog coverage per category.

- **M2 — News category from GDELT themes.** *Done when:* a pure `theme → category` whitelist mapping exists for all 8 categories and ingestion assigns category from `V2Themes`/`V2EnhancedThemes` (GKG SELECT adds `V2Themes`) instead of the matched keyword, with a deterministic tiebreak and a fail-loud fallback for theme-less stories. *Offline check:* unit tests mapping representative theme codes → expected category, multi-theme tiebreak, and the no-theme fallback — all against fixtures (no GDELT call). *Live-E2E residual:* confirm real GKG rows expose the expected themes and categories look right on a live pull.

- **M3 — Authority-weighted importance (E1).** *Done when:* `story_importance` implements breadth + **authority (source-tier table)** + velocity + recency + entity, **normalized within category**, with syndication dampening, replacing `min(1, outlet_count/12)`; and `ranking.py` β is raised so a genuinely big story outranks a well-matched minor one. *Offline check:* unit tests showing (a) an authority-varied 10-outlet story beats a 20-content-farm syndication burst, (b) within-category normalization, (c) the big-story-beats-minor reordering at the new β — all on synthetic clusters. *Live-E2E residual:* importance ordering sanity on a real day's clustered pool.

- **M4 — Trusted-outlet top-stories fetch.** *Done when:* news fetch is keyed on category + a curated ~10–15-domain authority set per category (DOC `domainis:` query construction or GKG `SourceCommonName ∈ set`), replacing keyword-first fetch for news; per-cell resilience + bounded gap-fill preserved. *Offline check:* unit tests over the query-builder (correct `domainis:`/SQL domain filter emitted per category, throttle/backoff path, gap-fill widening) against mocked adapter responses. *Live-E2E residual:* a real GDELT pull returns the day's big stories from the curated domains for each category.

- **M5 — Top-level-category onboarding + interest collapse.** *Done when:* the onboarding interest picker shows top-level categories only; a migration/transform collapses existing deep `user_interest_profile` rows to their root via `category_for_slug` (idempotent, no dupes). *Offline check:* unit tests on the collapse transform (deep→root, dedupe on conflict, idempotency) + the picker rendered against fixtures showing roots only. *Live-E2E residual:* run the collapse against live profiles; verify no orphaned/duplicate rows.

- **M6 — Source/cluster onboarding UI + priority feed mix.** *Done when:* after categories, the user sees YouTube/X/Personalities filtered by `topic_tags ∩ chosen categories`, ordered by `popularity_score`, with pre-selected recommended **clusters** they deselect (opt-out), writing to `user_content_sources`/`user_personalities`; and `feed_assembly` gives fresh followed-source items guaranteed slots first with category top-stories filling the rest to 30 (produce-cap headroom revisited). *Offline check:* component tests (filter ∩, popularity order, cluster bulk-select, no-dup grid, zero-cluster path) against fixtures + allocator unit tests (source-first priority, totals-30, don't-repeat, soft-roll, over-budget spill). *Live-E2E residual:* end-to-end onboarding → batch → a real personalized `daily_feeds` leading with followed-source items.

- **M7 — Summaries (long vs short) + docs.** *Done when:* `scripting.py`/`prompts.py`/`detail_enrichment.py` prompts produce a key-points summary for long-form video and a tight summary for short-form/tweets; and `reference/ranking-spec.md` + the source taxonomy/reuse docs are updated to the new model. *Offline check:* prompt-shaping unit tests / golden-summary fixtures distinguishing long vs short; reference docs updated and internally consistent. *Live-E2E residual:* summary quality spot-check on real fetched transcripts/tweets.

### Riskiest assumption + how we de-risk it

**Catalog quality is the #1 risk** — a thin or mis-tagged catalog returns "random people," collapsing the whole source-first thesis. De-risked in **M1**: the editorial-first seed + the cluster/no-dup resolver are built and unit-tested against fixtures here; the live residual (real per-category coverage) is the first thing to validate in a credentialed env. Secondary risk — **news commoditization** (same categories → same news) — is accepted by design; M6's source-first feed mix is what must carry uniqueness, so its allocator tests assert followed items actually lead the feed.

## User Stories

1. As a commuter onboarding for the first time, I want to pick only top-level interests (geopolitics, tech, markets, sport, …), so that setup is fast and I'm not forced into a drill-down.
2. As an existing user with deep interest selections, I want those to collapse to their root category automatically, so that I don't lose my setup or get a broken profile after the change.
3. As a user, I want the news in my feed to be the day's biggest stories in my chosen categories, so that I stop seeing minor, irrelevant items as "top" news.
4. As a user, I want big stories pulled from trusted outlets, so that I trust the feed isn't surfacing randoms or fringe sources.
5. As a user, I want each story labelled by what it's actually about, so that a retail takeover isn't filed under "geopolitics."
6. As a user, I want the genuinely big story of the day to actually appear, so that I feel caught up rather than asking "is that really all?"
7. As a user, I want a well-matched minor story not to outrank a major one, so that importance is respected in ordering.
8. As a user, after picking categories I want to choose YouTube channels relevant to those categories, so that my feed reflects creators I value.
9. As a user, I want to choose X accounts relevant to my categories, so that the accounts I follow shape my feed.
10. As a user, I want to follow a small set of Personalities, so that named voices I care about appear consistently.
11. As a user, I want to bulk-select a cluster of accounts ("AI founders") in one tap, so that I don't have to pick ~90 accounts individually.
12. As a user, I want recommended clusters pre-selected so I just deselect what I don't want, so that onboarding is opt-out, not laborious opt-in.
13. As a user, I want a personality I follow to appear once (not also as separate YouTube/X rows), so that the selection screen isn't cluttered with duplicates.
14. As a user, I want only category-relevant sources shown (no randoms), so that every option feels curated and trustworthy.
15. As a user, I want sources ordered by popularity, so that the most relevant/known options surface first.
16. As a user, I want fresh items from the creators and accounts I follow to lead my feed, so that my follows are clearly the personalization.
17. As a user, I want the rest of my ~30-reel feed filled with the day's category top-stories, so that I still get the shared-backbone news.
18. As a user following niche creators (e.g. deep cricket), I want that depth to come from those follows, so that niche interest survives even though news is top-level only.
19. As a user, I want a long-form video summarized as key points, so that I get the substance of a 90-minute podcast quickly.
20. As a user, I want a tweet or short clip summarized tightly, so that short content isn't over-summarized into filler.
21. As a user who follows nothing yet, I want a sensible category-only feed, so that I'm never shown an empty or broken feed.
22. As a user, I want a syndicated wire story not to dominate importance just because many outlets reprinted it, so that real breadth (varied authoritative outlets) wins.
23. As the product owner, I want the importance formula to be authority-weighted, normalized, and decayed, so that ordering is defensible and not gameable.
24. As the product owner, I want the catalog to be an editorially-curated content-ops asset, so that launch quality doesn't depend on unproven discovery algorithms.
25. As an engineer, I want every milestone to have an offline-verifiable check, so that progress is provable in the credential-less sandbox.
26. As an engineer, I want the importance revamp to implement the existing shared-pool E1 model rather than a parallel one, so that we don't fork two contradictory importance designs.

## Implementation Decisions

- **Cluster schema is net-new**: add `source_clusters` (one row per named grouping, `cluster_category` ∈ the 8 categories, ordered) + `source_cluster_members` (cluster_id × source/personality ref, ordered). One model serves both X and YouTube. Do not overload `archetypes`/`personas` (they are a separate recommendation key).
- **No-dup rule** lives in the catalog/selection resolver: a personality bundles its `youtube_channel_ids`/aliases; the resolver excludes those individual `content_sources` rows from the grid when the personality card is shown.
- **Theme→category** is a static whitelist keyed to the 8 categories (`agents/pipeline/categories.py` is the category source of truth). `GdeltBigQueryAdapter` must add `V2Themes` to its GKG SELECT (it currently selects only `V2Persons/V2Organizations/V2Locations`). The DOC API has no theme parameter, so GKG is the theme source; DOC carries the `domainis:` trusted-domain fetch.
- **Category fix is at ingestion-time tagging**, not `assign_category`: `ranking.py::assign_category` already picks the lowest-`match_depth` `story_interests` tag — correct behavior given correct tags. The bug is which tag a story gets at ingest; theme-based tagging fixes the input.
- **Importance = E1** per `reference/shared-pool-pipeline.md` §4: `story_importance = W_breadth·breadth + W_authority·authority_and_diversity + W_velocity·velocity + W_recency·recency + W_entity·entity`, normalized within category. This revamp finishes the **authority** + **within-category normalization** + **syndication-dampening** terms and the source-tier authority table; weights are config constants. Retire `produce_gate.compute_importance_score`'s `min(1, outlet_count/12)` as E1 lands.
- **Ranking weights** (`ranking.py`): `AFFINITY_WEIGHT/IMPORTANCE_WEIGHT/FRESHNESS_WEIGHT = 0.5/0.3/0.2`, `ENTITY_BONUS_WEIGHT = 0.3`, `DEFAULT_SCORE_THRESHOLD = 0.20` — raise `IMPORTANCE_WEIGHT` (β) for the big-story lift; keep them single config constants (no scattering).
- **Feed mix** (`feed_assembly.py`, `FEED_SLOT_BUDGET = 30`): followed-source items fill `SLOT_KIND_SOURCE` slots **first/with priority**, topic categories fill the rest in user sequence; preserve don't-repeat + within-feed dedup + the existing source-budget soft-roll; revisit `produce_caps` `headroom_multiplier` (currently 1.0; phase-5d notes 1.5) for the new mix.
- **Interest collapse**: a one-time idempotent transform mapping each deep `user_interest_profile.profile_interest_id` to its root via `category_for_slug`, deduping on conflict (keep the higher weight), writing `profile_source` unchanged.
- **Source ingestion is reused, not rebuilt**: `source_pipeline.run_source_ingestion` (YouTube/X, cadence 6h, dedup, ≥80-char substance filter, cluster→promote) and the produce-gate source-origin exemption already exist; this revamp reweights assembly, it does not re-author ingestion. Podcast/personality *ingestion* remains out of scope (only personality *selection* is in scope).
- **Summaries**: refine existing prompts only (`scripting.py`/`prompts.py`/`detail_enrichment.py`) — long-form → key-points, short-form/tweets → tight. No new ingestion or schema.

## Testing Decisions

Tests verify **external behavior**, not implementation (Rule 9), and every load-bearing test must be able to fail when the business rule changes:

- **Pure functions get the strongest offline coverage** — theme→category mapping, E1 importance (authority beats syndication burst, within-category normalization, recency clamp), cluster/no-dup resolver, interest collapse, allocation/slot math. Mirror existing Python test structure (`test_<module>.py`), mock all external services (GDELT, BigQuery, DB, LLM) at the boundary — prior art: `agents/pipeline/sim/*` and the existing ranking/produce-gate/feed-assembly tests.
- **Encode the WHY**: the importance test must assert the *reason* (an authority-varied 10-outlet story beats a 20-content-farm burst — not merely "returns a float"); the allocator test must assert followed items *lead* the feed (the product thesis), not just "30 rows."
- **Migrations** are tested as static artifacts here (parse/lint, structural assertions), not applied — applying needs live DB creds.
- **UI** tested against fixtures (roots-only picker, ∩-filtered + popularity-ordered grid, cluster bulk-select, no-dup, zero-follow path); browser/live verification is a LIVE-E2E residual.
- **Fail loud (Rule 12):** a milestone is not "done" if its offline check was skipped; "works end-to-end" is never claimed from this sandbox — each milestone's LIVE-E2E residual is stated, not hidden.

## Out of Scope

- The reel, audio/TTS, karaoke captions, Story Detail, trust/coverage layer, Q&A/RAG, in-news voice mode, auth, and the Capacitor/iOS shell (all shipped M1–M5; unchanged).
- **Algorithmic catalog discovery/expansion** (co-follow graphs, bio-embedding similarity, research-agent crawl). Launch is editorial-seed only; expansion is later.
- **Auto-generation of clusters** (co-follow/embeddings). Clusters are hand-authored at seed.
- **Podcast and personality *ingestion*** (RSS+Whisper, personality hunt). Only personality *selection* in onboarding is in scope; their content ingestion stays deferred.
- **New design system / tokens.** The onboarding source/cluster UI reuses existing tokens (`reference/design-language.md`); this revamp does not change the visual language, so that doc is untouched.
- **The broader shared-pool rework beyond E1** (online-clusterer tuning M3b/M3c, reel formats, MMR diversity, τ-threshold tuning) — tracked in `plans/shared-pool-rework-master-plan.md`; this revamp consumes its clustering + E1 contract, it does not re-plan it.
- **Live end-to-end execution** (real GDELT + real DB). Not runnable in this sandbox; it is the explicit LIVE-E2E residual per milestone.

## Further Notes

- **Master-plan bridge:** the planner reads THIS file. `plans/master-plan.md` (2026-05-28) is unrelated and must not be touched. `plans/shared-pool-rework-master-plan.md` is a *related, active* plan — its E1/clustering contract is a dependency, not a conflict to resolve.
- **Conflict surfaced (Rule 7):** WS4's importance + themes overlap the shared-pool rework. Resolution baked into Decision #5 and M3 — implement E1, don't fork. If a future planner finds E1 already fully implements authority+normalization by the time M3 starts, M3 collapses to "raise β + verify," and that's a win, not a gap.
- **Verified code facts (so the planner doesn't re-derive):** `gdelt_bigquery.py` GKG SELECT lacks `V2Themes` (selects `V2Persons/V2Organizations/V2Locations` only); `gdelt_doc.py` is keyless, ≤1 req/5s, no theme param, no `domainis:` filter built today; `produce_gate` importance is `min(1, outlet_count/12)`; source items already gate-exempt + slotted via `feed_assembly._fill_source_slots`; clustering module + `0018_story_clusters.sql` already exist; **no cluster abstraction exists** (net-new); `content_sources`/`user_content_sources`/`personalities` exist (migration `0009_content_sources.sql`).
- **Targets to honor in M6:** ~30–40 YouTube channels, ~40–50 X accounts, ~4 personalities per user; opt-out (deselect pre-selected clusters) not opt-in.
- **Open items flagged (non-blocking):** exact source-tier authority weights, the curated ~10–15 trusted domains per category, exact β value, and the over-budget source-spill rule are tuning decisions to pin during `/plan-phases`/`/run-phase`, not gates on planning.
