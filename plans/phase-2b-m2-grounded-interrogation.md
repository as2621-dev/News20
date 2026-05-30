# Phase 2b: Grounded Interrogation — RAG Q&A + verification + composer

**Milestone:** M2 — Detail View + trust + interrogation
**Status:** Not started
**Estimated effort:** L

## Goal
A user types a question into the Detail search box and gets a **source-grounded** answer with citation chips — or a clean `⌀ CAN'T ANSWER FROM SOURCE` refusal when off-source — powered by the ported TLDW RAG retriever + verification stage, scoped to the active story's grounding corpus. This RAG answer API is the shared brain M3's voice mode will later reuse.

> **⚠ Prerequisites (Rule 12):** (a) **Phase 2** ships the `StoryDetail` shell that the composer mounts into. (b) **M1** delivers the Supabase client + the Python worker (FastAPI → Railway/Fly, `prototype-port-map.md` §6) reachable from the static SPA over HTTPS, plus a Pinecone project (`reference/integrations.md`). (c) The active story's **grounding corpus text** must be seeded — see Open question #1 (resolved: index `detail_chunks` + any available `story_sources` body text; `detail_chunks` is already seeded by M1 and is sufficient for the M2 demo). **M1 is NOT YET PLANNED** — satisfy worker + Pinecone prereqs before running.

## Sub-phases

