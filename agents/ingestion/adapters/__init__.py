"""Source-type-specific news adapters for the News20 ingestion pipeline.

Each adapter implements ``BaseNewsAdapter`` (base.py): a two-phase contract of
``search()`` (discover candidate articles for a query) and ``extract_body()``
(fetch + extract the article body). The GDELT DOC adapter (gdelt_doc.py) is the
v1 source — keyless, global, fresh — chosen because no NewsAPI key is available
(see plans/phase-1d-daily-content-pipeline-progress.md Step 0).
"""
