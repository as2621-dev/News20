# Residual review findings — issue #73 (persist the resolved category on `stories`)

Commit: `cc3434d`.
Panel: run inline (writer coverage across every produced story, NULL-path equivalence,
migration reversibility/safety, the apply-before-deploy hazard, contract with #70's
verdict map). Fixes were applied during the build; what follows is what was **accepted,
not fixed** — plus the one thing that is not code at all.

## Verified during review (not residual)
- **Writer reaches every produced story.** `WritePhaseResult` is constructed in exactly
  one place (`agents/pipeline/orchestrator.py:583`), and the batch-review barrier
  reshapes survivors with `model_copy(update={"script": …})`
  (`agents/pipeline/stages/batch_review.py:271`), which preserves the new field. The
  batch's render wave goes through `render_phase` (`agents/pipeline/daily_batch.py:795`),
  never `persist_digest` directly — so the carry cannot be bypassed for a produced story.
- **The NULL path is structurally identical, not merely equal in test.**
  `assign_category` branches on `if category_override_by_story:`
  (`agents/pipeline/stages/ranking.py:1040`), so an empty dict takes the exact same
  branch as the `None` that path passed before. No other module branches on
  `category_override_by_story is not None`.
- **No value the writer can emit can abort the INSERT.** All ten `FeedCategory` Literal
  members are present in the Postgres `feed_category` enum (0008 + 0010 + 0020). The
  reverse gap — enum values the Literal does not carry (`breaking`, `world_politics`,
  `tech_science`, `markets`, `culture`, `podcasts`) — is dropped by the reader's
  membership filter rather than handed to an allocator that has no bucket for it.

## Residual 1 — no backfill; the fix is forward-only
Every story already in `stories` has `story_resolved_category` NULL, so the on-demand
path keeps re-deriving their category from tags until they are re-produced. The slice
therefore fixes new production, not the pool the founder is looking at today.
**Accepted:** the issue scopes backfill as optional and NULL as the safe default. A
backfill would have to re-run `compute_category_verdicts` over historical pools —
a separate slice with its own correctness burden. The new
`ready_pool_loaded{persisted_category_count}` log is the number that decides whether it
is worth filing.

## Residual 2 — non-batch persist callers write NULL
`persist_digest` callers outside the nightly batch (source-reel production, e2e
fixtures, scripts) have no `compute_category_verdicts` verdict to offer and so leave the
column NULL. Source reels in particular have an obvious intended category (`youtube` /
`x`) that nothing writes.
**Accepted:** inventing a verdict at those call sites would be the second resolver this
slice exists to avoid. NULL is honest — it says "the batch never ruled on this story".

## Residual 3 — apply-before-deploy is enforced by documentation only
The reader SELECTs the new column unconditionally. If the worker is deployed before
migration 0034 applies, `/feed/assemble-for-user` and `/feed/assemble-mine` return 500
(PostgREST "column does not exist"). Nothing in code detects the missing column and
degrades to the old SELECT.
**Accepted:** a runtime fallback would mean two SELECT shapes and a silent-degradation
path that hides a botched deploy — worse than a loud failure for a founder-operated,
single-tenant worker. The constraint is stated in the migration header, the commit body
and the issue comment instead. **This must sequence with the pending #65/#66 worker
deploy (`bfd9281`): migration first, deploy second.**

## Residual 4 — the override map is typed `dict[str, Any]`
`_load_ready_story_pool` returns `tuple[list[Any], list[Any], dict[str, Any]]` while the
consumer declares `dict[str, FeedCategory]`.
**Accepted:** `agents/worker/pipeline_routes.py` imports every `agents.pipeline` symbol
lazily inside functions, so the module cannot name `FeedCategory` in a signature without
breaking that convention — which is also why the first two tuple elements were already
`list[Any]`. The docstring states the real shape; the runtime membership filter enforces
it.

## Residual 5 — `reference/supabase-schema.md` not updated
The doc's `stories` DDL is already stale (missing `story_detail_category` /
`story_is_breaking` from migration 0015) and this column was not added to it.
**Accepted:** Rule 3 — the drift predates this slice and fixing it properly means
reconciling several migrations, not appending one line. Worth its own cleanup slice.
