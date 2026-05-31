# Phase 2c: Story Detail analytics — second-analytic tab + GDELT coverage (data pipeline)

**Milestone:** M2 — Detail View + trust + interrogation
**Status:** Not started
**Estimated effort:** L

## Goal
Produce, store, and serve the **richer Story Detail payload** the design now calls for: a hero key-figure, a **persistent Story Timeline**, a **segment-skinned "second analytic" tab** (Market Impact / Ripple / Impact / Stakes / Why It Matters), an **adaptive Coverage tab** (partisan L·C·R + blindspot *or* reach "covered by N outlets") fed by **GDELT**, and the **5 at-a-glance bullet points** above "Read the full article". This phase owns the **data path only** — schema, ingestion/enrichment pipeline, persistence, and the typed contract. **The UI is out of scope** (the owner supplies HTML/CSS against the extended `StoryDetail` contract).

> **⚠ Prerequisite (Rule 12 — read before running). Verified against the repo 2026-05-31 (audit):**
> - **M1 pipeline (`phase-1d`): SP1–SP3 committed (`fac5743`/`a142106`/`1c6bb1c`); SP4 (feed assembly → `daily_feeds`) NOT started.** `agents/pipeline/orchestrator.py` → `persist_digest` (`agents/pipeline/persist.py`) is live (a `FIXTURE-SP3-…` row was written to Supabase) and produces `stories`/`digests`/`caption_sentences`/`detail_chunks`/`story_trust`/`story_sources`/`story_interests`/`suggested_questions`. **Confirmed gaps this phase closes:** it does **not** write `story_timeline` rows, does **not** set `stories.story_key_figure_*` (`build_story_row` omits both — `persist_helpers.py`), and has no second-analytic/key-points. (The M2 UI's timeline + key figure today come from the **1b seed**, not the pipeline — so pipeline-produced stories are currently missing them. Audit discrepancy #3.)
> - **GDELT is already the live ingestion source** — `agents/ingestion/adapters/gdelt_doc.py` (`GdeltDocAdapter`, keyless DOC 2.0, validated live 2026-05-31). SP2 **reuses it**, it does not build a new GDELT client.
> - **The `outlets` bias table is dead:** defined in `0001` and FK'd from `stories.story_primary_outlet_id` / `story_sources.source_outlet_id`, but **never seeded and never populated** (all writes use outlet *name strings*; the FKs are null; coverage leans today ride on `covering_outlets`, not this table). SP1 brings it to life (see OQ#2).
> - M2 detail UI (`phase-2`, shipped — commit `0e76d50`): `src/types/detail.ts`, `src/lib/detail/fetchStoryDetail.ts`, `src/components/detail/*`. This phase **extends** those files additively; it does not rewrite them.
> - Migrations `0001`–`0003` applied; **`0004` does not exist yet**. Apply it via the **IPv4 session pooler** (`aws-1-us-east-1`, `supabase db push --db-url …`) — the direct host is IPv6-only (see memory `news20-supabase-ddl-connection`).

## Design decisions baked in (from the 2026-05-31 detail-analytics discussion)
1. **3 fixed tab slots, middle one skinned per segment.** `[ Story Timeline | «second analytic» | Coverage ]`. Timeline is universal; the second analytic's *kind* is chosen **deterministically by `story_segment_slug`** (Rule 5 — code, not the LLM); Coverage is universal but adaptive.
2. **Second-analytic kind → segment map:**
   | `segment_slug` | `analytic_kind` | tab label |
   |---|---|---|
   | `geopolitics` | `market_impact` | MARKET IMPACT |
   | `markets` | `ripple` | RIPPLE |
   | `tech` | `impact` | IMPACT |
   | `sport` | `stakes` | STAKES |
   | `wildcard` | `why_it_matters` | WHY IT MATTERS |
3. **Coverage is adaptive, not dropped.** `coverage_mode = 'partisan'` (L·C·R + blindspot) for `geopolitics` (+ any story the ingest flags as contested); `'reach'` (covered-by-N + momentum + who-broke-it + notable outlets) elsewhere. Mode is chosen deterministically; default `partisan` for `geopolitics`, else `reach`.
4. **Coverage census comes from GDELT**, not the few clustered ingest articles. GDELT DOC 2.0 API (`mode=ArtList`, free, no key) → distinct domains → join the static `outlets` bias table (Decision #6) → counts. `story_sources` (what we *scripted* from) stays fed by `covering_outlets`; GDELT feeds the *census*.
5. **Market numbers must be grounded or omitted (Rule 12).** A fabricated "Brent +4%" is worse than no number. The LLM drafts the narrative; numeric `analytic_row_value`s must be supported by the source text (or rendered direction-only, e.g. "↑") and gated by the existing verification pattern. `story_analytics.analytic_is_grounded` records the verdict.
6. **5 bullets ≠ body.** `detail_key_points` (the at-a-glance breakdown, always shown) is distinct from `detail_chunks` (the long-form body behind "Read the full article").

## Sub-phases

### Sub-phase 1: Schema migration 0004 + Pydantic models + TS contract
- **Files touched:** `supabase/migrations/0004_detail_analytics.sql` (new), `supabase/seed/outlets.sql` (new — the static bias table, see OQ#2), `agents/pipeline/models.py` (extend), `src/types/detail.ts` (extend), `reference/supabase-schema.md` (mark these entities live)
- **What ships:**
  - Migration `0004`: enums `coverage_mode` (`partisan`|`reach`) and `analytic_kind` (`market_impact`|`ripple`|`impact`|`stakes`|`why_it_matters`); table `story_analytics` (1:1 per story — `analytic_kind`, `analytic_tab_label`, `analytic_headline`, `analytic_summary_text`, `analytic_rows jsonb`, `analytic_is_grounded`); table `detail_key_points` (`key_point_index` 0-based, `key_point_text`, unique per story); `ALTER story_trust ADD coverage_mode`, `coverage_momentum`, `coverage_originating_outlet_name`, `coverage_notable_outlet_names text[]`; `ALTER outlets ADD outlet_domain text` (so GDELT domains resolve to a lean); public-read RLS on the two new tables.
  - **Seed the static bias table** (`supabase/seed/outlets.sql`): the `outlets` table is currently empty (OQ#2), so author `(outlet_name, outlet_bias_lean, outlet_domain)` rows for the ~100–200 outlets that appear in the feed (AllSides/Ad Fontes leans). Idempotent `on conflict (outlet_domain) do nothing`. **Without this, SP2's domain→lean join returns all-unrated and coverage counts are zero.**
  - Pydantic models in `agents/pipeline/models.py`: `AnalyticRow` (`analytic_row_label/value/direction/note`), `SecondAnalytic`, `CoverageReport` (partisan counts **or** reach fields + `coverage_mode`), `DetailTimelineEvent`, `KeyFigure`, `DetailKeyPoint` — validate every `analytic_rows[]` element before insert (schema §0 — never raw dicts).
  - TS additions to `src/types/detail.ts`: `AnalyticKind`, `CoverageMode`, `AnalyticRow`, `SecondAnalytic`, reach fields on `TrustSummary` (`coverage_mode`, `coverage_momentum`, `coverage_originating_outlet`, `coverage_notable_outlets`), `detail_key_points: DetailKeyPoint[]`, `second_analytic: SecondAnalytic | null` on `StoryDetail`.
- **What ships (observable):** migration applies cleanly to the live project; `\d story_analytics`, `\d detail_key_points`, and `\d+ story_trust` show the new columns/enums; `tsc`/Biome pass; the Pydantic models import and round-trip a sample payload.
- **Definition of done:** `supabase db push --db-url <pooler>` applies 0004 with no error; a SQL smoke (`INSERT` a sample `story_analytics` row + 5 `detail_key_points` for a seeded story, then `SELECT`) round-trips; `SELECT outlet_bias_lean FROM outlets WHERE outlet_domain = 'cnn.com'` returns a lean (bias table actually seeded — OQ#2); `npx tsc --noEmit` passes with the extended `StoryDetail`; a Pydantic unit test rejects a malformed `analytic_rows` element (Rule 9 — fails if validation is dropped).
- **Dependencies:** none. **Gate for SP2–SP4.**

### Sub-phase 2: GDELT coverage stage (deterministic)
- **Files touched:** `agents/pipeline/stages/coverage_gdelt.py` (new), `reference/integrations.md` (reconcile — GDELT is already the live source, not NewsAPI)
- **What ships:** `build_coverage_report(story, outlets_lookup, adapter) -> CoverageReport`. **Reuses the existing `GdeltDocAdapter`** (`agents/ingestion/adapters/gdelt_doc.py`) — calls `adapter.search(coverage_query, since_utc)` and reads `candidate_outlet_domain` off each returned `CandidateStory` (domain is already lowercased there). **No new GDELT client** (Rule 2/8). The adapter already enforces GDELT's real constraints (validated 2026-05-31): **≤1 request / 5s** via a shared throttle lock, `sort=hybridrel`, `maxrecords ≤ 250`, `timespan` 1–3d. Coverage flow: distinct domains → normalize (collapse affiliate subdomains) → resolve each against `outlets.outlet_domain` → bias lean. Reuses the existing `derive_coverage_counts` / `derive_blindspot_lean` helpers on the rated set. Picks `coverage_mode` by segment (deterministic). Partisan mode: L/C/R from rated outlets + blindspot (>70% one side). Reach mode: `coverage_outlet_count` (distinct outlets) + `coverage_momentum` (from `seendate` spread) + `coverage_originating_outlet_name` (earliest `seendate`) + up to 5 `coverage_notable_outlet_names`. GDELT failure is **non-fatal** — the adapter raises `AdapterFetchError`; catch it and fall back to the current `covering_outlets`-derived counts, logging a `fix_suggestion`.
- **What ships (observable):** one function turns a story into a populated, mode-correct `CoverageReport` from a live GDELT call (mocked in tests).
- **Definition of done:** a unit test mocks the GDELT HTTP client with a fixture article list spanning 3 leans + foreign/affiliate noise, and asserts (a) affiliate/foreign noise is filtered, (b) partisan counts match the rated domains, (c) the >70% blindspot branch fires correctly, (d) a `markets`/`sport` story yields `coverage_mode='reach'` with momentum + originating outlet, (e) GDELT error → graceful `covering_outlets` fallback (Rule 9/12 — fails if a fabricated count, wrong mode, or silent-empty-on-error slips through).
- **Dependencies:** SP1 (models + `outlets.outlet_domain`).

### Sub-phase 3: LLM detail-enrichment stage (grounded)
- **Files touched:** `agents/pipeline/stages/detail_enrichment.py` (new), `agents/pipeline/prompts.py` (append `DETAIL_ENRICHMENT_PROMPT` + per-`analytic_kind` instructions)
- **What ships:** `run_detail_enrichment(story, script, llm_client) -> DetailEnrichment` producing, **constrained to the single source** (Decision #4) and verification-gated (reuse the `run_single_source_verification` pattern): the hero `KeyFigure`, ordered `DetailTimelineEvent`s ("HOW IT DEVELOPED"), the `SecondAnalytic` for the story's segment-chosen `analytic_kind`, and exactly **5** `DetailKeyPoint`s. Numeric `analytic_row_value`s are verified against the source body; unsupported numbers are dropped to direction-only and `analytic_is_grounded=false` is set (never published as a fact).
- **What ships (observable):** one call returns a fully-populated, grounded `DetailEnrichment` for a story.
- **Definition of done:** a unit test mocks the LLM to return (a) a clean grounded payload → asserts 5 key points, ≥1 timeline event in order, a `SecondAnalytic` whose `analytic_kind` matches the segment map, and (b) a payload with a source-unsupported "+4%" → asserts that value is dropped/de-numbered and `analytic_is_grounded=false` (Rule 9/12 — fails if an ungrounded number publishes or the wrong analytic_kind is emitted). Segment→kind selection is a pure function with its own happy/edge tests.
- **Dependencies:** SP1 (models).

### Sub-phase 4: Persist + orchestrator wiring + fetch + seed
- **Files touched:** `agents/pipeline/persist.py` + `agents/pipeline/persist_helpers.py` (extend), `agents/pipeline/orchestrator.py` (wire the two new stages), `src/lib/detail/fetchStoryDetail.ts` (read new tables/columns), `supabase/seed/*` (extend the s1–s5 fixtures with second-analytic + key points + coverage_mode)
- **What ships:** `orchestrate_story` calls `build_coverage_report` (SP2) + `run_detail_enrichment` (SP3) after verification; `persist_digest` writes `stories.story_key_figure_*`, `story_timeline` rows, the `story_analytics` row, `detail_key_points` rows, and the new `story_trust` columns (all INSERT-only, parents-first, per the existing ordering). `fetchStoryDetail` additively reads `story_analytics`, `detail_key_points` (ordered), and the new `story_trust` columns into the extended `StoryDetail`. The seed gains realistic second-analytic + key-point + coverage-mode fixtures for s1–s5 so the existing UI renders against real shapes.
- **What ships (observable):** running the pipeline on one real story persists a complete detail payload; `fetchStoryDetail('s1')` returns `second_analytic`, 5 `detail_key_points`, a timeline, a key figure, and a mode-correct coverage block.
- **Definition of done:** the live per-story e2e (the `FIXTURE-SP3-` path) persists rows in all new tables with no FK/constraint error; a `persist_digest` unit test (mocked supabase) asserts each new table receives a correctly-shaped, ordered payload; a `fetchStoryDetail` test (mocked supabase) asserts the new columns map to the right fields **and key points/timeline come back in index order** (Rule 9). Biome + Ruff pass.
- **Dependencies:** SP1 + SP2 + SP3 (integration sub-phase — runs last).

## Phase-level definition of done
Running the daily pipeline on a real ingested story persists — for that story — a hero key figure, an ordered Story Timeline, a segment-correct `story_analytics` row (grounded numbers or none), 5 `detail_key_points`, and a GDELT-fed `story_trust` in the correct `coverage_mode`; and `fetchStoryDetail(story_id)` returns that complete, validated `StoryDetail`. No UI work (owner-supplied). `/run-phase` validates: the e2e persist succeeds and `fetchStoryDetail` returns every new field for both a `partisan` (geopolitics) and a `reach` (sport/markets) seeded story.

## Out of scope
- **All UI/rendering** — the owner supplies HTML/CSS against the `StoryDetail` contract.
- Q&A / voice grounding (`phase-2b` / M3) — untouched.
- Licensing/procurement of a commercial bias dataset — this phase uses the existing static `outlets` table (Decision #6); broadening it is a separate data task.
- A real-time markets quote feed — numbers are source-grounded only; a live quote API is a later enhancement.

## Open questions
1. **GDELT story-match precision + throttle latency.** Keyword/entity queries drift. SP2 v1 queries on title + extracted entities; if recall/precision is poor we add story clustering. **Latency is real:** `GdeltDocAdapter` serializes all calls through a shared **≤1-req/5s** throttle lock — so a per-story coverage call competes with ingestion's per-interest searches for the same 5s budget. For a daily batch of N stories that's ≥5N seconds of GDELT time; decide whether coverage runs in the same throttled adapter instance (safe, slow) or a separate rate-budgeted pass.
2. **The `outlets` bias table is empty — it must be *seeded*, not backfilled.** Verified: the repo has **no `outlets` seed** (only `interests.sql` + `seedM0Digests.ts`), so the static AllSides/Ad Fontes table Decision #6 assumes has zero rows. Without it, every GDELT domain resolves to "unrated" and coverage counts come back zero. SP1 must therefore **seed `(outlet_name, outlet_bias_lean, outlet_domain)`** for the ~100–200 outlets that actually appear in the feed (e.g. `(CNN, left, cnn.com)`), not merely add the column. (`outlet_homepage_url` is also unpopulated, so domains can't be derived — they're authored in the seed.)
3. **Contested-story detection for `coverage_mode`.** v1 keys mode off segment only (`geopolitics`→partisan). A business/health *policy* story is also partisan. Defer a content-based "is this contested?" classifier to a later iteration; flag the limitation in the log.

## Self-critique

**Product lens:** PASS. Traces directly to the M2 "True when" (*read* + *see who's covering*) and the owner's detail-analytics spec. The second-analytic tab is the new differentiator; Coverage stays meaningful everywhere via the reach/partisan split (no dead "L/R on a football score"). Scope is held to the data path — UI is explicitly the owner's. No speculative tables: `story_analytics` is 1:1, rows are JSONB (mirrors the `caption_sentences.word_tokens` precedent), `detail_key_points` is a thin ordered child.

**Engineering lens:** PASS. Reuses `derive_coverage_counts`/`derive_blindspot_lean` and the verification pattern rather than re-implementing (Rule 2). Each sub-phase touches a **disjoint file set** — SP2 and SP3 each create their own stage file; SP4 alone edits persist/orchestrator/fetch/seed; SP1 alone edits the migration/models/contract — so the only ordering is the honest SP1-gate + SP4-integration (no shared-file write races). Every DoD is fresh-context-checkable (migration apply + round-trip, mocked-client mapping/ordering tests, segment→kind pure-function tests) rather than "works end-to-end".

**Risk lens:** PASS (mitigated). **Hallucinated market numbers** are the headline risk — mitigated by SP3's grounding gate + `analytic_is_grounded` + direction-only fallback (Rule 12). **GDELT availability/precision** — mitigated by non-fatal fallback to the current `covering_outlets` counts and a precision open-question. **Reversibility:** migration 0004 is additive (new enums/tables/columns, no drops); pipeline changes are new stages + additive persists. **DDL connection** gotcha (IPv6) called out in the prerequisite (apply via the IPv4 pooler).

**Irreversible sub-phases:** SP1 applies DDL to the live project — additive only (no `DROP`), but a schema change. Apply via the IPv4 session pooler; verify with `\d` before proceeding to SP4.
