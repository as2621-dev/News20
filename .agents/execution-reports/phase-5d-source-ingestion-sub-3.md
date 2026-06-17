# Phase 5d SP3 — Source pipeline + cadence + promote-to-pool + poster-skip

**Status:** SUCCESS · **Date:** 2026-06-17

## Implemented
1. **`agents/ingestion/scheduler.py` (new)** — `CadenceScheduler` (YouTube 6h / X 6h,
   configurable + fallback). `is_source_due(type, last_fetched_at, now)` (never-polled
   → always due; naive datetimes assumed UTC; `>=` boundary is due) and
   `fetch_since(...)` (cutoff = `last_fetched_at`, or a bounded cold-start lookback for
   a fresh follow). Ported PATTERN from the TL;DW donor, scoped to the two shipped axes.
2. **`agents/ingestion/dedup.py` (extend)** — added `SOURCE_ORIGIN_DOMAINS`,
   `is_source_origin_domain(domain)`, `source_item_dedup_key(candidate)` (normalized
   external_id), and `dedup_source_items(...)` (drops already-ingested + intra-batch
   repeats, preserves first-seen order, logs counts). Existing news clustering untouched.
3. **`agents/pipeline/produce_gate.py` (extend)** — `evaluate_story_for_production`
   gains `is_source_origin: bool`; when true it bypasses checks 1 (serves-an-interest)
   and 2 (importance/freshness floor) — a followed source is intrinsically wanted and a
   single-source item has outlet count 1 — while the produce-once check (3) still
   applies. `select_stories_to_produce` auto-derives the flag from the story's outlet
   domain via `is_source_origin_domain`, so news gating is unchanged.
4. **`agents/ingestion/source_pipeline.py` (new)** — `run_source_ingestion(user_id,
   followed_sources, ...)`: cadence-filter → dispatch by `content_source_type`
   (`youtube_channel`→YouTubeAdapter, `x_account`→XAccountAdapter via `fetch_new_items`)
   → `dedup_source_items` → substance filter → cluster into `CanonicalStory` →
   `PromotedSourceStory` tagged to the user, carrying its source image. Returns a typed
   `SourceIngestionResult` with full counts. Pure over injected adapters/scheduler/clock
   (no DB, no network). One source's `AdapterFetchError` is logged + skipped, not fatal.
   Out-of-scope types (podcast/personality) are skipped with a warning.
5. **Poster-skip — `agents/m0/build_poster_for_digest` (extend)** — new keyword
   `supplied_poster_image_url`; when set, the SERP→score→Nano-Banana path is skipped
   entirely (genai client untouched), the supplied image (local screenshot path OR
   remote thumbnail URL) is read and run through the SAME `grade_and_brand` house grade,
   written as `poster.webp`. Missing/unreadable image → no poster + a note (fail loud).

## Source-origin marking decision
A candidate/story is **source-origin iff its outlet domain ∈ {`youtube.com`, `x.com`}** —
already set by the SP1/SP2 adapters, propagating to `CanonicalStory.canonical_primary_outlet_domain`.
No new model flag: this is the existing distinguisher, so produce_gate, the poster-skip
seam, and the source pipeline all agree via one predicate `is_source_origin_domain`. The
supplied image rides on the existing `candidate_social_image_url` → `canonical_social_image_url`.

## Divergences / decisions
- **Enum values:** the schema (migration 0009) uses `youtube_channel` / `x_account`
  (not `youtube`/`x`). The scheduler + dispatcher key on the schema values.
- **Substance floor:** added `_MIN_SUBSTANTIVE_BODY_CHARS = 80` (plans Open Q2) so
  empty-caption / one-word items are dropped before promotion. First-draft constant; 5e's
  control surface ("only their big stuff") tunes on top.
- **`run_source_ingestion` is per-user + pure** — it returns the promoted pool +
  user/source association; it does NOT write `content_sources.last_fetched_at`,
  `content_source_items`, or the user→story tag. Those DB writes are the SP4 orchestrator's
  job (mirrors how `interest_keyed_pipeline` returns payloads, not DB writes).

## Review findings + fixes (self-review of the diff)
- Removed a dead `if False else` artifact and an unused `candidate_by_key` map left from
  an edit in the promotion loop; consolidated inner imports to module level.
- Confirmed `produce_gate` importing `is_source_origin_domain` from `dedup` introduces no
  circular import (smoke-imported the four modules cleanly).
- Verified the genai client is provably untouched on the skip path (test uses a client
  that raises on any attribute access).

## Validation
- `ruff check` (all 8 touched files): **All checks passed!**
- `ruff format`: applied (4 files reformatted, idempotent after).
- `pytest tests/agents/ingestion/ tests/agents/pipeline/ -q`: **400 passed, 3 warnings in 5.93s.**
  - ingestion: 124 → **142 passed** (+18: scheduler + source_pipeline).
  - new files alone: **21 passed** (scheduler 10, source_pipeline 8, poster_skip 3).
  - produce_gate: **9 passed** (no regression to existing news gating).
- All adapters, the genai client, and image fetch are mocked — no network, no yt-dlp,
  no xAI, no Playwright, no DB.

## Definition of done: PASS
- ✅ A followed channel's new upload becomes a story-pool candidate tagged to the user —
  `test_new_upload_becomes_source_origin_pool_candidate` (user_id + source_id + source-origin
  domain + thumbnail carried).
- ✅ Flows through produce_gate — source-origin stories auto-exempt from interest + floor
  checks (`select_stories_to_produce` + the gate tests; a count-1 source story would
  otherwise be gated out).
- ✅ Reel uses the thumbnail, poster generation skipped — `test_poster_skip_source` (genai
  untouched; supplied image graded into the poster; URL + local-path both work).
- ✅ Cadence blocks re-fetch within the window — `test_cadence_blocks_refetch_within_window`
  (adapter not awaited).
- ✅ Dedup drops an already-ingested item — `test_dedup_drops_already_ingested_item` +
  `test_intra_batch_duplicate_dropped`.

## Concerns
1. **Orchestrator poster wiring is one line outside SP3's allowed files.** The skip branch
   lives in `build_poster_for_digest` (allowed), but the caller
   `agents/pipeline/orchestrator.py:generate_poster_bytes` must pass
   `supplied_poster_image_url=story.canonical_social_image_url` (gated on
   `is_source_origin_domain(...)`) for the end-to-end skip to fire in production. I did NOT
   edit `orchestrator.py` (not in Files-touched) — flagging per the brief instead of
   editing. The branch + its test are complete and proven; only this pass-through remains.
   Recommend it land in SP4's orchestrator wiring or a tiny follow-up edit.
2. **SP2's screenshot path → storage upload (carried forward):** `candidate_social_image_url`
   for X is a LOCAL screenshot path (assets dir). The poster-skip reads local paths fine,
   but the persisted reel poster is uploaded by `persist_digest`; on the live worker the
   screenshot file must exist at persist time (same process/container as ingestion+produce).
   If ingestion and produce ever run in separate containers, the screenshot needs uploading
   to shared storage first. Not in SP3 scope; flag for SP4/deploy.
3. **`last_fetched_at` write-back** is the orchestrator's responsibility (SP3 only reads it
   for cadence). Without it, cadence cannot actually throttle re-fetch in production — SP4
   must stamp `content_sources.last_fetched_at = now` after a successful poll.
