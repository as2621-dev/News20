# Execution report — phase-fsr-m2-theme-category, Sub-phase 2

**Status:** SUCCESS
**Branch:** `claude/feed-source-revamp-plan-388edf` (not switched, not committed)

## What was implemented
Carried GDELT GKG `V2Themes` from the BigQuery GKG SELECT through to a new
`CandidateStory.candidate_themes` field, parsed offset-stripped/deduped/verbatim-case.
No behavior change to matching/ranking — themes are carried, not yet used for category.

1. **`CandidateStory.candidate_themes: list[str]`** — additive field,
   `Field(default_factory=list, ...)`, documented in the class docstring Attributes
   list. No existing field reordered/renamed.
2. **`_BATCH_SQL` selects `V2Themes`** through `raw`→`base`→`matched`→`ranked`→final
   projection. Output column named **`v2_themes`** (lowercase, consistent with the
   other lowercased projection columns `url`/`outlet`). `_rows_to_candidates` reads
   the same `v2_themes` key.
3. **`_parse_v2_themes` helper + wiring** in `_rows_to_candidates` (applies to both
   the stamped and unstamped path — it is one `CandidateStory(...)` call).

## Files modified (absolute)
- `/home/user/News20/agents/ingestion/models.py`
- `/home/user/News20/agents/ingestion/adapters/gdelt_bigquery.py`
- `/home/user/News20/tests/agents/ingestion/conftest.py` (additive `v2_themes` kwarg, default None, on `make_bq_row`)
- `/home/user/News20/tests/agents/ingestion/test_gdelt_bigquery_adapter.py`

## EXACT `_BATCH_SQL` regions changed (M4 hand-off)
M4 will add a `domainis:`/`SourceCommonName` domain predicate to the **`raw` SELECT**
and must NOT clobber the V2Themes addition. Changed regions:

- **`raw` SELECT** — the entity-columns line became:
  `V2Persons, V2Organizations, V2Locations, V2Themes`
  (was `V2Persons, V2Organizations, V2Locations`). This is the line immediately
  after the `REGEXP_EXTRACT(... PAGE_TITLE ...) AS title,` line, and immediately
  before `FROM \`gdelt-bq.gdeltv2.gkg_partitioned\``. **M4: append your
  domain/SourceCommonName column or predicate WITHOUT removing `V2Themes` from this
  line.** SourceCommonName is already aliased as `outlet` in this same SELECT.
- **`base` SELECT** — added `V2Themes AS v2_themes` to the passthrough column list.
- **`matched` SELECT + GROUP BY** — added `b.v2_themes` to both (GROUP BY required
  since `matched` aggregates).
- **`ranked`** — unchanged (`SELECT *`, flows automatically).
- **final projection SELECT** — added `v2_themes` to the column list.

## Case convention pinned
**VERBATIM / UPPERCASE** — theme codes are kept exactly as GDELT emits them (uppercase).
Commented in `_parse_v2_themes` docstring. Coordinated with SP1: SP1's whitelist keys
are UPPERCASE GDELT codes, so lowercasing here would silently break the lookup.

## Output theme column name
`v2_themes` (lowercase) in the SQL projection; parsed into `candidate_themes` on the model.

## Self-review findings + fixes
- **Finding (weak test):** initial `test_final_projection_includes_theme_column`
  split on `"FROM ranked"` and asserted `v2_themes in` everything-before — but
  `v2_themes` also appears in `base`/`matched` (which precede `FROM ranked`), so the
  test would pass even if dropped from the projection. **Fixed:** isolate the LAST
  `SELECT` block via `rsplit("SELECT", 1)[1].split("FROM ranked", 1)[0]` so the
  assert targets only the final projection column list.

## Validation
`pytest tests/agents/ingestion/test_gdelt_bigquery_adapter.py -q` → **29 passed in 0.90s**
Broader sweep `pytest tests/agents/ingestion -q` → **161 passed** (additive field
broke no existing consumer).

## Definition of done (per case)
- (a) static `_BATCH_SQL` includes `V2Themes` in `raw` SELECT + theme column in final
  projection — **PASS** (`TestBatchSqlIncludesThemes`, 2 tests).
- (b) `v2_themes="WB_2670_JOBS,123;ECON_STOCKMARKET,456;WB_2670_JOBS,789"` →
  `candidate_themes == ["WB_2670_JOBS", "ECON_STOCKMARKET"]` — **PASS**.
- (c) `v2_themes=None` and `v2_themes=""` → `candidate_themes == []` — **PASS** (2 tests).
- (d) all existing adapter tests still pass — **PASS** (24 prior + 5 new = 29).

## Divergences
None. Chose the conftest `v2_themes` kwarg over inline rows (multiple `make_bq_row`
callers; additive + default None keeps existing callers working). Acceptable per the
prompt's "either is fine" note.

## Concerns
- LIVE-E2E (deferred, per phase file): a real BigQuery GKG pull returning populated
  `V2Themes` with live formatting is NOT verifiable offline (egress 403 / no creds).
- No theme-code case normalization is applied; SP1 MUST key its whitelist on
  uppercase GDELT codes (coordinated above). If SP1 lowercases, the loop breaks — but
  that is SP1/SP4's surface, not SP2's.
