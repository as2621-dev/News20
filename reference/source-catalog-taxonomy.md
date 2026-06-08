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
