# Residual review findings — Issue #23 (shared X cluster sweep + theme extraction)

Commit: shared once-daily X cluster sweep + theme extraction.
Panel: correctness, data-integrity, simplicity+architecture (3 reviewers).

## Applied (fixed before/at commit)
- **Stringy `is_retweet` coercion** (correctness): `bool("false")` is truthy in Python; added `_coerce_bool` so a stringified `"false"` flag no longer drops a genuine original post. Regression test: `test_stringified_is_retweet_false_is_not_dropped`.
- **Uncaught rehydration on a partial stored theme** (correctness): `_row_to_result` now defaults missing `supporting_handles` / `supporting_tweet_urls` to `[]` instead of raising `ValidationError` on the cached-read path (which is outside the DB try/except). Regression test: `test_cached_read_tolerates_partial_stored_theme`.
- **Mislabeled failure logs + fragile private import** (architecture): cluster_sweep no longer imports x_account's private `_parse_xai_response` / `_XAI_*` (which logged `adapter="x_account"`). Extracted a shared `agents/ingestion/adapters/xai_client.py` (`post_xai`, `parse_xai_response`, both taking a `source_name`), so sweep failures now log under `x_cluster_sweep`. This also collapses the duplicated transport in cluster_sweep.
- **Redundant index** (data-integrity note): dropped the explicit `idx_x_cluster_sweeps_cluster_date` — the `UNIQUE (cluster_id, sweep_date)` constraint already creates that index.
- **`_RealSeams` built twice** (simplicity note): now constructed once per `sweep_cluster` call.

## Deferred (human call — out of this slice's scope)
- **`x_account.py` should adopt the new `xai_client`.** The adapter still carries its own copy of the xAI transport (`_default_xai_discoverer`), parser (`_parse_xai_response`), and `_parse_post_datetime`. The slice dispatcher explicitly instructed *do NOT rewrite the adapter*, so it was left untouched; the new `xai_client` is the shared home it can migrate to later. Until then the request/error contract lives in two places and could drift. Low risk (both currently identical). Suggested follow-up: a small refactor slice repointing `x_account` at `xai_client` and deleting its private copies, guarded by the existing `tests/agents/ingestion/adapters/test_x_account.py`.
- **`_parse_dt` in cluster_sweep duplicates x_account's `_parse_post_datetime`** (~6 lines). Trivial; fold into a shared date util alongside the `x_account` refactor above rather than as its own change.

## Data-integrity: no defects found
The once-per-day / one-row guarantee is enforced by the `UNIQUE (cluster_id, sweep_date)` constraint + `on_conflict` upsert (the existence check is a cost optimization, not the correctness mechanism). A lost existence-check race costs one redundant x_search call, never a duplicate row. RLS is public-read + service-role-write only.
