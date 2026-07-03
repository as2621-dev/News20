---
title: A global LIMIT over a per-group window silently starves late groups
tags: [bigquery, batched-sql, ingestion, row-number, limit, starvation]
problem_type: performance
symptoms: some interests/niches return zero candidates despite matching rows; the missing
  ones are always the same (sorted last by the grouping key); no error, no warning
root_cause: a fixed global `LIMIT @max_rows` applied AFTER a `ROW_NUMBER() PARTITION BY group`
  window and `ORDER BY group_id` re-caps the whole result, so once
  groups × per_group_limit exceeds the fixed cap, rows for late-ordered groups fall off
date: 2026-07-03
---

When one batched query serves N groups (here: all active micro-interests in ONE GKG pass,
`agents/ingestion/adapters/gdelt_bigquery.py`), a per-group cap enforced in-SQL via
`ROW_NUMBER() OVER (PARTITION BY interest_id ...) AS rn ... WHERE rn <= @per_interest_limit`
looks like it bounds the result — but a trailing global `LIMIT @max_rows` with
`ORDER BY interest_id` re-bounds it. If `max_rows` is a fixed constant (was 5000) below the
batch's legitimate ceiling (`N × per_interest_limit`), the query fills groups in `interest_id`
order until it hits the cap and returns ZERO rows for every group after the cutoff. It scales
invisibly: fine at 9 interests (675 rows), broken past ~66 — exactly the "hundreds of niches"
the batching was built for. Bytes-scanned is unaffected, so it never shows in the $ estimate.

Fix: size the batched-pass cap to the batch, not a constant —
`max(self.max_rows, used_interests * self.per_interest_limit)` — so the per-group window is
the only thing that bounds per-group output. Keep the fixed constant only as a floor / for
single-group recall paths. Lock it with a test that asserts the bound query param scales with
the active-set size (a tiny fixed `max_rows` must be overridden by the batch-sized cap).

General rule: any time you `LIMIT` a result that is a UNION of independently-capped groups,
the global limit must be ≥ Σ per-group caps, or make the per-group window the sole bound.
Same trap applies to `TOP`/`OFFSET...FETCH` in other engines.
