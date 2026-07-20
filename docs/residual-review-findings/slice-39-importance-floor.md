# Slice #39 — importance floor: residual review findings (advisory only)

Review panel (correctness / simplicity / contract lenses) found no critical or high
issues. Advisory items deferred here:

1. **advisory · agents/worker/pipeline_routes.py:845 · on-demand path bypasses the floor**
   The worker's `_assemble_for_user` on-demand rebuild calls `assemble_user_feed`
   (the coarse allocator), not `assemble_niche_feed` — so neither the niche ladder nor
   the #39 importance floor runs on that path. This gap PRE-EXISTS slice #39 (it dates
   from slice #7: the niche assembler only runs on the orchestrator daily-batch path).
   Fix belongs to a future slice that routes the worker path through the niche assembler.

2. **advisory · agents/pipeline/feed_assembly.py (`_fill_niche_section`) · duplicate floor
   log entries across rungs** — a chain-tagged below-floor story appears in every rung's
   candidate list, so `niche_section_slot_floored` can log it once per rung (info-level
   noise only; counts are per-rung honest). Dedup by story id across rungs if the logs
   get noisy in prod.

3. **advisory · reference/ranking-spec.md · spec drift** — the section-fill importance
   floor (`NICHE_SECTION_IMPORTANCE_FLOOR`, feed_assembly.py) is not yet reflected in the
   ranking spec. Sync at the next `/improve-architecture` pass or the M2 validation slice
   (which tunes the value).
