# Slice #6 residual finding — two writers share `user_feed_allocation`

**Status:** advisory (deferred). Not a blocker for slice #6; flagged for slice #7 / owner.

## What
After migration 0026, `user_feed_allocation` has two writers with different row shapes:

1. **Frontend "Build your 30"** (`src/lib/feedAllocation.ts` `saveUserFeedAllocation`) —
   writes COARSE rows (one per category, `allocation_interest_id` NULL) and then DELETEs
   any row whose `allocation_category` is not in the saved set.
2. **Backend niche allocator** (`agents/pipeline/niche_allocation.py`
   `write_user_niche_allocation`) — full-replaces a user's rows with NICHE rows
   (interest ref + label), beyond-bubble, and source rows.

## The coupling
- The frontend's stale-row DELETE (`.not("allocation_category","in", saved)`) is scoped to
  the user but NOT to `allocation_interest_id`, so if a deep-profile user re-runs the
  "Build your 30" screen, it can DELETE backend-written niche rows for any category the
  coarse save dropped, and leaves niche rows for kept categories coexisting with a new
  coarse row (same category, NULL vs non-NULL interest) — a mixed, ambiguous state.
- Conversely, the backend allocator full-replaces (delete-all-then-insert), so it wipes any
  coarse rows the frontend wrote.

Today these do not collide in practice: the seeded personas never touch the frontend, and
real users have only coarse rows. But once the interview + "rebuild my feed" path (PRD
story #18) writes niche allocations for real users who also open "Build your 30", the two
writers race on the same rows.

## Recommended resolution (slice #7 / onboarding)
Pick ONE owner of `user_feed_allocation` per user, or make the frontend niche-aware:
- Option A: the interview/niche allocator becomes the sole writer for deep-profile users;
  the "Build your 30" screen edits niche sections (not coarse categories) for them.
- Option B: scope the frontend's stale-row DELETE to `allocation_interest_id IS NULL` so it
  only prunes its own coarse rows and never touches backend niche rows.

Option B is the smaller change and keeps both surfaces working; it is the suggested default.

## Additional notes for slice #7 (from the review panel)

1. **Writer atomicity.** `write_user_niche_allocation` does delete-then-insert as two
   round-trips (not one transaction). It is currently only called by the manual
   `scripts/allocate_niche_sections.py`; when slice #7 wires it into the daily batch, move
   the replace into a transactional Postgres RPC so a crash between the two calls can't
   leave a user with zero rows. (The empty-plan case is already a no-op — it never deletes.)

2. **Loader projection must be extended.** `daily_batch._load_category_allocation` selects
   only the 4 legacy columns, so today it reads niche rows as coarse per-category budgets
   (graceful degradation — no crash, feed collapses to category granularity). Slice #7 MUST
   add `allocation_interest_id, allocation_section_label` to that projection AND to the
   loaded model, or niche rows stay silently coarse.

3. **Beyond-bubble discriminator.** A beyond-bubble row and a coarse/roots-only row both
   have NULL interest + a topic category; they differ ONLY by `allocation_section_label`
   (`BEYOND_BUBBLE_LABEL` vs NULL). Import `BEYOND_BUBBLE_LABEL` from
   `agents.pipeline.niche_allocation` — never re-hardcode the string. A typed
   `allocation_row_kind` column would remove the string-match fragility if #7 wants it
   (out of scope for #6, which the issue capped at exactly two new columns).

4. **Beyond-bubble shape is per-root, one slot each** (3–5 un-lit roots), not a single
   pooled N-slot row. Slice #7 fills each from that root's importance-ranked backbone;
   per-root spread IS the intended serendipity (avoids a single hot un-lit root dominating).
   This is a deliberate design choice, ratified here against PRD Decision #7 wording.
