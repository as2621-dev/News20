---
title: GDELT split — BigQuery is the batched workhorse, DOC is the sequential per-anchor scalpel
tags: [gdelt, bigquery, gdelt-doc, ingestion, pacing, rate-limit, niche, anchors]
problem_type: architecture
symptoms: long-tail WHO anchors (an individual athlete, a niche founder) return no niche
  candidates from the unthrottled BigQuery GKG pass even though real news exists; niche
  sections come up empty for exactly the sub-niche interests the product promised to cover
root_cause: GDELT's BigQuery GKG crawl skews to high-volume mainstream outlets, so it
  under-covers long-tail entities; the keyless DOC 2.0 API covers more but is hard-limited
  to ~1 req / 5s / IP with a multi-minute penalty box, so it cannot be the bulk source
date: 2026-07-04
---

Two GDELT products, two roles — do NOT make DOC a backbone (issue #16):

- **BigQuery GKG (`agents/ingestion/adapters/gdelt_bigquery.py`) = the workhorse.** ONE SQL
  matches EVERY active micro-interest at once, unthrottled, no 250-row cap. Primary niche
  path. `search_active_interests` returns candidates already stamped with the matched
  interest id/slug.
- **DOC 2.0 (`agents/ingestion/adapters/gdelt_doc.py`) = the scalpel** — a *gap-filler*, not
  a source. `agents/ingestion/anchor_scalpel.py::run_anchor_scalpel` fires ONE exact-phrase
  query per WHO anchor term, but ONLY for interests the BigQuery batch returned nothing for
  (`covered_interest_ids` skip), tagging hits to the interest node. Per-anchor cap bounds junk.

Non-negotiables the scalpel got right (pacing live-verified 2026-07-04):

1. **Sequential, never parallel.** `await` one anchor at a time — no `asyncio.gather`. The
   ≥5s spacing + penalty-box backoff live INSIDE `GdeltDocAdapter._throttled_get`; a penalty
   box surfaces as `AdapterFetchError` for that one anchor → log it and RESUME at the next
   anchor. Never parallel-retry (that re-triggers the penalty box and burns the night).
2. **Share ONE DOC adapter instance across all DOC callers on the box.** Ingest scalpel +
   render-time coverage census both use the SAME `GdeltDocAdapter` so its internal throttle
   lock enforces ≤1 req/5s GLOBALLY on that IP — two separate instances would each pace
   correctly but collide with each other.
3. **Additive, never raises.** A total DOC outage (every anchor errors) logs loudly at error
   level with a `fix_suggestion` and returns what it has — a "BigQuery-only night". The DOC
   loop only ever *extends* the candidate list; the BigQuery backbone pool is untouched.

Gotcha — **anchors are persisted JOINED, not as an array.** The interview captures
`search_anchor_terms: list[str]` (≥2) but `src/lib/interviewProfile.ts::persistInterviewInterests`
stores them as `distinctNonEmpty(...).join(", ")` into the single leaf `interest_search_query`
column (there is no per-anchor column / table). To iterate per anchor, split that column back
on `", "` (`anchor_scalpel.split_anchor_terms`). Anchor phrases with internal commas are rare
but would over-split — acceptable, but know it's the inverse of the persistence join.

B7: wrap each anchor in double quotes for an exact-phrase DOC query and STRIP embedded `"`
first (`build_anchor_doc_query`) so a crafted term can't close the phrase and inject raw DOC
operators (`domain:`, `OR`, …).

Test the pacing/backoff DURATIONS deterministically without real waits: `monkeypatch` the
module `asyncio.sleep` to a recorder coroutine and assert a ≥5s spacing sleep between two
sequential searches and a ≥5s backoff on the HTTP-200 "Please limit requests" notice. Never
hit real GDELT in tests — the penalty box wastes the run's budget.
