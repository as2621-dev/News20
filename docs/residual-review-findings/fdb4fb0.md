# Residual review findings — #74 (pre-production cut + standby promotion)

Commits `f055756` (slice) + `fdb4fb0` (panel fixes). Panel: correctness/logic,
contract/architecture, performance, data-integrity lenses. Ten findings were fixed
in `fdb4fb0`; the two below are deferred, with why.

## R1 — Script similarity gate does not span the promotion boundary (low)

`agents/pipeline/daily_batch.py` runs `dedupe_written_scripts` over each promotion
round's scripts in isolation, and `enable_batch_review`'s cross-reel diversity pass
likewise sees only the promoted batch. A promoted reel is therefore never compared
against the round-one scripts that have already rendered, so issue #68's "two reels
must not play as the same reel" guarantee has a seam at the promotion boundary.

**Why deferred, not fixed:** the fix needs a human call on a real conflict. Judging
`write_results + promotion_writes` together means the judge can nominate an
*already-rendered* reel as the drop — and there is no unrender. The options (always
drop the promoted twin regardless of which the judge preferred; re-slot the rendered
one; accept the duplicate) trade reel quality against wasted spend differently, and
that is a founder/product decision, not an implementation detail.

**Bounded by:** the story-level dedup judge already compared this pair upstream
(same event, near angle), so reaching the script gate as twins is second-order.
Promotions are also rare by construction — one per production failure.

## R2 — `docs/ops/m1-live-audit-2026-07-24.md:104` shows the retired knob (won't-do)

The captured preflight transcript reads
`produce cap headroom .......... 2.0x (demand → render pool)`. Its sibling
`docs/ops/m4-persona-validation-2026-07-03.md` did get a retirement note in
`f055756`, so the treatment is inconsistent.

**Decision: won't-do.** The m4 doc's line is a *recipe* an operator would copy, so
it needed the note. The m1 doc's line is a verbatim transcript of a run that really
happened on 2026-07-24, when the knob really was live. Editing it would falsify the
record. Ops transcripts stay verbatim; recipes get retirement notes.

## Flagged for awareness (no action)

- **Public contract change.** `run_daily_pipeline` lost `produce_cap_headroom` and
  `compute_category_produce_caps` lost `headroom_multiplier`; `ShortlistArtifact`
  gained the optional `shortlist_selection`. The env var `PRODUCE_CAP_HEADROOM` is
  retired — still setting it is harmless (it now only triggers a warning), so no
  Railway change is required before deploying.
- **`MAX_PRODUCE` semantics changed.** It now bounds reels shipped (selection plus
  standby promotions), not just the candidate pool. The worker default stays 8.
