---
title: Shared once-per-period work — the UNIQUE key is the idempotency, the existence-check is only cost
tags: [shared-storage, idempotency, dedup, provider-degradation, xai, cluster-sweep, ingestion]
problem_type: pattern
symptoms: a "once per day, shared across users" job needs to (a) never do the expensive
  external call twice for the same key, (b) survive concurrent callers without duplicate
  rows, and (c) not poison the day when the provider fails; naively reaching for a
  per-user table or a check-then-write makes cost scale with users or leaves races/half-writes
root_cause: shared-not-per-user storage + which layer actually guarantees uniqueness
date: 2026-07-05
---

Built the shared X cluster sweep (issue #23, `agents/ingestion/cluster_sweep.py` +
migration `0031_x_cluster_sweeps.sql`). Three design choices that generalize to any
"produce once per (key, period), fan out to many followers" job:

**1. Store keyed by the SHARED key, never by user.** `x_cluster_sweeps` is keyed
`(cluster_id, sweep_date)` with `UNIQUE (cluster_id, sweep_date)` — NOT `(user_id, …)`.
N users following the same cluster share ONE row, so cost is flat with user growth. This
is the same "produce once, fan out" model the stories pipeline uses (`story_interests`),
applied to a per-cluster external sweep. If you catch yourself adding `user_id` to a table
whose content is identical for every user, stop.

**2. The UNIQUE constraint + upsert `on_conflict` is the real idempotency mechanism; the
existence-check is only a cost optimization.** The flow is: read-existing → if present,
return it (skip the expensive call) → else call + `upsert(..., on_conflict="cluster_id,sweep_date")`.
The read-then-write is a TOCTOU: two concurrent callers can both read `None` and both
proceed. That is FINE — the second upsert becomes an UPDATE against the unique constraint,
so the outcome is exactly one row. The existence-check does not prevent the race; it only
saves a redundant provider call on the common (already-swept) path. Don't over-engineer a
lock; let the constraint be correct and the check be an optimization. (Corollary: the
explicit index you're tempted to add on `(cluster_id, sweep_date)` is redundant — the
UNIQUE already creates it.)

**3. On PROVIDER failure, persist NOTHING; on an honest EMPTY result, persist the empty
row.** These two look similar but must diverge:
  - provider call raised (rate-limit / network) → return an empty result and write no row,
    so the day is retry-able and the once-per-day guard (a persisted row) isn't tripped by
    a transient outage. A single atomic upsert also means there's never a half-written row.
  - call succeeded but found nothing (a "silent" cluster) → write the empty row honestly.
    That is a real answer downstream must be able to read as "nothing today," never a gap
    that triggers re-sweeping or gets padded with a fabricated result.
Encode the difference with a `provider_failed` flag that gates persistence, not with an
empty-vs-nonempty check.

**Bonus (Rule 5, judgment-vs-code):** theme extraction from posts is a genuine LLM task, so
the grouping is an injectable seam. But the guarantees are deterministic CODE: dedup, the
retweet filter, the handle cap, and the "multi-handle" gate. Critically, do NOT trust the
model's self-reported attribution — recompute each theme's supporting handles from the
REAL fetched posts (match the model's cited URLs against the swept set) and gate on
`distinct handles >= 2`. A hallucinated second handle then can't rescue a single-source
"theme," and attribution is traceable by construction.
