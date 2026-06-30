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
- [x] 1: Pure theme→category whitelist module + tests — COMPLETED (16 passed; uncommitted)
- [x] 2: V2Themes in GKG SELECT + parse onto candidate — COMPLETED (29 passed; uncommitted)
- [ ] 3: Wire theme-derived category into ingestion-time tagging — FAILED / ESCALATED (Open Q1 gate tripped)
- [ ] 4: End-to-end fixture proof + LIVE-E2E residual doc — BLOCKED (depends on SP3)

## STATUS: PHASE FAILED — ESCALATION (no commit)

SP3's conditional-escalation gate (Open Question 1) is tripped: the recommended
"depth-0 tag on the category-ROOT interest" mechanism needs a category-root
interest id per root, but **5 of the 8 roots (ai, geopolitics, environment,
politics, arts) do not exist as interest rows** in the taxonomy — only
business/tech/sport do. `assign_category` drops orphan tags (interest_id not in
interest_nodes) -> DEFAULT, so a synthetic root tag would silently mis-categorize
5 of 8 categories. Making it work requires a schema/seed change (mint 8 root
interests + re-parent picker leaves) which the phase Out-of-Scope forbids — that's
the escalation. Full analysis: .agents/execution-reports/phase-fsr-m2-theme-category-sub-3.md

Per run-phase Rule 12, a phase with a failed sub-phase is NOT committed. SP1+SP2
remain correct and green in the working tree, uncommitted, awaiting the decision.
