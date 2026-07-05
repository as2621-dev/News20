---
title: NULLS NOT DISTINCT unique keeps a 2-col upsert working while allowing multiple rows per key
tags: [migration, postgres, postgrest, upsert, unique-constraint, allocation]
problem_type: pattern
symptoms: "need multiple rows per (a,b) for new feature, but an existing PostgREST upsert relies on (a,b) being unique"
date: 2026-07-03
---

## Problem
A table had PK `(follow_user_id, allocation_category)` and the frontend upserted on that
exact 2-column arbiter (`onConflict: "follow_user_id,allocation_category"`). Slice #6
needed MULTIPLE rows per `(user, category)` (three `sport` niche sections). Dropping the
2-col unique breaks the frontend upsert; keeping it forbids the new rows. A partial unique
index `(a,b) WHERE interest_id IS NULL` does NOT help — PostgREST can't express the index
predicate, so Postgres can't infer it as an `ON CONFLICT` arbiter.

## Fix (PG15+)
Move the PK to a surrogate `allocation_id uuid default gen_random_uuid()`, then add:

```sql
alter table t add constraint uq unique nulls not distinct (a, b, interest_id);
```

- `NULLS NOT DISTINCT` makes two `(a, b, NULL)` rows COLLIDE → preserves the old
  "one coarse/NULL-interest row per (a,b)" invariant.
- A non-null `interest_id` gives one row per `(a, b, interest_id)` → many niche rows per
  `(a,b)`.
- The frontend upsert changes its arbiter to the 3-col constraint
  (`onConflict: "a,b,interest_id"`); its rows omit `interest_id` (→ NULL) and the arbiter
  still matches thanks to NULLS NOT DISTINCT. No predicate needed, so PostgREST is happy.

Gotchas: needs Postgres 15+ (prod here is 17.6); on ≤PG14 the NULL rows would be treated as
distinct and silently duplicate. `add column ... not null default gen_random_uuid()`
backfills existing rows with distinct uuids (volatile default is evaluated per row).

See migration `0026_niche_allocation_sections.sql` + `src/lib/feedAllocation.ts`.
Related: [[definer-rpc-anon-execute-leak]] (same table-write-surface class of care).

## Extension (slice #12, 2026-07-05): the arbiter must include EVERY row-class discriminator

Once a THIRD row class shares the `(a, b, NULL-interest)` tuple, the 3-col arbiter silently
corrupts it. Here `user_feed_allocation` grew a `beyond-bubble` class: `interest_id NULL`,
`section_label = "Beyond your bubble"` — its arbiter tuple `(user, category, NULL)` is IDENTICAL to
a coarse row's, because `section_label` is NOT in the arbiter. A coarse upsert for a coinciding
category then conflicts onto the beyond-bubble row and overwrites its slot_count/sort_order (PostgREST
`ON CONFLICT DO UPDATE` writes only payload columns, so the label lingers → a corrupt hybrid).

Two lessons:
1. **The upsert arbiter must contain every column that distinguishes row CLASSES that share the
   NULL tuple.** Two classes are safe to coexist under one `(a,b)` only if a column in the arbiter
   tells them apart. Adding a discriminator column (`section_label`) without adding it to the
   `unique nulls not distinct (...)` re-introduces the very collision the surrogate PK was meant to
   allow. Fix: 4-col arbiter `(a, b, interest_id, section_label)`.
2. **Two-writer safety needs BOTH sides.** The frontend coarse writer additionally scopes its DELETE
   to true-coarse rows via `.is("interest_id", null).is("section_label", null)` so it never prunes a
   section row — but the DELETE guard alone is not enough: the UPSERT arbiter is the other half, and
   an arbiter that under-specifies still corrupts on write even when the delete is perfectly scoped.

Deferred fix tracked in `docs/residual-review-findings/slice-12-coarse-only-build-my-30.md` (needs a
schema migration + a product call on which row wins when classes coincide).
