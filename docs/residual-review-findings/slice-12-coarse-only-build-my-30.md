# Slice #12 — residual review-panel findings (Build-My-30 coarse-only)

Slice #12 shipped the coarse-only anti-corruption fix: `getUserFeedAllocation` projects the
migration-0026 niche columns; `saveUserFeedAllocation` filters section rows out of the upsert and
scopes the delete to coarse rows (`allocation_interest_id IS NULL AND allocation_section_label IS
NULL`); BuildYour30 renders niche/beyond-bubble rows as read-only named blocks and folds their
slots into the 30-budget so a coarse Save cannot over-allocate.

The multi-agent review panel found two defects that require **human-gated / cross-surface work**
and are therefore deferred (both are **latent, not reachable today** — the backend niche allocator
`agents/pipeline/niche_allocation.py::write_user_niche_allocation` is not wired into any running
path, so no user currently holds niche or beyond-bubble rows).

---

## 1. HIGH · beyond-bubble upsert-arbiter collision (needs a schema migration)

**Where:** `src/lib/feedAllocation.ts` upsert + `supabase/migrations/0026_niche_allocation_sections.sql`
unique constraint.

**Finding:** the coarse upsert targets the migration-0026 arbiter
`(follow_user_id, allocation_category, allocation_interest_id)` NULLS NOT DISTINCT, which **excludes**
`allocation_section_label`. A "Beyond your bubble" reserve row has `interest_id = NULL`,
`section_label = "Beyond your bubble"`, `category = an un-lit root`. A coarse upsert row for that
**same category** also has `interest_id = NULL` → identical arbiter tuple → the upsert conflicts
onto the beyond-bubble row. PostgREST `ON CONFLICT DO UPDATE` only writes the payload's columns
(slot_count, sort_order), leaving the label — producing a corrupt hybrid row and (because the
coarse-scoped delete then spares it, label set) making the user's coarse block vanish on reload.
The constraint literally cannot hold both a coarse `(cat, NULL, NULL)` row and a beyond-bubble
`(cat, NULL, label)` row at once.

Niche rows (interest set) are **fully safe** — the arbiter includes `interest_id`, so a coarse
upsert never collides with them. This residual is beyond-bubble-only.

**Reachability:** requires (a) the niche allocator wired and writing beyond-bubble rows, AND (b) a
category that is simultaneously an un-lit (beyond-bubble) root in the interview profile and a backed
coarse editable block in BuildYour30 — possible when the interview profile and the picker/interest-
vector backing diverge. Not reachable in the current app (allocator unwired).

**Concrete fix (schema migration — forward-only, human-gated prod apply per repo migration policy):**
extend the arbiter to include the label:
```sql
alter table user_feed_allocation drop constraint if exists uq_user_feed_allocation_user_category_interest;
alter table user_feed_allocation
  add constraint uq_user_feed_allocation_user_category_interest
  unique nulls not distinct (follow_user_id, allocation_category, allocation_interest_id, allocation_section_label);
```
Then change the frontend `onConflict` to
`"follow_user_id,allocation_category,allocation_interest_id,allocation_section_label"` and add
`allocation_section_label: null` to each `FeedAllocationUpsertRow`. Coarse rows (label NULL) still
collide with each other (one coarse row per user+category preserved); beyond-bubble rows (label set)
no longer collide with coarse. NOTE: must ship the frontend `onConflict` change and the migration
**together** — a 4-col `onConflict` against the unapplied 3-col constraint would break every prod
save ("no unique constraint matching"). Also a **product decision**: when a coarse category
coincides with a beyond-bubble reserve, which row wins? Filed as a follow-on slice issue.

---

## 2. MEDIUM · `assemble_niche_feed` silently skips coarse rows for a niche user (backend gap)

**Where:** `agents/pipeline/feed_assembly.py` `assemble_niche_feed` Pass-4 emit loop (branches for
source / `interest_id is not None` / `BEYOND_BUBBLE_LABEL` only).

**Finding:** once a user has **any** niche row, feed assembly routes to `assemble_niche_feed`, whose
emit loop has no branch for a coarse row (interest NULL, label NULL) → coarse rows are silently
skipped. So a deep-profile user's coarse Build-My-30 edits **have zero effect on their feed**. The
UI (post-#12) presents an editable coarse budget for such a user, which is misleading (edits persist
but don't reach the feed). Pre-existing backend behaviour surfaced — not introduced by #12 (#12 only
made the mixed coarse+niche state reachable by preserving niche rows across a coarse save).

**Concrete fix (product + backend, deferred):** decide the coarse-only model for niche users — either
(a) render coarse read-only / disable coarse editing when `nicheSections.length > 0` (deep users edit
via re-interview — matches founder intent), or (b) make `assemble_niche_feed` emit coarse rows so the
coarse budget reaches the feed. #12 already prevents the DB over-allocation (niche slots are folded
into the 30-budget), so this is a "coarse edits are a downstream no-op for niche users" honesty gap,
not a corruption. Filed with finding #1.

---

Applied in-slice from the same panel: loader now reuses `isCoarseAllocationSegment` (single coarse
discriminator); the beyond-bubble React-key collision fixed (key disambiguated by bucketId); the
30-budget folds in section slots (no over-allocation on a coarse Save).
