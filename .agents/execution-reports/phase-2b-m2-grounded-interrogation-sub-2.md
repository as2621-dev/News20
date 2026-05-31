# Phase 2b — Sub-phase 2 execution report

**Sub-phase:** Grounded answer endpoint + verification + refusal contract
**Status:** SUCCESS
**Date:** 2026-05-31

## What I implemented

The grounded Q&A brain: load SP1's per-story corpus → answer constrained to the
in-context corpus → re-verify the answer against the corpus → return the typed
`QuestionAnswer`. An ungrounded/off-source/unverified answer is NEVER surfaced as
grounded — it becomes a refusal with no fabricated answer (Rule 9). The endpoint
returns HTTP 200 + a graceful refusal on EVERY failure (never a 5xx).

Flow (`agents/qa/agent.py::answer_question`):
1. `corpus.render_context_block()` → the `[passage_id] text` block.
2. Gemini answer call (`GROUNDED_ANSWER_PROMPT` forbids answering without the
   provided context; instructs citing by `passage_id`; instructs a clean
   refusal). Reuses the existing `LLMClient.call_gemini` (no new provider).
3. Parse `{answer, citations[], is_grounded}`. Refuse pre-verification if the
   model's `is_grounded` is false, the answer is empty, there are no citations,
   or no cited id maps to a REAL corpus passage (hallucinated ids dropped).
4. **Second guardrail:** `verify_answer_against_corpus` re-grades the answer text
   vs the corpus; a non-`supported` verdict (or any verifier error) DOWNGRADES to
   a refusal.
5. Map cited passage ids → `AnswerCitation` chips, resolving each cited passage's
   outlet + that outlet's `story_sources` URL, so every citation traces to that
   story's corpus/sources (provenance).

## Files created / modified

Created:
- `agents/qa/prompts.py` — `GROUNDED_ANSWER_PROMPT`, `ANSWER_VERIFICATION_PROMPT`, `REFUSAL_ANSWER_TEXT` (96 LoC).
- `agents/qa/agent.py` — `answer_question`, `build_refusal_answer`, `_map_citations`, `_parse_answer_response` (250 LoC).
- `agents/qa/verification.py` — `verify_answer_against_corpus` (the answer-vs-corpus gate; 117 LoC).
- `agents/worker/__init__.py`, `agents/worker/main.py` — FastAPI app + `POST /api/story/{story_id}/question` (183 LoC).
- `agents/worker/corpus_cache.py` — per-story corpus context cache + Gemini `CachedContent` hook (91 LoC).
- `tests/agents/qa/test_agent.py`, `tests/agents/qa/test_verification.py`, `tests/agents/qa/test_worker.py`.

Modified (additive only):
- `agents/qa/models.py` — appended `AnswerCitation` + `QuestionAnswer` (now 340 LoC).
- `tests/agents/qa/conftest.py` — added `s1_corpus` fixture (loads via SP1's real loader) + an autouse corpus-cache reset (no existing fixtures touched; SP1's 12 tests still pass).
- `requirements.txt` — added `fastapi>=0.115` + `uvicorn>=0.32` (flagged below).

Did NOT touch: anything under `agents/pipeline/` (imported `llm_clients`,
`json_utils`, `verification` read-only), `src/**`, or `src/types/detail.ts`. The
other files in `git diff --stat` (`agents/pipeline/models.py`, `src/types/detail.ts`,
`agents/pipeline/prompts.py`) are the concurrent phase-2c sibling agents' work — outside my file set.

## The verification decision (import vs new file): NEW FILE

I created `agents/qa/verification.py` rather than importing
`agents/pipeline/stages/verification.py`. The pipeline verifier's signature is
`run_single_source_verification(script: DigestScript, source_story: CanonicalStory, ...)`
— it classifies the claims of a multi-host dialogue script against a
`CanonicalStory.canonical_body_text`. The QA verifier needs a fundamentally
different shape: a single free-text *answer string* graded against the *rendered
corpus context block*. Forcing a `DigestScript`/`CanonicalStory` shape around a
Q&A answer would be a worse fit than a small purpose-built verifier. I kept the
donor's load-bearing pattern (grade against source text ONLY → fail SAFE on any
non-`supported`/garbled/error outcome) and reused its `json_utils` +
`LLMClient`. The pipeline file was imported read-only context, never edited.

