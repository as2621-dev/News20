---
title: A metric read off the shortlist artifact counts standbys unless you intersect with the production ids
tags: [shortlist, selection, census, metrics, issue-74, issue-10]
problem_type: architecture
symptoms: a coverage/hit-rate number computed from `.agents/shortlists/<date>-shortlist.json`
  reads HIGHER than the same metric computed from stored `story_interests` rows, and the
  gap is invisible because both look like "matched interest slugs"
root_cause: since issue #74 `shortlist_entries` is candidates PLUS the standbys ranked below
  the cap line, so it is a superset of what the run would produce; `story_interests` rows are
  written per PRODUCED story, so a niche whose only match is a standby is a hit in one
  instrument and a miss in the other
date: 2026-07-26
---

Established in slice #10 day-3, building `agents/pipeline/selection_census.py` — the
≥60% hit-rate read for runs halted at the shortlist rung (the founder's produce-once
spend contract means no reels, therefore no `story_interests` rows to read).

**The trap.** `ShortlistArtifact.shortlist_entries` reads like "the stories this run
picked". It is not. Per `agents/pipeline/shortlist.build_produce_shortlist`, it is
"the candidates that survived the gates … **PLUS the standbys ranked below the cap
line** (issue #74), so every id the selection block names resolves to an entry." The
review artifact deliberately over-includes so a founder reviewing it can see what
would be promoted if a reel failed.

So any per-niche count taken straight off `shortlist_entries` silently includes
stories that will never be produced, will never earn a `story_interests` row, and
will never reach a feed.

**The fix — intersect with the production set:**

```python
selection = artifact["shortlist_selection"]                  # None on pre-#74 artifacts
production_story_ids = set(selection["selection_production_story_ids"])
produced_matches = pool_matches_for_slug & production_story_ids   # hit iff non-empty
```

Keep the pool count as *context* (it tells you supply exists but ranked short), and
score the hit off the produced set. Reporting only the pool count is what makes the
two instruments disagree.

**Why it matters more than it looks.** The day-3 measurement moved
**100% → 91.7%** on this alone: one niche (`business.venture-capital`) had exactly
two matches and both were standbys. Near a hard 60% go/no-go bar, one cell is the
whole decision — the pre-tuning read was 58.3% against 60%.

**Three artifact generations, all still on disk** — a reader must handle each:
a bare JSON list (pre-2026-07-25, no envelope); `{shortlist_run, shortlist_entries}`
(#67); and `+ shortlist_selection` (#74). `shortlist_selection is None` is the honest
"cannot separate standbys" signal — fall back to the whole pool and *say so in the
output*, rather than quietly reporting a number that means something different.

**Generalizes to:** any consumer of a review/audit artifact that was built to
over-include for human inspection. The set a human wants to *see* is rarely the set a
metric should *count*.
