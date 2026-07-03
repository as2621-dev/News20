# Slice #7 residual — EntityBonus not applied on the niche-first path

**Status:** advisory (deferred), not a correctness bug. Filed by the slice-#7 build.

## What

`assemble_niche_feed` (`agents/pipeline/feed_assembly.py`) fills niche sections with the
per-node scorer (`score_stories_for_interest`), which computes the base
`Score = (Affinity × DepthMatch)·0.5 + Importance·0.45 + Freshness·0.2` but does **not**
fold in the additive **EntityBonus** (`ENTITY_BONUS_WEIGHT`, a followed entity whose
label/ticker matches the story title). The coarse delegate path (`assemble_user_feed` →
`score_and_classify_for_user`) still applies it, so a **roots-only** user is unaffected;
only the niche path drops it.

## Why it was deferred

- The slice's acceptance criteria (honest ladder, strict, dedup, beyond-bubble, twin,
  idempotency) do not depend on the EntityBonus, and slice #7 was the tightest in the
  backlog (~100k budget). Threading the entity-aware score per node cleanly (the bonus is
  applied once per best-candidate-per-story in `score_and_classify_for_user`, not per
  interest bucket) is a non-trivial refactor of the scoring seam.
- It is a ranking-quality gap **within** a section (a Nvidia follower's Nvidia story would
  not get lifted above a twin inside their "AI" niche), never a correctness or honesty
  failure — no silent substitution, no wrong section, no missing/duplicate slot.

## How to close (follow-on)

Thread the EntityBonus into the niche fill: after `score_stories_for_interest` returns a
node's candidates, apply `compute_entity_bonus(...)` to each (reusing
`normalize_entity_follow_weights` for the user), add it to `candidate.score`, and re-sort
before `_take_top_qualifying`. Add a niche-path variant of the existing entity-lift test
(`tests/agents/pipeline/test_ranking.py` / `test_niche_assembly.py`). Small, isolated,
additive — a good standalone slice.