## Caching decision

`agents/worker/corpus_cache.py` caches the **assembled `GroundingCorpus`**
in-process, keyed by `story_id` — a repeat question about the same story skips
all the Supabase reads + corpus assembly (this IS the per-story "prompt/context
cache" at the layer we control cheaply). I did NOT wire Gemini `CachedContent`:
the SDK exposes it, but it has a minimum token floor (a few thousand tokens) that
a News20 per-story corpus (per-story, single-source, "<100s read"; `s1` ≈ a few
hundred tokens) sits BELOW, so an explicit Gemini cache would be rejected for the
common case and add a create/delete/TTL lifecycle for no benefit (Rule 2). A
clear `# Reason:`-commented hook for `client.caches.create(...)` is left in the
file for the escape-hatch case (a corpus that outgrows the floor — the same case
that trips `CorpusBudgetExceededError`/retrieval). Flagged, not faked.

## Dependency additions (flag for CSO)

- **fastapi** (installed 0.136.3) — actively maintained, frequent releases (last
  release within weeks of 2026-05); large ecosystem; the standard ASGI web
  framework. Pinned `>=0.115`.
- **uvicorn** (installed 0.48.0) — the ASGI server FastAPI runs under; actively
  maintained. Pinned `>=0.32`.
- Both pulled transitive `starlette` + `annotated-doc` (already had `httpx`,
  `pydantic`). No other deps added. The static-export SPA cannot hold the Gemini
  key / run verification client-side, so a server endpoint is a hard prereq
  (phase file prereq b) — this is the minimal one.

## Self-review findings + fixes

- **[fixed, low]** First draft used `except (PipelineStageError, Exception)` in
  both `agent.py` and `verification.py` — redundant (Exception already catches
  PipelineStageError) and misleading. Collapsed to `except Exception` with a
  comment, and removed the now-unused `PipelineStageError` import from both.
- **[fixed, formatting]** `ruff format` reflowed one line in `agent.py`; applied.
- **[ok]** Verbose naming, full type hints, Google-style docstrings with
  examples, structured JSON logging with `fix_suggestion` on every error path.
  Worker logs the `ErrorResponse` shape (`error_code`/`error_message`/
  `error_details`/`timestamp_utc`/`fix_suggestion`) on every failure. All files
  < 500 LoC. Worker app imports cleanly with NO env vars (build-time safe;
  Supabase client built lazily at request time, key never logged).
- **[ok]** Refusal copy is a single constant (`REFUSAL_ANSWER_TEXT`) used by
  `build_refusal_answer()` — every refusal branch (off-topic, unsupported,
  verification-failed, corpus error, LLM error) returns a byte-identical payload.

## Validation results

- `ruff check agents/qa agents/worker tests/agents/qa` → **All checks passed!**
- `ruff format --check agents/qa agents/worker` → **9 files already formatted**
- `pytest tests/agents/qa -q` → **29 passed** (1 warning: Starlette's
  httpx-TestClient deprecation notice — cosmetic, not from our code).
- SP1 regression: `pytest tests/agents/qa/test_corpus.py` → **12 passed** (my
  conftest edit is additive).
- Did NOT run `next build` / `npm test` (shared tree) per the brief.

Tests cover (LLM mocked at `LLMClient.call_gemini`, no retriever to mock):
- **Grounded branch:** on-topic question → `answer_is_grounded=True`, ≥1 citation
  whose `passage_id`+outlet+`source_url` trace to s1's `story_sources` (Reuters +
  its real URL); every citation outlet ∈ s1's `story_sources` outlets.
- **Refusal branch:** off-source ("what's the weather?") → `is_grounded=False`,
  fixed refusal copy, ZERO citations, verifier never called.
