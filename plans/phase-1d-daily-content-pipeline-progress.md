# Progress: phase-1d-daily-content-pipeline

**Phase file:** plans/phase-1d-daily-content-pipeline.md
**Started:** 2026-05-31
**Phase-diff baseline commit:** 1ab2546 (docs: drop voice-agent onboarding, cancel phase-3c)
**Execution mode:** SEQUENTIAL — linear deps SP1→SP2→SP3→SP4; SP3 + SP4 are ⚠ irreversible (paid API + data writes), so no worktree parallelism (run-phase Step 2).

## Step 0 — preconditions
- ✅ Reference docs present: ranking-spec.md, reuse-map.md, stack-notes.md, supabase-schema.md
- ✅ Reused M0 modules present: agents/voice/gemini_tts.py, agents/pipeline/stages/forced_alignment.py, agents/m0/
- ✅ TLDW donor present: ~/TLDW-Phase2/tldw/voice-agent-dashboard
- ✅ Keys set: GEMINI_API_KEY (TTS+image), SUPABASE_SERVICE_ROLE_KEY (writes/uploads), TRIGGER_* (v4)
- ⚠️ **NO NewsAPI key.** Only news-capable key in .env is SERPER_API_KEY (Serper/Google Search).
- ✅ **SOURCE DECISION: GDELT DOC 2.0 API** (keyless) — validated live 2026-05-31.
  Probe: `"Iran war"` + `"Arsenal FC"` → HTTP 200, fresh (today-stamped) global articles, URL+domain+country+lang.
  Findings baked into SP1 adapter (`agents/ingestion/adapters/gdelt_doc.py`):
  • **Throttle ≤1 req / 5s** (429 observed at faster). • Use `sort=hybridrel` (datedesc = noise).
  • Returns metadata, NO body → add `trafilatura` fetch+extract (check TLDW ingestion first).
  • Cross-outlet repetition → dedup→outlet-count for trust/coverage (Decision #6). • timespan=1d–3d; max 250/query.
  GDELT-on-BigQuery DEFERRED to M2 trust layer (GKG themes/tone/coverage). Pending user GO to start SP1.

## Sub-phase progress
- [x] 1: Interest-keyed ingestion + dedup pool + ancestor tagging — **COMPLETE 2026-05-31** (mock-tested, no cost/writes/live-API). 37 tests pass, ruff clean, full agent suite 65 green. Report: `phase-1d-daily-content-pipeline-sub-1.md`.
  - Files: `agents/ingestion/{models,dedup,ancestor_tagging,interest_keyed_pipeline}.py` + `adapters/{base,gdelt_doc}.py`; `agents/shared/exceptions.py` (+IngestionError/AdapterFetchError); `requirements.txt` (+trafilatura); tests under `tests/agents/ingestion/`.
  - **Deviations from phase file (flagged):** (1) adapter = `gdelt_doc.py` not `newsapi.py` (Step-0 decision); (2) `dedup.py` = PORT primitives + ADAPT into `StoryClusterer` w/ outlet-count (donor only drops dupes); (3) `models.py` News20-native, not donor per-user `ContentItemRecord`; (4) **`feed_utils.py` deferred** — RSS-specific, GDELT is JSON, porting now = dead code (Rule 2), port when an RSS adapter lands; (5) +`trafilatura` dep.
  - **SP2 contract:** SP1 outputs `IngestionResult{canonical_stories, story_interest_tags, active_interests, total_candidates_fetched}` as row *payloads* — `CanonicalStory.canonical_body_text` (trafilatura) feeds SP2 scripting; `StoryInterestTag` rows persist in SP3; the Supabase loader for followed-ids + the `interests` tree is SP4 glue (SP1 is pure over injected inputs).
- [x] 2: Produce-once gate → single-source script + verification — **COMPLETE 2026-05-31** (mock-tested, no cost/writes). 23 SP2 tests pass; full agent suite 88 green; ruff clean; agent files <500 LoC. Report: `phase-1d-daily-content-pipeline-sub-2.md`.
  - Files: `agents/pipeline/{json_utils,llm_clients,models,prompts,produce_gate}.py` + `stages/{scripting,verification}.py`; `agents/shared/exceptions.py` (+VerificationHaltError); tests under `tests/agents/pipeline/`.
  - **Deviation (flagged):** `llm_clients.py` = Gemini-text-only, not a verbatim PORT — `openai` is absent from the venv; donor's OpenAI/TTS/web-search methods would be ImportError + dead code (Rule 2). TTS is SP3's M0 reuse; verification grounds in-context (no web search) per `news20-qa-incontext-grounding`. Models slimmed to News20's single-source `DigestScript`.
  - **SP3 contract:** 3 async mock-tested entry points — `select_stories_to_produce(canonical_stories, story_interest_tags, has_current_digest_lookup, now_utc=)` → `(to_produce, decisions)` (SP3 builds `has_current_digest_lookup` from a Supabase `digests.digest_is_current` read and injects it — gate is pure); `run_single_source_scripting(story, llm_client)` → `DigestScript` (ALEX/JORDAN turns + word_count + estimated_duration_seconds + digest_story_id + source_url) feeding M0 TTS + forced-alignment; `run_single_source_verification(script, source_story, llm_client)` raises `VerificationHaltError` (SP3 catches → skip+log, never publish) or returns `VerificationReport(is_grounded=True)`. SP3 owns the over-budget regeneration policy (SP2 only warns). Floor constants `MIN_IMPORTANCE=0.05`/`MIN_FRESHNESS=0.10`/`SATURATION_OUTLET_COUNT=12` are first-draft (confirm at SP4 2-user run).
- [x] 3: Per-user scoring + fallback tree + orchestrator/persist — **COMPLETE 2026-05-31** (⚠ irreversible run done — owner PRE-AUTHORIZED). 33 new tests (full suite 121 green); ruff clean; agent files <500 LoC.
  - Files: `agents/pipeline/{stages/ranking,persist,persist_helpers,orchestrator}.py`; tests `tests/agents/pipeline/{test_ranking,test_fallback_tree,test_persist,test_orchestrator}.py` + gated live script `sp3_e2e_fixture_run.py`; `requirements.txt` (+supabase, +python-dotenv).
  - **Live e2e evidence (orchestrator-verified, INSERT-only, one story):** `stories.story_id=FIXTURE-SP3-950c5e0f05a1`, `digests.digest_id=df2839ae-bf68-440d-8347-f6d52a593c6d`, 10 caption_sentences, 1 detail_chunks, 1 story_trust, 4 story_sources, 2 story_interests (depth 0+1), 2 suggested_questions. Audio URL → **HTTP 200, 962,924 B audio/mpeg**; poster URL → **HTTP 200, 1,976,879 B image/png** (re-confirmed via httpx by orchestrator). Spend ≈$0.16 (1 TTS + 1 image + ~2 text + Serper). ⚠ **This fixture row is still live in Supabase** — delete it when no longer needed (it has the `FIXTURE-SP3-` prefix; local poster artifact at `assets/m0/FIXTURE-SP3-950c5e0f05a1/`, untracked).
  - **Flagged divergences:** (1) trust derived from static outlet→bias table (no importable M2 Python module — M2 is read-only TS); (2) `story_segment_slug` defaults to `wildcard` — SP4 backfills from matched interest's `interest_segment_slug`; (3) live e2e under `tests/` (env-gated `RUN_LIVE_E2E=1`, not pytest-collected) not `scripts/`; (4) `persist` split into `persist_helpers.py` for <500 LoC; (5) fixed a real blindspot-derivation bug (mis-fired on balanced coverage).
  - **SP4 handoff:** consume `score_candidates_for_user(...) -> {followed_leaf_id: [ScoredCandidate]}` (each carries score/matched_interest_id/fallback_depth). Exploration (§3.7) is an allocator concern, intentionally NOT in the fallback generator. SP4 builds `interest_nodes`/`UserProfileInterest[]`/`has_current_digest_lookup` from Supabase, owns `daily_feeds`, confirms `T`/floor constants at the 2-user run.
- [ ] 4: Profile-update job + per-user allocation → daily_feeds + Trigger.dev fan-out — PENDING (⚠ irreversible: cron + paid API + writes; needs live news source)

## Open questions resolved by orchestrator (defaults, low-risk)
- Q1 caption timing: REUSE M0 time-slice path (phase recommendation). No real forced alignment in M1.
- Q3 execution host: SP4 wires Trigger.dev v4 `schedules.task` → `batchTrigger` per the phase; heavy Python steps run inline for the manual M1 run. Flag at SP4.
- Q4 allocation constants: confirmed via the SP4 2-user manual run (first-draft constants).

## Awaiting user decision
- News source for live ingestion (see Step 0). SP1 build target depends on it.
- Budget ack for paid Gemini TTS/image + Supabase writes (SP3 ~1 story; SP4 ≥10 stories). "Run phase 1d" taken as authorization for the modest flagged side-effects unless the user says otherwise.
