# Source-catalog taxonomy (Phase 5f)

Locks the taxonomy + curation policy for the per-archetype content-source catalog
that feeds the onboarding SourceSwipe decks. The catalog is populated by an LLM
**candidate generator** (`scripts/seed_catalog/generate_candidates.py`) whose
output is verified + upserted by the existing **seeder**
(`scripts/seed_catalog/seed_catalog.py`). This doc is the source of truth for the
decisions; the seeder's `ALLOWED_ARCHETYPES` / `ALLOWED_TOPIC_TAGS` constants are
the runtime mirror.

## The 12 archetypes (interest profiles)

The catalog is keyed to exactly these 12 archetype slugs (see
`supabase/seed/archetypes.sql` and `reference/archetypes.md`). A candidate file is
named `data/{type}.{archetype}.json`; an unknown archetype is skipped (fail loud).

| Slug | Domain |
|---|---|
| `ai-frontier-tech` | AI / ML / frontier deep-tech |
| `markets-macro` | Markets & macroeconomics |
| `startup-operator` | Startups, founders, VC, product |
| `crypto-fintech` | Crypto, blockchain, fintech |
| `geopolitics-world` | International relations & world affairs |
| `us-politics-policy` | US elections, Congress, courts, policy |
| `climate-energy` | Climate science & the energy transition |
| `sports-fan` | Sports (globally inclusive) |
| `arts-culture` | Film, music, literature, visual art |
| `creator-media` | Creator economy & new media |
| `tech-generalist` | Consumer tech & broad tech journalism |
| `balanced-generalist` | Broad general-interest mix |

## Regional / cultural coverage policy (Open Q1)

**Decision: keep the 12 archetypes; achieve regional/cultural breadth via
INTRA-archetype diversity — not by adding region-specific archetypes.**

Rationale: the 12 archetypes are interest profiles, not regions. Splitting each
into regional variants (e.g. `sports-fan-india`, `sports-fan-us`) would multiply
the deck count, fragment the onboarding UX, and force users to pick a region
before an interest. Instead, each archetype's generation prompt explicitly
requests a culturally and geographically diverse set within the domain. Concrete
expectations baked into `scripts/seed_catalog/prompts.py`:

- `sports-fan` → football/soccer, **cricket**, basketball, tennis, F1 — not only
  US leagues (NFL/NBA/MLB).
- `arts-culture` → Hollywood **and Bollywood** and other global cinema (Korean,
  Nigerian, European), plus world music and international literary voices.
- `geopolitics-world` → genuinely global coverage (Asia, Africa, Middle East,
  Latin America, Europe) and a range of analytical perspectives.
- `ai-frontier-tech`, `startup-operator`, `crypto-fintech` → voices from the US,
  Europe, China, India, Southeast Asia, Latin America, Africa — not only Silicon
  Valley.
- `us-politics-policy` → a balanced spread across left / center / right.

This is a generation-prompt policy; the resolvers do not enforce diversity (they
only verify existence). Curators can re-balance a cell by editing its JSON and
re-seeding.

## The 8 topic-tag keys (axis C1)

Every candidate's `topic_tags` array is constrained to these 8 keys (the seeder's
`ALLOWED_TOPIC_TAGS`). **`topic_tags[0]` MUST be one of these 8** and is the
single best-fitting category; array position 0 is the highest-popularity / primary
category. Unknown tags are stripped at generation time; a candidate with no valid
key is dropped.

`ai`, `geopolitics`, `business`, `environment`, `politics`, `tech`, `sport`, `arts`

### Curated finer sub-niches per category

These are curation guidance for diversity within a category — NOT new tag keys
(the on-disk tags stay within the 8). They help a curator sanity-check that a
cell spans its category rather than clustering on one sub-topic.

- **ai** — LLMs/foundation models, ML research, robotics, semiconductors/compute, AI policy & safety, applied/enterprise AI.
- **geopolitics** — great-power competition, regional conflict, diplomacy/trade, defense & security, intelligence analysis.
- **business** — equities & markets, macro/central banks, startups/VC, corporate strategy, personal finance, crypto/fintech.
- **environment** — climate science, renewables & the energy transition, conservation/biodiversity, climate policy, cleantech.
- **politics** — elections, legislatures, courts/law, domestic policy, political analysis & commentary.
- **tech** — consumer gadgets, software/dev, internet & platforms, cybersecurity, science & engineering explainers.
- **sport** — football/soccer, cricket, basketball, tennis, motorsport (F1), athletics & olympics, combat sports.
- **arts** — film/cinema (incl. Bollywood & global), music, literature, visual art & design, cultural criticism.

## Resolved open questions

