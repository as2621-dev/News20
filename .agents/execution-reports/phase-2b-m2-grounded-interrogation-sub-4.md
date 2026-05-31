# Phase 2b — Sub-phase 4 execution report

**Sub-phase:** Persist + cache verified turns to `story_qa`
**Status:** SUCCESS
**Date:** 2026-05-31

## What I implemented

The SP4 verified-answer cache, layered ON TOP of SP2's per-story corpus context
cache (a different thing — the corpus cache skips Supabase reads + assembly; this
skips the whole LLM + verification answer round-trip). Flow added around the
existing `answer_question` call in `agents/worker/main.py::post_story_question`:

1. **Cache READ** — after the service-role client is built, BEFORE the corpus
   load, look up `story_qa` by the exact `(qa_story_id, qa_question_text)` UNIQUE
   pair. On a HIT, map the cached row back to `QuestionAnswer` and return it
   immediately — no corpus load, no LLM, no verification.
2. **Cache WRITE** — on a MISS, after `answer_question` returns the live answer,
   persist ONE `story_qa` row (service-role INSERT). `qa_is_grounded` preserved
   from `answer_is_grounded`; citation `source_outlet_name`s flattened into
   `qa_citation_outlet_names`; `qa_source_kind='rag_cached'` (legacy enum label —
   no migration). **Refusals are persisted too** (a verified refusal is a
   cacheable result, so a repeat off-source ask is cheap).

Both the read and write are **best-effort at the boundary**: a read/write failure
logs a typed `ErrorResponse` (with `fix_suggestion`) and falls through to the
live answer — the endpoint's HTTP-200-always / never-break-the-conversation
contract is preserved.

## Files created / modified

Modified (additive only):
- `agents/qa/models.py` — appended `StoryQaCacheRow` (the `story_qa` row shape)
  with `to_insert_payload()` (the INSERT dict), `from_question_answer()` (write
  side — preserves the grounded flag, flattens outlet names), and
  `to_question_answer()` (read side — re-serves grounded chips OR the
  byte-identical refusal contract). Added `from typing import Any`. Now 489 LoC.
- `agents/worker/main.py` — added `STORY_QA_TABLE` constant, `_read_cached_answer`
  + `_write_cached_answer` helpers (both boundary-safe), and wired them into the
  route (read before corpus load; write after the live answer). 313 LoC.

Created:
- `tests/agents/qa/test_qa_cache.py` — 5 SP4 tests with a `FakeStoryQaClient`
  boundary mock (Supabase `story_qa` read/write) + a mocked `answer_question`.

Did NOT touch: `agents/pipeline/**`, `src/**`, `supabase/**`. Imported `persist.py`
read-only for the service-role write pattern (matched its INSERT/`.execute()`
shape). No migration added.

## Design decisions

- **No second LLM/verification on a hit.** The cache read returns before the
  corpus load and the answerer, so a hit costs one indexed Supabase select and
  zero model calls. Proven by `answer_question` call-count == 1 across two
  identical requests.
- **Unexpected-answer-error refusals are NOT cached.** Only the verified result
  from `answer_question` (grounded answer or its canonical refusal) is persisted.
  A refusal returned from the route's *exception* fallback (e.g. Gemini exploded)
  is returned but deliberately not written — caching an infrastructure-error
  refusal would poison the cache against a transient failure. Verified refusals
  (off-source, downgraded-by-verifier) ARE cached, as intended.
- **Cached grounded chips re-serve outlet names only.** The cache stores outlet
  labels (`qa_citation_outlet_names`), not per-passage `source_url` / `passage_id`
  provenance — that is what the SP3 `.cite-chip` renders. A cached grounded row
  yields one `AnswerCitation` per stored outlet name (`passage_id="cache"`
  sentinel, `source_url=None`). See "Concerns" #1.
- **Cache key = the exact question text.** Matches the table's
  `(qa_story_id, qa_question_text)` UNIQUE constraint verbatim. No normalization
  (lower-casing / trimming) — an exact-match cache, consistent with the schema's
  unique key. See "Concerns" #2.

## Validation results

- `ruff check agents/qa agents/worker tests/agents/qa` → **All checks passed!**
- `ruff format --check agents/qa agents/worker` → **9 files already formatted**
  (formatter reflowed one line in `main.py` during the cycle; applied).
