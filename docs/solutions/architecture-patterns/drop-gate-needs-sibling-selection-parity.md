# A gate that DROPS records needs parity in every sibling "pick the representative" rule

**Date:** 2026-07-18 · **Slice:** #45 (commit 5012c70) · **Files:**
`agents/shared/headline_quality.py`, `agents/ingestion/dedup.py`,
`agents/pipeline/clustering/reconcile.py`, `agents/pipeline/persist_helpers.py`

## Problem

Slice #45 made headline quality fail-closed: `StoryClusterer` began picking the
**best-titled** cluster member instead of the earliest-published one, and persist
began DROPPING a story whose final title is a masthead. But a second, independent
representative-picker — `clustering/reconcile.py::_pick_representative_index`, on
the semantic-merge path that is **ON by default in prod** — still picked
earliest-published. A merged group would therefore keep the masthead title, and
the new gate would then drop the whole event *even though a publishable headline
existed in the group*. The quality fix would have silently become story loss.

## Rules

1. **Grep for every sibling selection rule before shipping a drop-gate.** Any
   function that chooses which member of a group survives (`min(...)` over a
   cluster, `_pick_representative_index`, `_representative_index`, "keep the
   first/earliest/most-recent") is a place the gate's input is decided. A stale
   sibling does not merely fail to help — it feeds the gate the worst candidate
   and converts a no-op into data loss. Parity goes in the SAME commit.
2. **Calibrate a drop threshold to the junk class only.** The headline word floor
   is 3, not 4, because real 3-word headlines exist ("Musk buys Twitter") and this
   gate has **no fallback** — a false positive costs a whole reel. Where a gate
   only reorders or warns, a tighter threshold is cheap; where it drops, every
   point of tightening buys junk removal with real records.
3. **Gate the target class, not everything on the path.** Followed-source
   (YouTube/X) reels ride the same `write_phase`/`persist_digest` seam but carry
   creator-written titles, not scraped `<PAGE_TITLE>`s. They needed the same
   `is_source_origin_domain` exemption they already have at the produce gate and
   the poster gate — the exemption list is a codebase convention, so check it
   whenever adding a gate to a shared path.
4. **Keep an invariant field off the changed rule.** Moving the representative
   would have moved `canonical_published_utc` (freshness, ranking recency,
   `story_first_reported_utc`) with it. Reading it from an explicit
   `earliest_published_utc` over all members kept every downstream consumer
   bit-identical. Same for the fuzzy-match anchor: pinned to the first-seen member
   so cluster *membership* cannot become a function of title quality.

## Applies with

`architecture-patterns/no-signal-returns-none-not-default-plus-twin-drift-test.md`
— that entry is the reject-don't-default half; this one is what reject-don't-default
costs you when a sibling picker still hands over the reject.
