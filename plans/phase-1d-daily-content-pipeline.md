# Phase 1d: Automated daily content pipeline → fresh feed

**Milestone:** M1 — Audio-first karaoke reel MVP
**Status:** Not started
**Estimated effort:** L

## Goal
A **Trigger.dev v4 daily pipeline** that ingests real news, ranks a finite daily set, scripts each as a single-source two-host digest, and runs it through the (M0-validated) TTS → caption-timing → poster stages, persisting each as a playable digest in Supabase — so the reel shows a **fresh, finite ~20–30-story daily briefing** instead of the 5 seeds.

## Context the sub-agents need
- **Heavy reuse** (`reference/reuse-map.md`): ingestion (`adapters/base.py`, `feed_utils.py`, `dedup.py`) = **PORT**; `ranking.py`, `scripting.py`, `orchestrator.py` = **ADAPT**; `verification.py`, LLM client/prompts/models = **PORT**. Donor at `~/TLDW-Phase2/tldw/voice-agent-dashboard/`; read before porting (Rule 8); don't touch TLDW `_legacy/`.
- **Already built in M0 (reuse, don't rebuild):** `agents/voice/gemini_tts.py` (anchor-duo TTS), `agents/pipeline/stages/forced_alignment.py` (the time-slice caption path), the poster generator under `agents/m0/` (Gemini `gemini-3-pro-image-preview`).
- **Persist target = `reference/supabase-schema.md`** (the Phase-1b schema). Caption JSON → `caption_sentences.word_tokens`.
- **Locked constraints:** single-source scripts (Decision #4) + the `verification` hallucination guardrail (Decision #5); Gemini TTS pins in `reference/stack-notes.md`; Trigger.dev **v4 only** (`schedules.task`/`task`/`batchTrigger` — **never** `client.defineJob`).
- **Caption timing:** reuse M0's time-slice path for M1 (master-plan Open Q7); real forced alignment is a flagged future upgrade.

## Sub-phases

### Sub-phase 1: News ingestion + dedup
- **Files touched:** `agents/ingestion/adapters/{base.py,feed_utils.py}` (PORT), `agents/ingestion/dedup.py` (PORT), `agents/ingestion/adapters/newsapi.py` (NEW — ≥1 real adapter against `base.py`), `agents/ingestion/pipeline.py` (ADAPT), `tests/agents/ingestion/{test_dedup.py,test_newsapi_adapter.py}` (mock HTTP).
- **What ships:** ingestion that fetches real news from ≥1 source and dedups across sources into clustered candidate stories with outlet attribution (feeds the later outlet-count/trust numbers).
- **Definition of done:** running ingestion against a **mocked** feed returns typed candidate stories; `dedup` merges near-duplicate items and counts covering outlets; unit tests assert adapter parsing + dedup clustering (mocked HTTP — no live key in tests). ⚠ live external API when run for real.
- **Dependencies:** none (within phase)

### Sub-phase 2: Rank + single-source script (+ verification guardrail)
- **Files touched:** `agents/pipeline/stages/ranking.py` (ADAPT — segment weighting), `agents/pipeline/stages/scripting.py` (ADAPT — one source article → ~50–55 s two-host ALEX/JORDAN dialogue, single-source), `agents/pipeline/stages/verification.py` (PORT — guardrail), `agents/pipeline/{llm_clients,prompts,models,json_utils}.py` (PORT), `tests/agents/pipeline/{test_ranking.py,test_scripting.py,test_verification.py}`.
- **What ships:** ranking that selects a finite daily set + a scripting stage producing a speaker-tagged, single-source, length-bounded digest script that passes the verification guardrail.
- **Definition of done:** unit tests (mocked LLM) assert: ranking orders/limits the set with segment weighting; scripting output is speaker-tagged (ALEX/JORDAN), within the ~140-word/55 s budget, and constrained to the single source; `verification` flags an injected out-of-source claim. Ruff passes; agent files < 500 LoC.
- **Dependencies:** Sub-phase 1

### Sub-phase 3: Per-story orchestrator + Supabase persistence
- **Files touched:** `agents/pipeline/orchestrator.py` (ADAPT — chain `script → TTS (reuse agents/voice/gemini_tts) → caption-timing (reuse agents/pipeline/stages/forced_alignment) → poster (reuse agents/m0 generator) → persist`), `agents/pipeline/persist.py` (NEW — `supabase-py` service-role writer: insert `story`/`digest`/`caption_sentences`/`detail_chunks`/`story_trust`/`story_sources`/`suggested_questions` + upload audio + poster to storage, mapping to `reference/supabase-schema.md`), `tests/agents/pipeline/{test_persist.py,test_orchestrator.py}`.
- **What ships:** a function that turns one ingested+scripted story into a **persisted, playable digest** (rows + storage assets) that `getFeed()` surfaces.
- **Definition of done:** running the orchestrator on one fixture story produces a Supabase story with a digest (audio URL resolves), `caption_sentences` (`word_tokens` with timings, one highlight/sentence), `poster_url` resolves, trust/detail rows; it appears via the Phase-1b `getFeed()` and plays in the reel. A unit test asserts the caption-JSON→`caption_sentences` persistence mapping is lossless. ⚠ writes data + uploads + calls paid TTS/image APIs when run for real.
- **Dependencies:** Sub-phase 2, **Phase 1b** (schema/storage)

### Sub-phase 4: Trigger.dev daily fan-out + finite feed assembly
- **Files touched:** `trigger.config.ts` (v4), `trigger/dailyDigest.ts` (`schedules.task` → ingest + rank, then fan-out per-story via `batchTrigger` to the orchestrator), `agents/pipeline/feed_assembly.py` (NEW — assign `feed_date` + `feed_position` as a finite world-tier-ordered set; set `FEED_TOTAL`), `tests/{trigger,agents/pipeline/test_feed_assembly.py}`.
- **What ships:** a scheduled daily job that produces today's finite, ordered ~20–30-story feed end-to-end; the reel renders it as `NN / NN`.
- **Definition of done:** triggering the task once (manually, dev) ingests real news and persists **≥N fresh digests** for today's `feed_date` (target ~20–30; **DoD floor ≥ 10**), ordered `01..N`; the reel shows today's fresh stories and ends at the All-caught-up screen at `N / N`. A unit test covers `feed_assembly` ordering + position assignment. ⚠ scheduled cron + paid API calls — run manually before enabling the schedule.
- **Dependencies:** Sub-phase 3

## Phase-level definition of done
Invoking the daily pipeline ingests real news → ranks a finite daily set → scripts (single-source, verified) → TTS + captions + poster → persists each as a playable digest in Supabase, and the Phase-1c reel shows today's **fresh, finite, ordered ~20–30-story briefing** (DoD floor ≥ 10 real digests) ending at the All-caught-up screen. **Automated:** ingestion/dedup, ranking/scripting/verification, persist-mapping, and feed-assembly unit tests (mocked external services) green; **one manual end-to-end batch run** produces the persisted daily feed. ⚠ contains a scheduled Trigger.dev task + paid TTS/image/news API calls + data writes.

## Out of scope
- The full target of 30 *polished* daily digests every day (this phase proves the mechanism at a verifiable floor; volume/quality tuning is ongoing).
- Personalization / interest weighting beyond a world-tier order (M3).
- Image sourcing beyond the reused M0 poster generator; YouTube/podcast ingestion (later phase).
- Remotion share-export (demoted, deferred).

## Open questions
1. **Caption timing source** (master-plan Open Q7): reuse M0's time-slice path or invest in real forced alignment for production captions? Recommend shipping time-slice for M1, flagging the upgrade.
2. **Paid-API budget + keys:** NewsAPI / Gemini TTS / Gemini image keys + daily quota — Gemini free-tier TTS was 10/day in M0; a ~20–30-story daily run needs paid quota. Confirm budget before enabling the schedule.
3. **Execution host** (master-plan Open Q5): Trigger.dev task vs the FastAPI worker for the heavy Python TTS/poster steps — decide where they run.

## Self-critique

**Product lens:** PASS. This is what makes M1 "true" — a fresh, finite daily feed (not 5 seeds). Single-source + verification keeps the brief's zero-hallucination guardrail (Decisions #4/#5). Stays within M1: world-tier ordering only, no personalization (M3), no Detail/voice. The 30/day volume is a target, not a hard DoD — the floor of ≥10 proves the mechanism without over-scoping (Rules 2/12).

**Engineering lens:** PASS. Every stage maps to a `reuse-map.md` decision (ingestion PORT, ranking/scripting ADAPT, TTS/align/poster reuse M0, Trigger.dev v4 `schedules.task` — never `defineJob`). DoDs are verifiable: mocked-service unit tests on each stage + a one-shot manual batch run. SP3 fixes the persistence mapping to `supabase-schema.md` (the Phase-1b contract) — conforming, not inventing. No two sub-phases overlap (ingest ≠ rank/script ≠ orchestrate/persist ≠ schedule/assemble).

**Risk lens:** PASS with flags. ⚠ side-effecting/irreversible: SP1 hits a live news API, SP3 writes data + calls paid TTS/image APIs, SP4 registers a scheduled cron — all flagged; tests mock externals and the real run is a deliberate manual one before enabling the schedule. File boundaries disjoint across `agents/ingestion`, `agents/pipeline/stages`, orchestrator/persist, and `trigger/`. Painting-into-a-corner: SP1→4 — ingestion feeds ranking/scripting, which feeds orchestrate+persist, which the scheduler fans out; each consumes the prior's typed output, so SP4 works given SP1–3.

**Irreversible sub-phases:** SP3 (data writes + paid API calls), SP4 (scheduled cron + paid API calls); SP1 has live-API side-effects when run for real.
