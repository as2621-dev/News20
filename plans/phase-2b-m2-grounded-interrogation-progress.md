# Progress: phase-2b-m2-grounded-interrogation

**Phase file:** plans/phase-2b-m2-grounded-interrogation.md
**Started:** 2026-05-31
**Phase-diff baseline commit:** de4f0da
**Execution mode:** PARALLEL with phase-2c (disjoint file sets, same working tree). Internally strictly sequential: SP1→SP2→SP3→SP4.

## Prereqs (verified)
- Phase 2 `StoryDetail` shell shipped (commit `0e76d50`): `src/components/detail/StoryDetail.tsx` present — SP3 mounts the composer into it.
- Grounding corpus seeded by Phase 1b (`detail_chunks`, `story_timeline`, `story_sources`, digest) — SP1 reads it.
- `agents/pipeline/stages/verification.py` exists (1d SP2) — SP2 REUSES by import; must not modify its existing signatures.

## Cross-phase guardrails (enforced in every sub-agent prompt)
- **Do NOT edit `src/types/detail.ts`** (phase-2c owns it) — put the `QuestionAnswer` TS type in a NEW `src/types/qa.ts` (or inside `src/lib/qa/`).
- **Do NOT edit `agents/pipeline/*`** (phase-2c territory) — except importing the existing `verification.py` read-only. 2b's verification lives under `agents/qa/` if a variant is needed.
- 2b owns: `agents/qa/*`, `agents/worker/main.py`, `src/components/detail/Qa*.tsx`, `src/lib/qa/askQuestion.ts`, `src/types/qa.ts`, and the mount edit in `src/components/detail/StoryDetail.tsx` (2b-only).

## Sub-phase progress
- [x] 1: Load a story's grounding corpus into a citeable context block (no vector DB) — **COMPLETE 2026-05-31** (12 tests pass, ruff clean, only-s1 scope proven w/ s2 decoy). Files: `agents/qa/{__init__,models,corpus}.py` + `agents/shared/exceptions.py` (+GroundingCorpusError/CorpusBudgetExceededError) + `tests/agents/qa/*`. Report: sub-1.md.
  - **SP2 contract:** `load_grounding_corpus(story_id, supabase_client, char_budget=24_000) -> GroundingCorpus` (client INJECTED, like persist.py). `corpus.render_context_block()` → `[<passage_id>] <text>` lines; cite by passage_id. `corpus.citation_targets` carry source_outlet_name + source_article_url + source_bias_lean → map onto AnswerCitation.source_url. Raises `GroundingCorpusError`/`CorpusBudgetExceededError` — SP2 catches at boundary, returns refusal/fallback (HTTP 200), never 500.
- [x] 2: Grounded answer endpoint + verification + refusal contract — **COMPLETE 2026-05-31** (29 tests pass, ruff clean; zero-tolerance test verified — verifier downgrades confident answer → refusal, never surfaced as grounded). Files: `agents/qa/{prompts,agent,verification}.py`, `agents/qa/models.py` (+AnswerCitation/QuestionAnswer), `agents/worker/{__init__,main,corpus_cache}.py`, `requirements.txt` (+fastapi 0.136.3/uvicorn 0.48.0 — CSO flag), `tests/agents/qa/{test_agent,test_verification,test_worker}.py`. Report: sub-2.md.
  - **SP3 contract:** `POST /api/story/{story_id}/question` body `{question_text, conversation_id?}` → ALWAYS HTTP 200 `QuestionAnswer {answer_text, answer_citations[], answer_is_grounded}`. `AnswerCitation` = `{source_url, source_quote, source_outlet_name, passage_id}`. `answer_is_grounded=false` → fixed refusal copy + empty citations → SP3 renders `⌀ CAN'T ANSWER FROM SOURCE`, never a bubble.
  - **SP4 note:** layer `story_qa` cache around the route; map `answer_is_grounded→qa_is_grounded`, citation outlets→`qa_citation_outlet_names`, `qa_source_kind='rag_cached'`; persist refusals too.
  - **Verification decision:** NEW `agents/qa/verification.py` (not the pipeline verifier — different shape); reused json_utils/LLMClient read-only.
- [x] 3: Q&A frontend — composer + thread + suggested chips + citation/refusal — **COMPLETE 2026-05-31** (tsc 0, biome 72 files clean, vitest 136 pass /17 files (10 new), next build OK). Refusal invariant enforced twice (askQuestion drops citations + QaThread render branch). Files: `src/types/qa.ts` (new), `src/lib/qa/askQuestion.ts` (new), `src/components/detail/{QaComposer,QaThread,SuggestedQuestionChips}.tsx` (new), `src/components/detail/StoryDetail.tsx` (additive mount + story-switch stale-guard), `.env.example` (+NEXT_PUBLIC_QA_API_BASE_URL), `tests/lib/qa/*`. Report: sub-3.md.
  - **Pending-human:** real-device swipe/tap feel (keyboard-avoidance, chip-row scroll) — owner device check, not faked.
  - **Deploy note:** set `NEXT_PUBLIC_QA_API_BASE_URL` to the worker origin for Capacitor (no same-origin server); worker CORS must allow the app origin.
- [x] 4: Persist + cache verified turns to story_qa — **COMPLETE 2026-05-31** (34 qa tests pass, ruff clean; cache-hit asserts `answer_question.call_count==1`, refusals cached, write-failure falls through). Files: `agents/qa/models.py` (+StoryQaCacheRow), `agents/worker/main.py` (cache read/write helpers + route), `tests/agents/qa/test_qa_cache.py`. Report: sub-4.md.
  - **Known limit:** cache re-serve maps outlet names only (`qa_citation_outlet_names`) — per-citation `source_url`/`passage_id` are lost on a cache hit (schema has no column; fix needs a migration, out of scope). Live `story_qa` write is owner-gated smoke (mock-test satisfies DoD).

## Phase-level passes (all PASS)
- **DoD (code):** PASS — grounded answer w/ citation chips + `⌀ CAN'T ANSWER FROM SOURCE` refusal in the detail view; off-source never surfaces an answer bubble (enforced twice). Combined green: Python 217 · tsc 0 · biome 72 · vitest 136. **Live worker smoke (real Gemini + story_qa write) pending owner GO.**
- **Slop scan:** PASS — no TODO/console.log/dead code/swallowed errors (worker broad-excepts are the intentional HTTP-200 boundary). Accepted-with-justification: fastapi/uvicorn dep (required; maintained).
- **CSO:** PASS — no secrets/injection/auth issues. 2 MEDIUM deploy-time follow-ups logged → `.agents/cso-findings/phase-2b-2c-m2.md` (public-endpoint rate limiting; worker CORS scoped to app origin).
- **Status: COMPLETE — committed.**

## Gated (need explicit owner GO — paid/irreversible)
- **SP4 live `story_qa` cache write** (service-role write, cheap content rows) — low risk; flag at SP4.
- Any live LLM call for an end-to-end smoke (paid) — mock-test by default; live smoke needs owner GO.
</content>
