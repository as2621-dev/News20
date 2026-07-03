# Residual review findings — slice #3 (niche BigQuery ingestion + persona seed)

Commit: `feat(ingestion): batched BigQuery niche pass + persona seed (slice #3)` (`83ffe4e`),
review-panel fixes in `fix(ingestion): review-panel fixes for slice #3`.
Findings the multi-agent review panel raised that were DEFERRED (advisory / human-call /
cross-slice) or REJECTED, with the reasoning. Applied fixes are in the fix commit and not
re-listed here. The security lens found no defects.

Fixed in the follow-up commit (for reference): the batched pass now sizes `@max_rows` to
`used_interests × per_interest_limit` so late-ordered interests are never starved by the
global `LIMIT`; `_get_or_create_user` in the seed paginates `list_users`; the seed writes a
default `user_interest_traits` row (convergence); the total-batch-failure log escalated to
ERROR; the misleading "DOC-backed backbone" comments were corrected.

## 1. No independent DOC story backbone on a BigQuery outage (med, correctness lens) — DEFERRED (architectural / cross-slice)

The daily path's ONLY story source is `ingest_fn` → `ingest_active_interests(niche_adapter=BigQuery)`.
The DOC `census_adapter` passed as `gdelt_adapter` is consumed ONLY at render time for the
coverage/breaking-signal census (`orchestrator._run_detail_stages`), NOT as a story source.
`ingest_trusted_outlets` exists but is never called in the daily path. So a total BigQuery
outage yields an empty pool → the run completes with zero new niche stories. The slice's
comments originally claimed a "DOC-backed backbone" survives such a failure; there is none.

**What the fix commit did:** corrected the comments to state the truth and escalated the
total-batch-failure log to ERROR (Rule 12 — a total-ingestion outage is now loud, not a
warning that reads as normal).

**Why the rest is deferred, not fixed:** wiring an actual DOC-backed story backbone (or a
DOC re-run of `ingest_active_interests` on BigQuery failure), or threading a `degraded`
flag into `pipeline_daily_run_completed`, is an architectural change beyond this slice's
"wire the batched pass" scope. The empty-pool-on-total-source-outage shape is pre-existing
(the DOC fan-out had the same failure mode). Best owned by slice #7 (assembly ladder) or a
dedicated ingestion-resilience slice.

**Follow-up:** file a `slice` issue for BigQuery-outage story resilience if a real outage
ever ships a zero-story feed.

## 2. Backbone regression guard + failure test don't exercise the daily-path wiring (low, correctness lens / Rule 9) — DEFERRED (test depth)

`TestBackboneRegressionGuard` characterizes `ingest_trusted_outlets` in isolation (proves
this slice didn't change that function) and `test_bigquery_failure_...` drives one adapter
through `ingest_active_interests`; neither asserts the `pipeline_routes` niche/census
separation (that the census adapter is a distinct DOC instance that still runs when the
niche adapter fails).

**Why deferred:** the adapter-seam tests DO cover the load-bearing behavior (one batched
pass, node+depth tags, in-SQL cap, zero-match, failure-fail-safe). A pipeline_routes-level
wiring test is a depth improvement, not a correctness gap. Add it when `pipeline_routes`
gains a second failure path worth pinning.

## 3. `_get_or_create_user` is a verbatim copy across two scripts (med, simplicity lens) — DEFERRED (cross-file refactor)

The seed's `_get_or_create_user` mirrors `scripts.run_live_batch._get_or_create_user`
byte-for-byte. The fix commit fixed the pagination-idempotency bug in the SEED copy only.
`run_live_batch.py`'s twin still has the page-1-only scan bug.

**Why deferred:** extracting a shared `scripts/_seed_utils.py` (and fixing the run_live_batch
twin) touches a file outside this slice's story that a concurrent session may be editing
(the same cross-slice caution as slice-2 residual #3). Low value vs. regression surface
mid-slice.

**Follow-up:** fix `run_live_batch._get_or_create_user` (same pagination fix) and extract
the shared helper as a standalone refactor slice if a third caller appears.

## 4. `INGEST_SOURCE` knob exists in the manual script but not the worker (low, simplicity lens) — DEFERRED (human-call)

`scripts/run_live_batch.py` keeps an `INGEST_SOURCE=doc` override to force the niche pass
back onto the DOC adapter; the production worker (`pipeline_routes._run_daily`) hardcodes
niche→BigQuery with no equivalent escape hatch. The two daily-batch entry points diverge in
configurability.

**Why deferred:** whether the worker SHOULD have a DOC escape hatch is a product/ops call
(it doubles as the "survive a BigQuery outage" question in #1). Left for the operator to
decide; do not silently add or remove the knob mid-slice.

## 5. Regex `JOIN UNNEST(@interest_terms) ON REGEXP_CONTAINS(...)` compute at 100× niches (med, performance lens) — DEFERRED (forward-looking scaling)

The batched SQL matches each row against a per-row struct-field regex, so RE2 cannot
precompile the pattern — cost is O(base_rows × term_predicates). Fine today (~273K
articles × ~47 predicates); at thousands of predicates the query slot/wall time (not bytes,
so invisible in the $ estimate) could hit "resources exceeded".

**Why deferred:** this is the adapter's SQL design (pre-existing, unmodified by this slice)
and only bites well beyond current scale. The concrete redesign (exact-token `IN UNNEST`
matching, reserving regex only where word-boundary semantics are needed, or sharding the
term array) is a scaling slice, not a review-panel fix.

**Follow-up:** file a scaling slice before the active-interest set crosses a few hundred.

## 6. Rejection/validation backstop in the seed over-built for fixed constants (low, simplicity lens) — REJECTED (keep)

The seed runs `micro_interest_rejection_reason` + a `rejected` accumulator over the
author-written `PERSONA_SPECS` literals, which a test already asserts are all valid.

**Why rejected, not removed:** the backstop encodes "a privileged mint never trusts its
input" — the project's fail-loud preference (CLAUDE.md Rule 12). It is cheap and makes the
seed safe to extend with new personas without a silent bad-slug mint. Removing it to save a
few lines trades a durable safety property for nothing.

## 7. Dead `_DOMAIN_FILTER_SQL` module constant (informational, simplicity lens) — DEFERRED (pre-existing)

`agents/ingestion/adapters/gdelt_bigquery.py::_DOMAIN_FILTER_SQL` is unreferenced
(`_batch_sql_with_domains` builds the predicate via `build_domain_filter_sql()` instead).

**Why deferred:** it predates this slice (the file was not modified by `83ffe4e`) and is a
trivial one-line removal best swept in a general adapter cleanup, not this slice's fix
commit.
