# Slice #45 — headline provenance: residual review findings

Head at review time: `fd7d006`. Panel: correctness lens + contract/data-integrity lens.
Everything below was evidenced but **not** fixed in the slice commit; each line says why.

## Deferred — deliberate design call

- **`story_sources` primary row pairs `canonical_url` (title-winner's article) with
  `canonical_published_utc` (cluster minimum).** Before this slice the representative
  *was* the earliest-published member, so URL and timestamp described the same article.
  Keeping `canonical_published_utc` as the cluster minimum was chosen over following the
  representative because it (a) matches the field's documented contract in
  `agents/ingestion/models.py` ("Earliest publication time across the cluster") and
  (b) leaves every freshness consumer — produce gate, ranking recency,
  `story_first_reported_utc` — bit-identical to pre-slice behaviour. The cost is that
  the primary `story_sources.source_published_utc` can now be earlier than its paired
  article URL by the intra-cluster delta (hours, same event). Fixing it properly means
  carrying the representative's own timestamp as a new `CanonicalStory` field — a model
  change with its own blast radius, out of this slice's scope.

## Deferred — advisory, no concrete fix inside this slice

- **Repeat LLM spend on unrescuable stories.** With the editorial rewrite enabled (the
  nightly config) a masthead-titled story pays scripting + verification + rewrite before
  being dropped, and nothing records the rejection — so it is re-ingested and re-charged
  every run. A negative marker (or a pre-scripting gate for `title_equals_outlet` with no
  body) is a product/cost decision, not a mechanical fix.
- **Other representative pickers were not given title-quality parity:**
  `agents/pipeline/produce_dedup.py::_representative_index` (picks most-recent among
  already-produced duplicates), `agents/pipeline/clustering/near_dup.py::drop_exact_reprints`
  (insertion order), `agents/pipeline/clustering/online_clusterer.py` (min index). These
  operate on already-gated or embedding-internal paths, so an unpublishable title cannot
  reach a reel through them — only recall is at stake. `clustering/reconcile.py` DID get
  parity in this slice, because it runs on the default prod path and would otherwise have
  reverted the fix into story loss.

## Filed as follow-up work

- **Already-persisted masthead reels are not cleaned up or filtered on read.** The gate is
  write-time only; `agents/worker/pipeline_routes.py::_load_ready_stories` rebuilds stories
  from `stories.story_headline` and re-assembles them with no re-check, so the existing
  07-07 "Language Magazine" row keeps shipping. Filed as its own backlog issue.
