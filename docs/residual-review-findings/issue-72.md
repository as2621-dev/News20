# Residual review findings — issue #72 (old-shape `story_interests` cleanup)

**Status: panel COMPLETE (run inline).** Three adversarial lenses — correctness of
the predicate + date scope, data-integrity / irreversibility, contract & architecture
— were executed by the build agent rather than spawned reviewers (notification relay
for spawned sub-agents has been unreliable; blocking on it would have parked a slice
whose prod mutation was already founder-authorized). Every concrete fix was APPLIED
before the commit; outcomes below.

## What shipped

- `scripts/cleanup_theme_root_story_interests.py` — dry-run-by-default one-off
  cleanup. The predicate is one pure function, `select_theme_root_tag_rows`, shared
  by the script and both test suites so the script cannot develop a private opinion
  of what an old-shape row is (same discipline as
  `retire_unpublishable_headlines.py::classify_unpublishable_rows`).
- `tests/scripts/test_cleanup_theme_root_story_interests.py` — 10 discriminator
  safety properties.
- `tests/agents/worker/test_mixed_vintage_story_interests.py` — the mixed-vintage
  regression through the REAL `_load_ready_story_pool` → `assign_category` path.
- `.agents/backups/story_interests-cleanup-72-2026-07-25.json` — the 212-row rollback
  manifest (full column payload; re-insertable with `ON CONFLICT DO NOTHING`).

## Prod mutation (founder-authorized, executed 2026-07-25)

| measure | value |
|---|---|
| `story_interests` total before → after | 1422 → 1210 (−212) |
| rows at `match_depth = 0` before → after | 516 → 304 (−212) |
| old-shape theme-root rows deleted | **212** |
| distinct stories affected | **146** |
| affected stories left with ZERO tags | **0** |
| rows still matching the predicate after | **0** |
| depth-0 root rows created after the cutover (never touched) | 0 |
| `created_at` span of deleted rows | 2026-07-03T23:53:45Z → 2026-07-19T19:49:09Z |

Untouched KEEP buckets verified intact after the delete: 287 depth-0 non-root leaf
tags, 17 depth-0 root rows predating the window.

**Correction to the issue body, from data:** the issue estimated the window as
"≈ the 06-30 → 07-07 producing runs". The real span is **07-03 → 07-19** — a further
producing run on **2026-07-19** wrote 60 rows the issue did not anticipate, and
nothing was written 06-30 → 07-02. The `[06-30, cutover]` delete scope contains the
observed span, so the mutation was correct; the estimate was simply narrower than
reality. Anyone reasoning about "the affected window" should use 07-03 → 07-19.

**Race question (#47 SHORTLIST_ONLY audit running concurrently) — VERIFIED, not
assumed:** `story_interests` rows with `created_at >= 2026-07-25T00:00:00Z`, at ANY
depth: **0**. The issue's assertion that SHORTLIST_ONLY runs write nothing to
`story_interests` holds. Independently, the `created_at <= cutover` bound means the
DELETE could not have reached a concurrent write even if one had landed mid-run.

## Findings → outcomes

| # | Finding (lens, severity) | Outcome |
|---|---|---|
| 1 | `int(depth or 0) != 0` coerced an absent/NULL `match_depth` to 0, widening an IRREVERSIBLE delete on a field that isn't there (data-integrity, MED) | FIXED — `isinstance(raw_depth, int) and raw_depth == 0`; regression `test_row_with_a_missing_or_null_match_depth_is_never_selected`. Unreachable in prod (column is `not null` and the scan filters server-side), but the pure function is the reusable artifact |
| 2 | Rollback recipe undocumented — a part-way-failed run leaves some rows present, so a naive re-insert aborts on `uq_story_interest` (data-integrity, MED) | FIXED — ROLLBACK section in the module docstring specifies `ON CONFLICT DO NOTHING` and records that `story_interest_id` / `created_at` are plain defaulted columns, so the restore is byte-faithful |
| 3 | Unpaged roots lookup could silently truncate at the PostgREST 1000-row cap (correctness, LOW) | DOCUMENTED, not changed — 15 roots in prod, and truncation yields a SMALLER root set, i.e. fewer deletions (safe direction). Paging a 15-row lookup would be premature (Rule 2) |
| 4 | Empty roots set could degrade to a wildcard that deletes every in-window depth-0 row (correctness, HIGH if hit) | ALREADY GUARDED, now tested at both layers — `select_theme_root_tag_rows` returns `[]` on an empty set, and `_fetch_root_interest_ids` RAISES rather than returning empty so "0 to delete" can never be confused with a broken lookup |
| 5 | Cascade risk from deleting `story_interests` rows (data-integrity) | CHECKED CLEAN — no table in `supabase/migrations/` references `story_interests.story_interest_id`; the delete cannot cascade |
| 6 | A story could lose its ONLY tag and fall to the loud `DEFAULT_CATEGORY` fallback instead of its true category (data-integrity, HIGH if hit) | MEASURED CLEAN — 0 of the 146 affected stories were left tagless. Empirically confirms the discriminator's premise: in-window theme roots were purely ADDITIVE alongside the real (shifted) keyword tags |
| 7 | Window constants drifting silently would change what a re-run deletes (correctness, LOW) | FIXED — `test_cutover_constant_matches_the_c38092f_commit_instant` asserts both bounds against the commit instants rather than trusting a comment |
| 8 | A regression fixture where the real leaf happened to win the slug tiebreak would pass while proving nothing (contract, MED) | FIXED — the fixture pins `arts` (root) against `sport.cricket` (leaf) so the phantom DOES win pre-cleanup, and the test asserts the before-verdict alongside the after-verdict. The post-state is derived by running the real discriminator, not hand-written, so the two cannot drift |
| 9 | Test-to-test import of `_FakeSupabase` from `test_pipeline_routes.py` (contract, LOW) | ACCEPTED — reuse beats a third copy of the fake; same package, and a refactor breaks it loudly at import |

## Open residuals (deliberate, tracked)

1. **On-demand assembly can still never see the theme map** — the worker's loader
   rebuilds `CanonicalStory` without `canonical_themes`
   (`agents/worker/pipeline_routes.py:700-716`), so `assign_category` on the
   assemble-for-user / assemble-mine paths runs with `theme_category_by_story=None`.
   This cleanup removes the *corruption* those paths were reading, but an untagged
   story on that path still falls to `DEFAULT_CATEGORY` rather than its aboutness.
   The recorded design option — persist the resolved category on `stories` and have
   the loader read it — is AC #4 of #72 and was deliberately NOT built here
   (schema + writer + backfill is its own slice, and this slice was scoped to a
   DB-only cleanup). **Filed as a follow-up slice.**
2. **Keyword-tag depths remain attenuated.** By design: the old writer's
   `min(depth + 1, 2)` clamp collapsed parent and grandparent into a persisted `2`,
   so an inverse shift would be a guess. Affected rows score slightly low on
   `DEPTH_MATCH_BY_DEPTH` / `DEPTH_ATTENUATION` until those stories age out. No
   further action planned — attenuation is the safe direction.
3. **`user_interest_profile` weights already corrupted before this cleanup are not
   repaired.** `agents/memory/session_processor.py` applied the full 1.0x nudge to
   phantom roots on every engagement with a pre-fix story. The cleanup stops the
   bleeding (no phantom rows remain to attenuate against) but does not unwind
   weights already written. Not measured; likely small relative to normal weight
   drift. Raise a slice if root-category affinities look inflated.
