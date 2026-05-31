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
- [ ] 2: Produce-once gate → single-source script + verification — PENDING (next; still mock-tested / no cost)
- [ ] 3: Per-user scoring + fallback tree + orchestrator/persist — PENDING (⚠ irreversible: real TTS/image/DB — will checkpoint before spending)
- [ ] 4: Profile-update job + per-user allocation → daily_feeds + Trigger.dev fan-out — PENDING (⚠ irreversible: cron + paid API + writes; needs live news source)

## Open questions resolved by orchestrator (defaults, low-risk)
- Q1 caption timing: REUSE M0 time-slice path (phase recommendation). No real forced alignment in M1.
- Q3 execution host: SP4 wires Trigger.dev v4 `schedules.task` → `batchTrigger` per the phase; heavy Python steps run inline for the manual M1 run. Flag at SP4.
- Q4 allocation constants: confirmed via the SP4 2-user manual run (first-draft constants).

## Awaiting user decision
- News source for live ingestion (see Step 0). SP1 build target depends on it.
- Budget ack for paid Gemini TTS/image + Supabase writes (SP3 ~1 story; SP4 ≥10 stories). "Run phase 1d" taken as authorization for the modest flagged side-effects unless the user says otherwise.