### Sub-phase 1: Port RAG indexing for a story's grounding corpus
- **Files touched:** `agents/rag/{chunker,embedder,retriever,pinecone_client,pipeline,models}.py` (**PORT** from TLDW per `reference/reuse-map.md`), `agents/rag/index_story_corpus.py` (new thin wrapper), `agents/shared/{settings,logger,exceptions}.py` (**PORT** if not already present from M1)
- **What ships:** TLDW `agents/rag/*` ported verbatim then a wrapper that takes a `story_id`, reads its grounding corpus (`detail_chunks` body text + any `story_sources` article text), chunks → embeds → upserts to Pinecone **scoped by `story_id`** (namespace or metadata filter) so retrieval is per-story, never whole-briefing.
- **Definition of done:** Indexing `'s1'` upserts vectors tagged with its `story_id`; a `retrieve(query, story_id)` returns top-K chunks **all** belonging to that story. Pytest mocks Pinecone + the embedder at the boundary (CLAUDE.md mocking) and asserts (i) the per-story scope filter is applied and (ii) the returned chunk shape is a validated Pydantic model — the test fails if retrieval ever leaks another story's chunk (Rule 9). `ruff check` + `ruff format` pass; ported TLDW tests pass after copy (Rule 8).
- **Dependencies:** none within phase. Prereq: Pinecone key; grounding corpus seeded (OQ#1).

### Sub-phase 2: Grounded answer endpoint + verification + refusal contract
- **Files touched:** `agents/chat/{agent,prompts,models}.py` (**ADAPT** from TLDW — ground on a single story's corpus), `agents/pipeline/stages/verification.py` (**PORT**), `agents/worker/main.py` (add route `POST /api/story/{story_id}/question`)
- **What ships:** The grounded Q&A endpoint: `retrieve` (SP1) → answer **constrained to retrieved context** (system prompt forbids answering without tool context, `prototype-port-map.md` §7) → `verification` stage gates the claim against the source → returns `QuestionAnswer { answer_text, answer_citations[], answer_is_grounded }` per `api-contracts.md`. Off-source / unsupported → `answer_is_grounded = false` + the refusal payload. Failures return HTTP 200 with a graceful fallback (never breaks the conversation) and log a typed `ErrorResponse` with `fix_suggestion`.
- **Definition of done:** An on-topic question (e.g. "Why does Hormuz matter?" for `s1`) returns `answer_is_grounded = true` with ≥1 citation whose `source_url`/outlet traces to that story's `story_sources`/corpus; an off-source question ("what's the weather?") returns `answer_is_grounded = false` with **no fabricated answer**. Pytest mocks the LLM + retriever and asserts the grounded vs refusal branch **and** citation provenance — the test fails if an ungrounded answer is ever surfaced as grounded (Rule 9; this is the brief's zero-tolerance accuracy guardrail, Open Q5 / Decision #5). `ruff` passes.
- **Dependencies:** Sub-phase 1. **⚠ This is the riskiest surface in M2** — the hallucination guardrail. Consider spiking it first if de-risking early.

### Sub-phase 3: Q&A frontend — composer + thread + suggested chips + citation/refusal contract
- **Files touched:** `src/components/detail/QaComposer.tsx`, `src/components/detail/QaThread.tsx`, `src/components/detail/SuggestedQuestionChips.tsx`, `src/lib/qa/askQuestion.ts`, `src/components/detail/StoryDetail.tsx` (mount the composer — Phase 2's file, edited here only; Phase 2b runs after Phase 2 completes, so no parallel conflict)
- **What ships:** The pinned bottom `QaComposer`; the `QaThread` rendering the `.dot-typing` thinking state → a grounded `.qa-bubble-a` with one `.cite-chip` per `answer_citations` entry, **or** the `.qa-refusal` blush card with the mono header `⌀ CAN'T ANSWER FROM SOURCE`; and `SuggestedQuestionChips` from `suggested_questions`. `askQuestion()` calls the SP2 endpoint and maps `answer_is_grounded` to the two visual states **byte-for-byte** (`prototype-port-map.md` §7 — this visual distinction is how users learn to trust the system; do not redesign it).
- **Definition of done:** Typing or tapping a suggested question shows the thinking state, then a grounded bubble with citation chips for an on-topic question, or the refusal card for an off-source one. Manual UI smoke + a component test asserting `answer_is_grounded = false` renders the refusal card and **never** an answer bubble (Rule 9).
- **Dependencies:** Sub-phase 2; Phase 2 SP2 (the `StoryDetail` shell).

### Sub-phase 4: Persist + cache verified turns to `story_qa`
- **Files touched:** `agents/worker/main.py` (cache write inside the endpoint), `agents/chat/models.py` (cache model)
- **What ships:** Verified answers persisted to `story_qa` (service-role write — `story_qa` is a **content** table, no auth needed) with `qa_is_grounded` preserved, `qa_source_kind = 'rag_cached'`, and citation outlet names into `qa_citation_outlet_names`; on a repeat of the same `(story_id, question)` the endpoint serves the cached verified row instead of re-running RAG + the LLM (`prototype-port-map.md` §7 mandates persisting turns; `supabase-schema.md` designed `story_qa` for exactly this parity/cache use).
- **Definition of done:** Asking a question writes one `story_qa` row with the correct `qa_is_grounded` flag + citation outlet names; asking the identical question again returns the cached row **without** a second LLM/retriever call (test asserts the cache-hit path is taken and the persisted grounded flag matches the live answer — Rule 9). `ruff` passes.
- **Dependencies:** Sub-phases 2 & 3.

## Phase-level definition of done
In the Detail view of a seeded story, a user types a question and receives a source-grounded answer with citation chips whose provenance traces to that story's grounding corpus; an off-source question yields the `⌀ CAN'T ANSWER FROM SOURCE` refusal — **never a fabricated fact**; verified turns persist to `story_qa` and repeat questions hit the cache. `/run-phase` validates: a grounded question and an off-source question against `s1` produce the two correct, visually-distinct states, and the off-source path never surfaces an answer bubble.

## Out of scope
- **Voice mode** (M3 mounts this same RAG brain over Gemini Live — `prototype-port-map.md` §7 "same brain… voice just streams").
- `player_signals` emission on `ask` — defers to **M3** (signals are `auth.uid()`-scoped; auth is M3; M2 stays auth-free). `story_qa` caching here is fine because `story_qa` is a service-role content table, not user-scoped.
- Multi-turn conversational memory beyond single-question grounding (the `conversation_id` field exists in `api-contracts.md`; M2's bar is single-turn grounded answers — multi-turn is an M3 stretch).
- News ingestion / fetching full external article bodies (M1 ingestion).

## Open questions
1. **Grounding-corpus source (resolved, confirm).** RAG needs *text*, but the prototype's `story_sources` carries outlet **names**, not article bodies. **Resolution:** index the story's `detail_chunks` (the readable body — already seeded by M1, the single-source article per Decision #4) plus any `story_sources` article text available; `detail_chunks` alone is sufficient for the M2 demo. Confirm this corpus choice before SP1. Production breadth (ingesting full external article bodies) is M1 ingestion, out of scope here.
2. **Provider split (`integrations.md`).** TLDW uses OpenAI for the LLM + embeddings; News20 also has Gemini in stack. Confirm the answer model + embedder provider before porting SP1/SP2 (affects the embedder client + Pinecone vector dimension).
3. **Worker hosting (M1 infra).** The RAG endpoint runs on the Python worker (FastAPI → Railway/Fly) reached from the static SPA over HTTPS. Confirm the worker is deployed by M1, or stand up a minimal worker as part of SP2.

## Self-critique

**Product lens:** PASS. Completes M2's "True when": *ask a question answered from the source*. This is the brief's **moat** (interrogate-in-place) and its zero-tolerance accuracy guardrail (Open Q5 / Decision #5), realized in SP2's verification + refusal contract. No scope creep — voice, `player_signals`, and multi-turn memory are explicitly deferred to M3. The riskiest assumption of the whole project (digest quality) is M0 and already gated; M2's *own* riskiest piece (hallucination) is SP2 and is built as early as the dependency chain allows, with a "spike first" note.

**Engineering lens:** PASS. No stack escape — RAG/worker/Pinecone/Vercel API are all in the master plan + `reuse-map.md`; the port is TLDW `agents/rag/*` + `chat/*` + `verification.py` (limit-new-code directive). Every DoD is fresh-context-checkable (scope-leak test, grounded-vs-refusal + provenance test, refusal-renders-no-answer test, cache-hit test). SP4 (cache) consumes the SP2 `QuestionAnswer` shape — it does not cement an API shape early. SP1 (index infra) and SP2 (answer/verify) are genuinely distinct, not the same thing.

**Risk lens:** PASS (mitigated). **File boundary:** within this phase no two sub-phases edit the same file (SP3 edits `askQuestion.ts` + components; SP4 edits the worker). SP3 edits Phase 2's `StoryDetail.tsx`, but Phase 2b runs in a separate `/run-phase` session after Phase 2's commit — sequential, no parallel conflict. **Reversibility:** Pinecone upserts (SP1) write to a shared external index — **scoped/namespaced by `story_id`** makes them reversible per story; `story_qa` cache rows are deletable content rows. No DB migrations (those are M1). Test coverage: every DoD carries a test; SP3 flagged manual UI smoke + a component test on the refusal branch (the trust-critical path).

**Irreversible sub-phases:** SP1 ⚠ writes vectors to a shared Pinecone index — reversible per story via the `story_id` scope; treat with index-hygiene care (delete-by-scope on re-index). No data-destructive or public-API-locking changes.
