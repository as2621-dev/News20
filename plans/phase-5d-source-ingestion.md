# Phase 5d: Ingestion of followed sources

**Milestone:** M5 — Two-axis personalization (sources + control surface)
**Status:** Not started
**Estimated effort:** L

## Goal
A followed **YouTube channel / podcast / X account**'s fresh content is detected on a schedule, transcribed, deduped, and promoted into the per-user **story pool** that feeds the existing digest pipeline — so source-driven stories sit alongside topic-driven news.

## Why this phase exists
The sources axis is inert until followed sources actually produce digest content (spec §4). This phase ports TL;DW's battle-tested YouTube + podcast ingestion (Decision #12) and builds the one missing adapter (X). It wires source content into News20's existing `agents/ingestion` + `agents/pipeline` so produced stories are indistinguishable downstream.

## Context the sub-agents need
- **Existing News20 ingestion:** `agents/ingestion/` has `adapters/base.py`, `adapters/gdelt_doc.py`, `ancestor_tagging.py`, `dedup.py`, `interest_keyed_pipeline.py`, `models.py`. New adapters must conform to `adapters/base.py`. The produce/script/TTS pipeline lives in `agents/pipeline/` (`orchestrator.py`, `daily_batch.py`, `feed_assembly.py`, `produce_gate.py`, `stages/`). Trigger tasks: `trigger/dailyPipeline.ts`, `trigger/produceStory.ts`.
- **Donor:** `reference/sources-reuse-map.md` §3–§4 — port `adapters/youtube.py:62-477` (uploads-playlist `"UU"+id` trick; `playlistItems.list` = 1 quota unit; captions-only transcript), `podcast_audio.py` + `adapters/podcast.py` (RSS→Whisper, duration cap + daily cost budget), `scheduler.py` (`CadenceScheduler`), `trigger/ingestion-cron.ts` (2h fan-out). **Drop the Pinecone embed step** (News20 uses in-context grounding — see `news20-qa-incontext-grounding`).
- **X is build-fresh:** no donor adapter; only the `TwitterContentMetadata` shape (`models.py:436`) is reusable. Decide the X API in 5c's open Q.
- **Lands in:** `content_source_items` (from Phase 5b migration 0008), then **promoted** into the story pool. Tables already exist after 5b.
- **Secrets:** `YOUTUBE_API_KEY`, `OPENAI_API_KEY` (Whisper), X API key — via `agents/shared/settings.py` (pydantic-settings), never hardcoded/logged (CLAUDE.md).
- **Server-side only** — these run in the Python worker / Trigger.dev, never on device.

## Sub-phases

### Sub-phase 1: YouTube + podcast adapters (port)
- **Files touched:** `agents/ingestion/adapters/youtube.py`, `agents/ingestion/adapters/podcast.py`, `agents/ingestion/podcast_audio.py`, `agents/ingestion/adapters/__init__.py` (register in `get_adapter()`), `agents/shared/settings.py` (add keys if absent).
- **What ships:** the ported YouTube adapter (fresh-upload detection via uploads playlist + `playlistItems.list`; captions-only transcription via `youtube-transcript-api`; traction score) and podcast adapter (`feedparser` RSS + duration cap + **daily transcription budget**; `podcast_audio.py` stream-download → chunk → Whisper → concat with cost estimate), both implementing `adapters/base.py` and returning `content_source_items`-shaped records.
- **Definition of done:** given a channel + `since` cutoff, the YouTube adapter returns new video items with transcripts and skips caption-less videos as `failed` (YouTube API **mocked**); given a fixture RSS, the podcast adapter returns episodes within the duration cap and stops at the daily budget, with transcripts (Whisper **mocked**); both pass the `base.py` interface contract. Pytest, external APIs mocked (CLAUDE.md).
- **Dependencies:** Phase 5b SP1 (schema).

### Sub-phase 2: X/Twitter adapter (build fresh)
- **Files touched:** `agents/ingestion/adapters/x_account.py`, `agents/ingestion/models.py` (add `TwitterContentMetadata`).
- **What ships:** a new adapter polling followed X handles for recent posts via the chosen X API, normalizing each post to a `content_source_items`-shaped record with `platform_metadata` carrying the `TwitterContentMetadata` shape (tweet id, author, thread/quote refs), conforming to `adapters/base.py`.
- **Definition of done:** given a handle + cutoff, returns recent posts as `content_source_items` records (X API **mocked**); on rate-limit/no-auth it returns a clean `failed` status and logs a structured error with `fix_suggestion` (no crash, no secret leak). Pytest mocked.
- **Dependencies:** Phase 5b SP1.

