# Sub-phases 3 + 4 — SHIPPED (escalation resolved by M2R)

**Phase:** phase-fsr-m2-theme-category
**Outcome:** SUCCESS. SP3 (wire theme-derived category into ingestion-time tagging)
and SP4 (end-to-end fixture proof + LIVE-E2E residual) implemented, tested, committed.
Supersedes the prior `phase-fsr-m2-theme-category-sub-3.md` escalation, which STOPPED
because only 3 of 8 category-root interest nodes existed.

## What unblocked it
Phase **M2R** (`plans/phase-fsr-m2r-root-interest-nodes.md`, committed `b76feab`):
- `supabase/migrations/0023_root_interest_nodes.sql` mints all 8 depth-0 topic-root
  interest nodes (`interest_slug` == the `FeedCategory` key) + re-parents picker leaves.
- `agents/pipeline/categories.py::root_interest_slug_for_category(category) -> str | None`
  — identity for the 8 topic roots, `None` for source axes (youtube/x).

So a theme-derived depth-0 tag on a category-root interest now RESOLVES in
`assign_category` (it no longer collapses to DEFAULT for the 5 previously-missing roots).
No schema or `assign_category` change was made in M2 itself.

## SP3 — wiring (files: agents/ingestion/{models.py,dedup.py,interest_keyed_pipeline.py})
1. `CanonicalStory.canonical_themes: list[str]` (additive field).
2. `StoryClusterer._to_canonical` aggregates the UNION of cluster members'
   `candidate_themes` (deduped, first-seen order, verbatim case) onto it.
3. `ingest_active_interests` tag loop: for each story,
   `category_for_themes(canonical_themes)` → `root_interest_slug_for_category` →
   look up the root interest id in a `root_id_by_slug` index of depth-0 nodes →
   emit a **depth-0** `story_interests` tag on that root. Keyword ancestor tags
   (`merge_story_tags`) are emitted **shifted +1** (clamped ≤ 2) so the theme tag is
   the **strict** lowest-depth signal `assign_category` selects — a deterministic
   theme-win, not a fragile slug tiebreak between two depth-0 tags.
4. Fail-loud-per-cell: no resolvable theme root (roots not loaded into the map) →
   a structured warning + keyword tags keep natural depth (degraded-but-categorizable,
   never a dropped story, never a batch abort). Empty/unmapped themes →
   `category_for_themes` returns `DEFAULT_CATEGORY` ("arts") with its own warning.

### Wiring decision (Open Q1 resolution)
Mechanism (a)-via-root-slug from the phase: a depth-0 tag on the category-ROOT
interest, whose slug round-trips through `category_for_slug` identity. Chosen because
it needs zero `assign_category`/schema change and is fully offline-testable. The one
addition over the phase's literal recommendation: the **+1 keyword shift**, required
because a keyword leaf is naturally depth 0 too — without the shift the theme tag and
keyword leaf tie at depth 0 and `assign_category`'s slug tiebreak is non-deterministic
w.r.t. intent (it can pick the wrong one when the theme slug sorts after the keyword
slug). Shifting keyword tags makes the theme tag the unambiguous winner.

## SP4 — end-to-end proof (file: tests/agents/ingestion/test_interest_keyed_pipeline.py)
- `TestThemeDerivedCategoryTagging`: business-themed story that matched a GEOPOLITICS
  keyword categorizes **business** via downstream `assign_category` (the M2 bug fix,
  asserted to NOT be geopolitics); theme tag strict depth-0 + keyword shifted to 1;
  no-theme → arts + batch completes; unmapped theme → arts (not keyword-inherited).
- `TestThemeCategoryEndToEnd`: a batched GKG-style adapter (`search_active_interests`,
  the trusted/batch path) returns theme-bearing candidates → ingest → tags →
  `assign_category` returns business for the themed story and arts for the no-theme
  one, in a single batch run (closes SP2-parse → SP1-map → SP3-tag → assign_category).

## DoD: PASS
- SP3 DoD (a) mis-categorizing keyword overridden by business themes: PASS.
- SP3 DoD (b) empty themes → DEFAULT + batch completes: PASS.
- SP4 DoD mocked adapter → ingest → tags → assign_category theme category (happy) +
  DEFAULT (no-theme): PASS.
- Full M2 suite + regression: 280 passed, 0 failed (uv pytest tool env). The ~18
  orchestrator/clustering/poster failures are missing-dep baseline (ffmpeg/PIL/
  datasketch), pre-existing and out of scope.

## LIVE-E2E residual (deferred — NOT run)
Real BigQuery GKG pull → confirm live `V2Themes` populate, the parser handles live
formatting, and `assign_category` over a live batch produces sensible categories on a
real day. Blocked by the offline sandbox (GDELT 403, no BigQuery creds). Never faked.

## Concern for M3 (touches ranking/importance)
The +1 keyword-tag shift demotes a keyword-path followed-LEAF's DepthMatch from 1.0
to 0.6. Deliberate trade (category correctness > leaf affinity) and harmless on the
M4 trusted-outlet NEWS path (no keyword tags → no shift). But M3 tunes ranking; it
should be aware the keyword path's DepthMatch ladder now starts at parent-depth for a
themed story, and decide whether the residual keyword path keeps that behavior or is
retired (M4 already rekeys news to category-domain fetch).
