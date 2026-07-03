# Slice #7 review-panel advisories (deferred)

The slice-#7 multi-agent review panel (correctness / data-integrity / contract / simplicity)
found the assembler correct, idempotent, deduped, ≤30, and the twin faithful. Three findings
were fixed in `fix(assembly): apply slice #7 review-panel findings`:
surfacing `feed_matched_interest_id` to the TS layer (so slice #8 can name the fallback
source), flipping the unknown-section strict default to fail-safe (never silently broaden),
and a docstring correction. The remaining items are advisory and deliberately deferred:

1. **Private-helper cross-module import.** `feed_assembly.py` imports `_index_tags_by_story`
   and `_walk_ancestors` from `stages.ranking`. Reuse is correct (one ladder), but they are
   now a shared seam and should shed the leading underscore. Deferred: renaming touches
   ranking's public surface + the sim's imports — out of this slice's scope (Rule 3).

2. **Legacy-path conversion double-hop.** For a roots-only user the orchestrator builds
   `NicheAllocationRow`s from `category_allocation`, and `assemble_niche_feed` converts them
   back to `CategoryAllocation` to delegate. Harmless field copying that buys a single public
   entry point; the orchestrator fallback could call `assemble_user_feed` directly. Low value.

3. **Redundant belt-and-suspenders cap.** The per-row `position >= feed_slot_budget` break and
   the final `slots[:feed_slot_budget]` are provably dead given `total_target ≤ budget`. Kept
   as defensive guards; not load-bearing.

4. **`feed_fallback_source_level` has no DB CHECK.** The 0–2 range is enforced only by the
   Python model (`le=2`). The pipeline writer is the sole producer, so this is a one-directional
   guard drift, not a live bug.

5. **`write_daily_feed` produce-once is a read-then-insert (TOCTOU).** Two concurrent batch
   runs for the same (user, date) could both pass the pre-check; the second insert then RAISES
   on `uq_daily_feed_position`/`uq_daily_feed_story` (loud failure, not silent duplication).
   Pre-existing — unchanged by this slice — and the unique constraints prevent corruption. A
   transactional/`on conflict` upsert would close it; tracked but out of scope.
