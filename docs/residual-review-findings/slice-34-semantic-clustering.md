# Residual review record — slice #34 (semantic clustering reconcile in production)

Commits: `844ecdf` (slice) + fix commit (this one). Review panel: **all three lenses
returned** (correctness, simplicity/reuse, data-integrity).

## Panel findings → resolution

| Finding | Severity | Resolution |
|---|---|---|
| `persist_run` per-cluster loop could commit a partial, order-dependent subset of `story_clusters` before a mid-loop failure, silently discarded by the new whole-run fallback | HIGH (data-integrity) | **Fixed**: `persist_run` now does ONE batched upsert per table (`upsert_clusters`, `upsert_cluster_members`); failure test pins that a cluster-batch failure touches no member rows |
| `_guard_category_precedence` clamped `story_interest_match_depth` to steer the category contest — but that field is also the ranker's DepthMatch input and is persisted verbatim (`build_story_interest_rows`); clamp degrades a genuine foreign-interest follower (1.0 → 0.6/0.3) and depth 3 zeroes affinity (`DEPTH_MATCH_BY_DEPTH.get(3, 0.0)`) | HIGH (data-integrity + correctness, deduped) | **Fixed by removal**: guard rewritten as `_log_cross_category_merge_conflicts` — detection + structured `reconcile_category_conflict` log only, tags byte-identical (test (e) pins depths untouched). Enforcement **deferred** — see remainder below |
| `guard_enforced=False` branch untested | MEDIUM (simplicity) | **Fixed**: test (g) pins the untagged-representative conflict (logged, unenforced, tags pass through) |
| Fallback log's "re-run — idempotent" wording misleading: an embedding failure before persist means that batch's duplicate pairs are never re-seen together — the dedup miss is permanent for the day | Process note | **Fixed**: `fix_suggestion` now states the collapse is permanently skipped for the batch and produce-once keeps the duplicates |

## Deferred (#34 remainder — issue parked back with scope)

**Category-flip ENFORCEMENT without depth mutation.** Panel-agreed design: reconcile
returns `category_override_by_story: dict[str, FeedCategory]` (representative's
fetching category per conflicted merged story); `assign_category` gains an optional
override checked before the depth/slug computation; the dict rides the existing
`cluster_importance_by_story` plumbing to the four call sites
(`stages/ranking.py:1076`, `feed_assembly.py:951`, `produce_caps.py:190,267`).
Until then a cross-category merge can still flip the surviving story's category
(logged, never silent).

**Future call (advisory, no fix now):** alerting on `semantic_reconcile_failed_run_fallback`
— the dedup miss is permanent per-day, so repeated fallbacks deserve operator attention
beyond a log line.

## Reusable learning (compound, folded here)

`story_interest_match_depth` is load-bearing TWICE: it decides the category contest
(`assign_category` lowest-depth rule, #35 doctrine) AND it is the ranker's DepthMatch
input persisted verbatim to `story_interests`. Any mechanism that mutates depth to
steer categorization corrupts personalization (and past depth 2 silently zeroes
affinity). Steer category via an explicit override seam, never via depth.
