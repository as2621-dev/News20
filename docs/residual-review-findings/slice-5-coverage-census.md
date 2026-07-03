# Residual review findings — slice #5 (per-niche coverage census)

Commit: `feat(census): per-niche coverage report + hit-rate read-off (slice #5)` (`862dbd6`),
review-panel fixes folded into the follow-up commit. Findings the multi-agent review panel
(correctness/logic + simplicity/reuse lenses) raised that were DEFERRED (advisory / scoping)
or already handled, with reasoning. Applied fixes are in the fix commit and not re-listed here.

Fixed in the follow-up commit (for reference): every DB read now paginates through
`_fetch_all` (`.range()` loop) so a busy day past PostgREST's 1000-row `.select()` cap can no
longer silently truncate the present-day set or the hit counts; the dead
`_DEFAULT_PERSONA_EMAILS` constant was removed (the CLI already imports `PERSONA_SPECS`
directly as the single source of truth).

## 1. Counts persisted (produced/gated) tags, not all ingested candidates (low, correctness lens) — DEFERRED (scoping, disclosed)

`fetch_census_inputs` reads `story_interests`, which `persist.py` step 9 writes ONLY for the
produced/gated story subset (production/gating attrition happens before persistence). If
"direct-tagged candidate counts" in the acceptance criterion is read as *ingestion-time*
candidates (pre-gating), the census structurally undercounts. This is a property of the
current architecture — `story_interests` is the only persisted node-tagged store; there is no
separate ingested-candidate store. It is explicitly disclosed in the commit message and in
`docs/ops/coverage-census-day1-2026-07-03.md`. If a future slice persists the full ingested
pool, the census picks it up automatically (same table, same columns). No code change: the
go/no-go read-off must be read as "production persisted a direct niche tag for this section",
which is the honest thing the feed actually fills from.

## 2. `LadderLevelCount.depth_from_interest` is redundant with list order (low, simplicity lens) — DEFERRED (locked contract)

`depth_from_interest` is always the item's index in `ladder_context` (0 self, then
`enumerate(..., start=1)`), and `render_report_text` reads only `ladder_slug` +
`total_direct_hits`. It is part of the report shape pinned by the slice-#10 contract shape
test, so removing it is churn against a deliberately-locked surface for a field a downstream
consumer may still want to read explicitly rather than infer from position. Kept.

## Checked and clear (no findings)

- Window bounds are a correct half-open range (`.gte(start)` / `.lt(end-day+1)`), no off-by-one.
- `_utc_date_of` timezone conversion is correct and consistent with the UTC filter bounds.
- Ancestor walk has a `seen` cycle-guard; per-`(interest, day)` dedup is set-based; the
  hit-rate denominator guards div-by-zero and handles mid-window adds / missing days / dry
  niches (all under test).
- The inline `_ancestors` parent-walk is correctly NOT reused from
  `agents/ingestion/ancestor_tagging.py`: different input shape (raw Supabase dicts vs
  `InterestNode`), different output (ancestor dicts for slug/id vs `StoryInterestTag`
  payloads), uncapped-to-root vs grandparent-capped, warn-and-continue vs raise. Reuse would
  be strictly more code.
