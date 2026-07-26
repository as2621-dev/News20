# Residual review findings — issue #68 (SCRIPTS_ONLY halt + script similarity gate + armed reel stage)

Reviewed inline (correctness / spend-safety / contract lenses). Everything below was
found, judged, and deliberately left — none of it blocks the slice.

## R1 — `scripts/produce_source_reels.py` is a second, un-laddered spend path
`orchestrate_story` is called directly by that manual script, so it renders TTS +
posters without consulting `run_flags.resolve_run_stage()`. It is a human-launched
one-off tool (like `fill_batch_posters.py`), not the daily pipeline #68 stages, so
laddering it was out of scope (Rule 3). If source reels ever move onto the cron, that
script must read the arm first.

## R2 — `_produce_story_pool` still fuses write → render
Kept as a thin composition of the two new halves so the pre-existing produce-pool
tests (the byte-for-byte proof that the armed path is unchanged) keep exercising the
real seam. Nothing in production calls it any more — `run_daily_pipeline` drives the
halves directly. Delete it when those tests are retargeted at the halves.

## R3 — The armed LIVE path gains one judge call
`enable_script_dedup` defaults **False** in the library (the legacy path is unchanged),
but BOTH live entry points pass `True`, including on an armed run. That is deliberate:
an armed run re-writes its scripts from scratch, so gating only the halted run would
let the near-duplicate twin back in at the exact moment TTS + posters are billed. Cost
is one `gemini-3.5-flash` call per run; fail-open.

## R4 — Scripts are not persisted between the halt and the armed run
The founder reviews `.agents/scripts/<date>-scripts.json`, then the armed run writes
NEW scripts (same model, same inputs, but not byte-identical). What was approved is
therefore the batch's quality, not the exact wording that ships. Persisting and
replaying the approved scripts needs a store (table or artifact re-read) and was not
in #68's scope.

## R5 — `agents/pipeline/daily_batch.py` is 1582 lines (CLAUDE.md limit: 1000)
Pre-existing (1397 before this slice; +185 here). Splitting the batch runner is its own
refactor slice — doing it inside a spend-safety change would have destroyed the
byte-for-byte comparison that proves the armed path is untouched.

## R6 — `.agents/scripts/` is not gitignored
Mirrors `.agents/shortlists/` (#67), which is also untracked-but-not-ignored. Runtime
artifacts are simply never staged. Worth one `.gitignore` line when someone touches
that file for another reason.
