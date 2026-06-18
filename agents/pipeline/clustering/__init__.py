"""Online clustering foundations for the shared-pool rework (Milestone M3a).

This package holds the reusable building blocks of the global clusterer: the
Gemini embedding adapter, the MinHash near-duplicate prefilter, the cluster-store
repository, and their pydantic models. Each module is a self-contained, unit-
tested primitive that M3b's assign-or-spawn engine composes — no module here
performs a live DB write or a live LLM call without an injected client.
"""
