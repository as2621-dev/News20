"""News20 interest-keyed news ingestion (Phase 1d).

Ingests real news *per active interest* (the distinct union of all users'
followed interest nodes), deduplicates cross-outlet coverage into a single
canonical story pool with outlet counts, and tags each story to its matched
interest node *and its ancestors* — so a niche-followed story reaches broad
followers for free at a lower DepthMatch (reference/ranking-spec.md).
"""
