---
title: An "additive, safe" migration stops being safe the moment a reader SELECTs the new column
tags: [migrations, deploy-ordering, postgrest, supabase, resolve-once, backward-compatible-schema]
problem_type: gotcha
symptoms: a route that worked before the deploy returns 500 with PostgREST "column does not exist"; the migration was reviewed as additive/nullable/reversible and looked incapable of breaking anything
root_cause: additivity is a property of the SCHEMA change alone; the code shipped alongside it takes a hard dependency on the new column, so the safe-in-isolation migration becomes one half of an ordered pair
date: 2026-07-25
---

## What happened (slice #73, commit `cc3434d`)

Migration 0034 adds `stories.story_resolved_category` — nullable, no default, no index,
no backfill, no table rewrite. Every review lens says *safe*: old code ignores the
column, NULL preserves current behaviour exactly, one `drop column` reverses it.

But the whole point of the column is that the worker's on-demand feed loader **reads**
it, so the same slice adds `story_resolved_category` to that loader's `.select(...)`.
PostgREST resolves the column list at query time against the live schema, so on a worker
deployed ahead of the migration, every call to `/feed/assemble-for-user` and
`/feed/assemble-mine` fails with *column does not exist* — a hard 500 on a route that
worked five minutes earlier. Nothing about the migration is unsafe; the **pair** is
order-dependent, and the ordering lives entirely in the operator's head.

## Rules

1. **Grade the migration + its code together, never the SQL alone.** "Additive and
   nullable" answers *can old code survive the new schema?* The question that actually
   bites is the mirror image: *can the new code survive the old schema?* A writer-only
   column is genuinely order-free. A column any shipped `SELECT` names is not.
2. **Write the ordering into the artifacts the operator will actually be holding.** Put
   it in the migration header, the commit body, and the issue comment — the migration
   file is the one thing open at apply time, and the commit body is what a bisect
   surfaces. A plan doc nobody rereads at deploy time is not a control.
3. **Prefer a loud break to a runtime fallback** on a founder-operated, single-tenant
   service. Branching on "does the column exist" means two `SELECT` shapes forever and a
   degradation path that silently hides a botched deploy — worse than a 500 that names
   the missing column. On a multi-tenant or zero-downtime service the trade flips: ship
   the tolerant read first, migrate, then tighten.
4. **Watch for an already-pending deploy.** #73 landed on a branch that was *already*
   carrying an undeployed worker change (#65/#66). The hazard is not "someone deploys
   this slice early" — it is that an unrelated deploy authorized for another reason
   carries this reader out with it. Any queued deploy on the same branch inherits the
   ordering constraint.
5. **Make "applied but not yet exercised" observable.** After the apply and before the
   first batch, every row's verdict is NULL, which reads identically to "the feature was
   never wired". One positive counter at the read seam
   (`ready_pool_loaded{persisted_category_count}`) separates them — the same
   positive-evidence discipline as [[proving-run-mode-needs-positive-evidence-not-absent-errors]].

## The reason the column existed at all

The read path rebuilds its stories without `canonical_themes`, so it is *structurally*
incapable of re-deriving the category the batch resolved. Carrying a resolved value in
memory (the [[resolve-once-consume-dont-re-derive-plus-total-sort-key]] doctrine) only
reaches consumers inside the same process — a second process reading the same rows needs
the verdict **durable**. When two paths must agree and one of them cannot see the
inputs, persistence is the seam, and the ordering constraint above is its price.