| # | Question | Decision |
|---|---|---|
| Q1 | Regional archetypes? | **Keep 12 + intra-archetype diversity** (see above). |
| Q2 | X-account avatars + followers | Use **unavatar** for the avatar (hot-link `unavatar.io/twitter/<handle>`); **followers null / approximate** (no live X API in 5f). The X axis upserts the handle as `external_id` with no live resolver, so `subscriber_count` is null. |
| Q3 | Thumbnails storage | **Hot-link** the source-of-truth image URL (YouTube `yt3.ggpht.com` avatar, iTunes artwork, Wikipedia lead image, unavatar for X). No re-hosting / no blob storage in 5f. |
| Q4 | Popularity score | **Rank-based** from candidate-file array position via the seeder's `POPULARITY_TOP=100 / POPULARITY_STEP=2 / POPULARITY_FLOOR=10` (rank 0 → 100, descending). The generator emits best-first order; position is the rank. |
| Q5 | Display cap (archetypes shown) | **Keep all 12** archetype decks in onboarding; no cap. |
| Q6 | Over-generation factor | **Measured in SP1** (channels / `ai-frontier-tech`): 90 proposed → 74 resolved on YouTube = **82.2% resolve rate**, 1 quota unit per `forHandle` call. To clear ≥50 surviving, **over-generate ~75–90 per channels cell** (≥61 needed at 82% to clear 50; 75 default leaves comfortable margin). Default `DEFAULT_CANDIDATE_COUNT = 75`; bump `--count` for any low-resolve cell. |

## Quota math (full SP2 channels run)

`channels.list?forHandle` = **1 unit/call**, free quota **10,000 units/day**.
12 archetypes × ~90 channels = ~1,080 forHandle calls = **~1,080 units** — well
within the daily free quota (≈11% of 10k). Even at 120/cell (~1,440 units) the
full channels axis fits one day with headroom for re-runs. Podcasts (iTunes) and
personalities (Wikipedia) and X (no resolver / unavatar) do **not** consume
YouTube quota.

---

## Clusters — the onboarding bulk-select grouping (feed-source revamp, 2026-06-30)

> Added by the **feed-source revamp** (`plans/prd.md` M1, Decision #6/#7). A **cluster**
> is **net-new** — it is NOT an archetype and NOT a `persona`. Archetypes are a
> recommendation-matching key (interest-vector → catalog); a cluster is a
> **user-facing, named bulk-select grouping of catalog rows within ONE of the 8
> categories** ("Leading AI-lab researchers", "AI founders", "AI journalists").

**Why clusters exist.** Onboarding targets ~30–40 YouTube channels + ~40–50 X
accounts + ~4 personalities per user. Hand-picking ~90 accounts is unacceptable
friction. A cluster lets the user follow a whole named group in one tap, and lets
onboarding **pre-select recommended clusters the user deselects (opt-out)** rather
than opt-in.

**One model for both axes.** A single cluster model serves **both** X and YouTube
(YouTube clusters simply have fewer members per cluster). Net-new tables (migration
**`0022_source_clusters.sql`**): `source_clusters` (one row per named grouping;
`cluster_category` ∈ the 8 `ALLOWED_TOPIC_TAGS`; ordered) + `source_cluster_members`
(cluster → catalog row / personality ref, ordered by `popularity_score`). Public-read
RLS, service-role writes (same tier as `content_sources`).

**Supersedes the 5c archetype/persona SourceSwipe deck (M6a).** The category-keyed
**cluster onboarding** is now the onboarding source-selection step — it **supersedes**
the phase-5c archetype/persona-keyed **SourceSwipe deck** described at the top of this
doc. The 12 archetypes + persona-union catalog generation (above) remain the
**curation/seed** machinery, but the *user-facing source picker* at onboarding is
cluster-driven (filtered by `topic_tags ∩ chosen categories`, ordered by
`popularity_score`, recommended clusters pre-selected for opt-out). See `plans/prd.md`
M6 / Decision #6/#7 and `reference/sources-reuse-map.md`.

**The no-dup rule (load-bearing).** A person who is a followable **personality**
(bundling their handles via `personalities.youtube_channel_ids` / `aliases`) is shown
**once** as a personality card. Their individual `content_sources` YouTube/X rows are
**excluded** from the grid and from any cluster's rendered members when that
personality card is present. The cluster/no-dup resolver owns this; tests must cover:
cluster membership, empty cluster (don't surface), personality-dedup (handles hidden),
a row tagged to multiple categories (appears per category, deduped), and a member in
two clusters of one category (allowed; dedup at selection).

**Authoring is editorial at seed.** Clusters are hand-authored per category for
launch trust ("no randoms"); auto-generation (co-follow graphs, bio-embedding
similarity) is **out of scope** for the revamp (later enhancement). Onboarding quality
== catalog + cluster quality — this is the #1 risk (`plans/prd.md` riskiest assumption).
