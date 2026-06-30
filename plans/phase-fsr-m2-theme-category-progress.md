# Progress: phase-fsr-m2-theme-category

**Phase file:** plans/phase-fsr-m2-theme-category.md
**Started:** 2026-06-30
**Branch:** claude/feed-source-revamp-plan-388edf

## Test-env note (M1 wrinkle resolved)
pytest runs from the uv tool env `/root/.local/share/uv/tools/pytest/bin/python`.
Injected missing deps into it (offline, no logic change):
`structlog pydantic pytest-asyncio pydantic-settings httpx pydub trafilatura google-cloud-bigquery`.
Baseline (pre-phase) GREEN: 81 passed across test_categories, test_ranking,
test_gdelt_bigquery_adapter, test_interest_keyed_pipeline.

## Sub-phase progress
- [x] 1: Pure theme→category whitelist module + tests — COMPLETED (committed b25c568)
- [x] 2: V2Themes in GKG SELECT + parse onto candidate — COMPLETED (committed b25c568)
- [x] 3: Wire theme-derived category into ingestion-time tagging — COMPLETED
- [x] 4: End-to-end fixture proof + LIVE-E2E residual doc — COMPLETED

## STATUS: PHASE SHIPPED

The SP3 escalation (Open Q1: only 3 of 8 root interest nodes existed) was resolved
by phase M2R — `supabase/migrations/0023_root_interest_nodes.sql` (committed b76feab)
mints all 8 depth-0 roots + re-parents picker leaves — and the new accessor
`agents/pipeline/categories.py::root_interest_slug_for_category`. SP3 then wired
the theme-derived tag with NO schema or `assign_category` change.

### SP3/SP4 implementation
- `CanonicalStory.canonical_themes` (models.py) — union of cluster members'
  `candidate_themes`, aggregated in `StoryClusterer._to_canonical` (dedup.py).
- `ingest_active_interests` (interest_keyed_pipeline.py): per story,
  `category_for_themes(canonical_themes)` → `root_interest_slug_for_category` →
  depth-0 `story_interests` tag on the category-ROOT interest (resolved via a
  `root_id_by_slug` index of depth-0 nodes). Keyword ancestor tags are emitted
  shifted +1 (clamped ≤2) so the theme tag is the strict lowest-depth signal
  `assign_category` reads (deterministic theme-win, not a fragile slug tiebreak).
  No resolvable theme root (roots not loaded) → keyword tags keep natural depth
  (degraded-but-categorizable, fail-loud warning) — never drops the story.
- Tests (test_interest_keyed_pipeline.py): SP3 = business theme beats geopolitics
  keyword via downstream `assign_category`; theme tag is strict depth-0 + keyword
  shifted to 1; no-theme → arts + batch completes; unmapped theme → arts (not
  keyword). SP4 = batched GKG-style adapter end-to-end (themes → ingest → tags →
  `assign_category`), happy + no-theme in one run.

### DoD: PASS
Full M2 suite green (uv pytest tool env):
`tests/agents/pipeline/test_theme_category.py tests/agents/ingestion/test_gdelt_bigquery_adapter.py tests/agents/ingestion/test_interest_keyed_pipeline.py`
+ regression `tests/agents/ingestion tests/agents/pipeline/test_ranking.py
tests/agents/pipeline/test_categories.py tests/agents/pipeline/test_fallback_tree.py`
= 280 passed, 0 failed. (Pre-existing ~18 orchestrator/clustering/poster failures
are missing-dep baseline — ffmpeg/PIL/datasketch — not touched by this phase.)

### LIVE-E2E residual (deferred, NOT run — offline sandbox)
A real BigQuery GKG pull confirming live `V2Themes` populate, the parser handles
live formatting, and `assign_category` over a live batch yields sensible categories
on a real day. Cannot run: GDELT egress is 403 + no BigQuery creds. Stands as the
M2 live residual alongside SP2's parser-on-live-data residual.

### Surfaced design trade (Rule 7/12)
The +1 keyword-tag shift drops a keyword-path followed-LEAF's DepthMatch from 1.0
to 0.6 — a deliberate trade (category correctness > leaf-affinity nuance), the
only deterministic theme-win without touching `assign_category`/schema. The M4
trusted-outlet NEWS path carries no keyword tags, so its theme tag is the sole tag
(no shift, no scoring impact); the shift only affects the residual keyword path.
Flagged for M3 (which tunes ranking/importance).