### Sub-phase 3: Source pipeline + cadence + promote-to-story-pool
- **Files touched:** `agents/ingestion/source_pipeline.py` (new), `agents/ingestion/scheduler.py` (new — port `CadenceScheduler`), `agents/ingestion/dedup.py` (extend), `agents/pipeline/produce_gate.py` (extend to accept source-origin candidates).
- **What ships:** `run_source_ingestion(user)` — fetch the user's active `user_content_sources` → cadence-filter (YouTube 6h / podcast 12h / X 6h, configurable) → dispatch adapter → dedup (extend `dedup.py` for source items) → upsert `content_source_items` → **promote** substantive items into the deduped story pool tagged to that user (so `produce_gate`/`orchestrator` treat them as story candidates exactly like news).
- **Definition of done:** a followed channel's new upload becomes a story-pool candidate tagged to the user (adapters **mocked**); the cadence scheduler prevents re-fetch within the window; dedup drops an item already ingested; a promoted item flows through `produce_gate` like a news candidate. Pytest with mocked adapters + test DB.
- **Dependencies:** Sub-phases 1, 2.

### Sub-phase 4: Trigger.dev source-ingestion cron
- **Files touched:** `trigger/sourceIngestion.ts` (new), `trigger.config.ts` (if registration needed).
- **What ships:** a Trigger.dev **v4 `schedules.task`** (e.g. `cron: "0 */2 * * *"`) that lists users with ≥1 active source and fans out `run_source_ingestion` per user (port `ingestion-cron.ts` pattern), with structured per-run logging of items fetched/promoted/dropped (no silent caps).
- **Definition of done:** the task is a valid v4 `schedules.task` (uses `@trigger.dev/sdk` v4 `schedules.task`, **never** `client.defineJob`); a local/dev trigger fans out per-user ingestion and writes `content_source_items` (against a test DB or mocked worker call); the cron expression is validated; the run logs counts. ⚠ going live makes outward API calls — keep gated to test/dev until M5 deploy.
- **Dependencies:** Sub-phase 3.

## Phase-level definition of done
On a schedule, each user's followed YouTube/podcast/X sources are polled by cadence, fresh content is fetched + transcribed (captions for YT, Whisper for podcasts, posts for X), deduped, upserted to `content_source_items`, and promoted into the per-user story pool feeding the existing produce pipeline. **Validated by:** the three adapter tests (mocked APIs, incl. caption-less + rate-limit failure paths); the source-pipeline cadence + dedup + promote test; the v4 cron validity + fan-out test.

## Out of scope
- The **control surface** allocation (Phase 5e) — this fills the pool; 5e decides how many slots each source claims.
- The **recommendation/onboarding** UI (Phase 5c).
- Periodic **catalog refresh** / discovery (Phase 6).
- Producing the actual digest (audio/caption/poster) — reuses the **existing** `agents/pipeline` unchanged; this phase only feeds it candidates.

## Open questions
1. **X API** — which one, cost, rate limits, auth model (resolves 5c open Q3 too).
2. **Promotion criterion** — what makes a source item "substantive" enough to become a story (length? engagement? topic match?) — ties to 5e's "Only their big stuff".
3. **Transcription cost budget** tuning per source type (donor defaults: podcast 10 episodes/day).
4. **Dedup across axes** — a story covered by both a followed source and topic news: dedupe to the source-origin (per Decision #11 pinned-first); confirm here.

## Self-critique
**Product lens:** PASS — delivers spec §4 (followed sources flow into the same 30-story pipeline). The "Gavin Baker problem" payoff (following the right people surfaces their content automatically) lands here. Reuses the existing digest pipeline so source stories feel identical to news stories.
**Engineering lens:** PASS — ports proven donor ingestion (Decision #12), conforms new adapters to the existing `base.py` interface (read existing code first, Rule 8), and drops Pinecone per the in-context-grounding decision rather than blindly porting. Trigger task is held to the v4 `schedules.task` constraint (CLAUDE.md). DoDs are pytest-verifiable with mocked externals. SP4 (cron) lands last and is gated.
**Risk lens:** PASS with flags. No schema drops (additive code; tables from 5b). ⚠ SP4 cron makes **outward API calls** when live — flagged to stay gated until deploy. Within-phase overlap: `dedup.py` (SP3), `models.py` (SP2), `adapters/__init__.py` (SP1) — distinct sub-phases, no parallel edit. External-API + cost risk surfaced as open questions, not hidden. Test coverage includes the failure paths (caption-less video, X rate-limit) per Rule 9 — a test that fails if errors are swallowed.
**Irreversible sub-phases:** none (additive code; the live cron is operationally significant but reversible — disable the schedule).
