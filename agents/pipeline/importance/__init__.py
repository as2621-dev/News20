"""Authority-weighted, within-category-normalized importance (the shared-pool E1 model).

This package implements the **existing** E1 ``story_importance`` model specified in
``reference/shared-pool-pipeline.md`` §4 and ``plans/shared-pool-rework-master-plan.md`` —
NOT a parallel design (PRD Decision #5, Rule 7). It replaces ``produce_gate``'s raw
``min(1, story_outlet_count / 12)`` as the intrinsic importance signal for clustered
stories.

    story_importance(cluster) =
          W_breadth   · norm(distinct outlet count)        # syndication-dampened breadth
        + W_authority · authority_and_diversity(outlets)    # high-authority AND varied > N farms
        + W_velocity  · norm(cluster_velocity)              # coverage acceleration (ex-"breaking")
        + W_recency   · freshness(cluster_last_seen_utc)    # ~24h half-life (reuses produce_gate)
        + W_entity    · entity_prominence(cluster)          # involves registry entities
    → normalized WITHIN cluster_category

Two self-contained modules:

  - ``source_tiers`` — the config-driven domain → authority-tier lookup + the
    ``authority_and_diversity`` aggregator (SP1). A Python config artifact, NOT a DB
    table, mirroring ``produce_gate``'s single-config-source convention.
  - ``story_importance`` — the per-cluster raw-term computation, the ``W_*`` weight
    combine, and the within-category normalization + cluster wiring (SP2/SP3).

Every function is **pure** over its injected inputs (no DB, no clock, no network) —
fully offline-unit-testable, like ``produce_gate`` and ``stages.ranking``.
"""