- **THE zero-tolerance test:** a confident, well-formed, real-citation answer that
  the verifier rejects (`unsupported`) is DOWNGRADED to a refusal — never
  surfaced as grounded. Plus: hallucinated cited id → refusal; verifier
  fail-safe (empty answer / garbled / LLM-error all → not grounded).
- **Boundary (HTTP 200):** `GroundingCorpusError`, `CorpusBudgetExceededError`,
  missing Supabase config, and an unexpected answerer error each return HTTP 200
  + the refusal payload — never a 5xx. Happy path returns the grounded answer.

## Definition of done: PASS

- On-topic question → grounded with traced citation provenance: **PASS** (test
  asserts `passage_id`/outlet/URL trace to s1's `story_sources`).
- Off-source question → not grounded, no fabricated answer: **PASS**.
- Ungrounded-never-surfaced-as-grounded: **PASS** (verifier-downgrade test +
  hallucinated-citation test + fail-safe tests all assert refusal).
- Corpus-error → HTTP-200 refusal (not 500): **PASS**.
- LLM mocked, no live call: **PASS**.

## Endpoint contract SP3 calls + SP4 caches

**Route:** `POST /api/story/{story_id}/question`
**Request body** (`agents/worker/main.py::QuestionRequest`, matches
`api-contracts.md` `QuestionRequest`):
```json
{ "question_text": "Why does Hormuz matter?", "conversation_id": null }
```
`conversation_id` is reserved for M3 multi-turn; unused in M2.

**Response** (HTTP 200 always — `agents.qa.models.QuestionAnswer`, matches
`api-contracts.md`):
```json
{
  "answer_text": "...",
  "answer_citations": [
    { "source_url": "https://reuters.com/world/hormuz", "source_quote": "...",
      "source_outlet_name": "Reuters", "passage_id": "detail_chunk:0" }
  ],
  "answer_is_grounded": true
}
```
- `answer_is_grounded=false` → refusal: `answer_text` is the fixed refusal copy
  (`REFUSAL_ANSWER_TEXT`), `answer_citations` is `[]`. **SP3 maps this to the
  `⌀ CAN'T ANSWER FROM SOURCE` blush card and NEVER an answer bubble.**
- `AnswerCitation` extends the TS `{source_url, source_quote}` with
  `source_outlet_name` (chip label) + `passage_id` (provenance). SP3 renders one
  `.cite-chip` per entry.

**For SP4 (persist + cache):**
- Build the answer via `from agents.qa.agent import answer_question` and the
  corpus via `from agents.worker.corpus_cache import get_or_load_corpus`
  (`loader=load_grounding_corpus`) — both already wired in `post_story_question`.
- The SP4 `story_qa` cache layers ON TOP of the existing per-story corpus cache;
  wrap the persisted-turn lookup around the route before the `answer_question`
  call. Map `answer_is_grounded`→`qa_is_grounded`, citation
  `source_outlet_name`s→`qa_citation_outlet_names`, `qa_source_kind='rag_cached'`.
- `build_refusal_answer()` is the canonical refusal payload — SP4 should persist
  refusals too (so a repeat off-source question hits the cache) with
  `qa_is_grounded=false`.

## Concerns / notes for SP3 + SP4 + CSO

1. The worker reads `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` from the env at
   request time (same construction as the Phase 1d e2e script). These are not yet
   in `agents/shared/settings.py` (which only has Gemini keys); the worker reads
   them via `os.environ` directly, matching the existing e2e pattern. If a future
   phase wants them in `Settings`, that is an additive change.
2. The corpus cache is process-local and unbounded by distinct-stories-per-process
   — fine for the M2 demo's small story set; swap for an LRU / shared cache for a
   large catalog (flagged for CSO).
3. The Starlette TestClient deprecation warning (`install httpx2`) is cosmetic and
   from the test harness, not shipped code.
4. No `uvicorn` entrypoint/`if __name__` runner was added (the brief asked for a
   minimal app + route only); add `uvicorn agents.worker.main:app` to deploy.