- `pytest tests/agents/qa -q` → **34 passed** (29 prior SP1/SP2 + 5 new SP4;
  1 cosmetic Starlette TestClient deprecation warning from the harness, not our
  code). No prior tests regressed (the SP4 additions are purely additive).
- Did NOT run `next build` / `npm test` (shared tree) per the brief. No live LLM
  calls, no live Supabase writes — all mocked at the boundary.

The 5 SP4 tests:
- **Write payload shape:** a grounded answer writes ONE `story_qa` row;
  asserts `qa_is_grounded=True`, `qa_source_kind='rag_cached'`,
  `qa_citation_outlet_names=['Reuters']`, and the cache-key columns.
- **Cache hit, no 2nd LLM call (Rule 9):** two identical requests →
  `answer_question.call_count == 1`, only the miss persisted, and the cached
  grounded flag + answer text MATCH the live answer (the cache cannot flip the
  verdict).
- **Refusal cached + re-served:** an off-source refusal writes
  `qa_is_grounded=False` + empty outlets; the repeat hits the cache (call-count
  stays 1) and re-serves the refusal (not grounded, no citations).
- **Write failure → still returns the answer:** `raise_on_write` → HTTP 200 +
  the grounded answer, nothing cached (best-effort fallback).
- **Read failure → falls through to live:** `raise_on_read` → HTTP 200 + live
  answer, answerer consulted (graceful fallback).

## Definition of done: PASS

- One `story_qa` row written with the correct `qa_is_grounded` flag + outlet
  names: **PASS** (write-payload-shape test).
- Identical repeat hits the cache WITHOUT a second LLM/verification call: **PASS**
  (asserted via `answer_question.call_count == 1`).
- Persisted grounded flag matches the live answer: **PASS** (cache-hit test
  asserts `cached.answer_is_grounded == live.answer_is_grounded`).
- Refusal cached + re-served: **PASS**. Cache-write/read failure → answer still
  returned: **PASS**.

**Live `story_qa` write against real Supabase = OPTIONAL owner-gated smoke**
(per the brief). The mock-test satisfies the DoD; the live smoke is GATED and was
NOT run (no live writes, per the hard constraint).

## Concerns / notes for the phase-level DoD + CSO

1. **Cached grounded chips lose per-citation `source_url` / `passage_id`.** The
   live answer carries full provenance (`source_url`, `source_quote`,
   `passage_id`); the cache persists only `qa_citation_outlet_names` (the schema's
   only citation column). So a CACHED grounded answer renders chips with the
   correct outlet labels but `source_url=None` and no per-chip quote. The SP3
   chip label still renders; the click-through URL is absent on cached re-serves.
   If the URL matters on cache hits, a future change would store the URLs (e.g. a
   parallel JSON column) — out of scope here (no migration; schema column shape is
   fixed). Flagged for CSO / SP3 awareness.
2. **Exact-match cache key (no normalization).** "Why does Hormuz matter?" and
   "why does hormuz matter" are distinct keys → a fresh LLM turn each. This
   matches the `(qa_story_id, qa_question_text)` UNIQUE constraint exactly and is
   the conservative choice (no risk of serving a wrong-question cached answer).
   Suggested-question chips (fixed text) always hit; free-text paraphrases may
   miss. Fine for the M2 demo; a normalization layer is a later optimization.
3. **First-writer-wins on a UNIQUE race.** Two concurrent identical first-asks
   both miss, both answer, both INSERT — the second INSERT violates
   `uq_story_qa` and raises. The write is best-effort (caught + logged), so the
   user still gets their (correct, freshly-verified) answer; only the redundant
   cache row is dropped. No correctness impact; a tiny double-LLM-spend window on
   first concurrent ask. Acceptable for M2; an upsert/`on_conflict` would close it
   if cost matters at scale.
4. **`agents/qa/models.py` is at 489 LoC** (under the 1000-line hard cap; the
   500-line "agent code" cap targets tool/prompt/orchestration files — this is a
   pure Pydantic models file). If it grows further, split the QA-answer models
   from the corpus models. Flagged, not acted on (Rule 3 — surgical).
5. The `story_qa` write reuses persist.py's service-role pattern but is a small
   single-row INSERT inside the worker (not routed through `persist_digest`),
   because the worker already holds its own service-role client and the persist
   module is digest-shaped. No `persist.py` edit (imported read-only for the
   pattern).
