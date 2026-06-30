# Phase FSR-M7 — Summaries (long vs short) + docs — progress

**Status:** Done (2026-06-30). Single commit at phase end.

## Sub-phases

- **SP1 — summary-mode selector + variant constants — DONE.**
  - NEW `agents/pipeline/summary_mode.py`: `summary_mode_for(story) -> "long"|"short"|"news"` (youtube.com→long, x.com→short, else news), delegating to `is_source_origin_domain` (Rule 7, single-sourced domain set).
  - `agents/pipeline/prompts.py`: added `SCRIPTING_SHAPE_{LONG,SHORT,NEWS}` + `KEY_POINTS_{LONG,SHORT,NEWS}`. news = `""` (byte-identical guard); long/short non-empty and differ.
  - NEW `tests/agents/pipeline/test_summary_mode.py` (10 tests): mode selection (incl. case-insensitive, empty-domain) + variant constants distinct/shaped.

- **SP2 — long/short template in scripting — DONE.**
  - `scripting.py`: `_build_system_prompt` derives mode and interpolates `{SUMMARY_SHAPE}` (new slot in `DIGEST_SCRIPTING_PROMPT` LENGTH BUDGET block). News → `""` → byte-identical.
  - `test_scripting.py`: `TestLongVsShortSummaryShape` — youtube gets long block (not short), x gets tight (not long), news prompt byte-identical to template-with-slot-emptied, shape reaches mocked client.

- **SP3 — long/short key-points template in detail enrichment — DONE.**
  - `detail_enrichment.py`: `_build_system_prompt` interpolates `{KEY_POINTS_SHAPE}` (new slot on the key_points line of `DETAIL_ENRICHMENT_PROMPT`). "EXACTLY 5" + numeric-grounding gate untouched.
  - `test_detail_enrichment.py`: `TestLongVsShortKeyPointsShape` — youtube long, x tight, news byte-identical; existing exactly-5 + grounding tests still pass.

- **SP4 — docs — DONE.** Updated `reference/ranking-spec.md` (β 0.3→0.45, E1 authority, M6b source-first priority + recency→importance→id spill + 1.5 headroom, §3a.5 summary mode), `reference/sources-reuse-map.md` + `reference/source-catalog-taxonomy.md` (M1 0022 clusters + no-dup, M6a supersedes 5c SourceSwipe deck), `personalization-and-source-curation-spec.md` (source-first banner + roots collapse + migration ledger 0022/0023/0024 + summary mode), `README.md` (pointer), `reference/reuse-map.md` (scripting row note).

## Validation
- `pytest test_summary_mode.py test_scripting.py test_detail_enrichment.py` → **40 passed**.
- News path **byte-for-byte identical** (reconstruct-and-assert tests in both stages).
- No new ingestion / schema / migration / pipeline stage (diff = prompts + selector + tests + docs).

## Baseline (pre-existing, ignored per brief)
- 18 pipeline failures in acoustic_alignment / forced_alignment / orchestrator = missing `ffmpeg` + `num2words`; clustering/poster collection errors = missing `datasketch`/`PIL`. Not caused by this phase (no audio/render path touched).

## LIVE-E2E residual (deferred)
- Summary-quality spot-check on real fetched YouTube transcripts / X tweets needs a live LLM call + real content (no creds, GDELT egress blocked in sandbox). Not a gate on this phase.
