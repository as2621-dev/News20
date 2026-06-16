# SP1 — Server: expose the corpus + a web-only answer path

**Status: SUCCESS**

## What I implemented

### 1. `GET /api/story/{story_id}/corpus` (agents/worker/main.py)
- New `StoryCorpusResponse` Pydantic model: `context_block: str`, `approx_token_count: int` (>=0).
- New `get_story_corpus` route. Reuses `get_or_load_corpus(story_id, supabase_client, loader=load_grounding_corpus)` so corpus assembly stays server-side and per-story cached.
- Populates from `corpus.render_context_block()` + `corpus.approx_token_count`.
- Graceful boundary mirroring `post_story_question`: catches `KeyError` (missing Supabase env), `GroundingCorpusError`, and any other `Exception` (which includes `CorpusBudgetExceededError`, exactly as the Q&A route handles it) — each logs a typed `ErrorResponse` via `_log_error_response` and returns HTTP 200 with `{context_block: "", approx_token_count: 0}`. Never 5xx.
- **CORS:** changed `allow_methods` from `["POST", "OPTIONS"]` to `["GET", "POST", "OPTIONS"]`. The app-wide `QA_API_ALLOWED_ORIGINS` middleware now covers this GET cross-origin (it did not before — GET was not an allowed method, so a browser cross-origin GET would have been blocked).
- **Rate limit:** left the GET OUT of throttling. The middleware only throttles `POST` requests on `_RATE_LIMITED_PREFIXES`; a GET is already excluded. Did NOT widen scope (cheap cached read, no LLM). Documented this in the route docstring.

### 2. Web-only mode on the answer path
- Added `web_only: bool = False` to `QuestionRequest` with a Field description (skip corpus answer+verify, answer from web only; used by the voice tool).
- Added public wrapper `answer_from_web_only(...)` to `agents/qa/agent.py` that delegates to the module-private `_answer_from_web(...)` with a clear docstring (preferred over importing the private symbol across modules, per the mission).
- In `post_story_question`: introduced `skip_answer_cache = has_conversation_context or request.web_only`. When `web_only` is True the answer cache is bypassed (read AND write) and `answer_from_web_only(...)` is called instead of `answer_question(...)`. Corpus is still loaded (needed for `_answer_from_web`'s relatedness context block). All existing graceful try/except boundaries preserved.
- `web_only=False` behavior is byte-identical to before (verified by the unchanged existing tests).

## Files modified
- `agents/worker/main.py`
- `agents/qa/agent.py`
- `tests/agents/qa/test_worker.py` (added tests)
- `tests/agents/qa/test_agent.py` (added tests)

NOTE: `agents/qa/models.py` was NOT modified — see divergence below.

## Divergences (and why)
- **`web_only` field location.** The mission said to add `web_only` to `QuestionRequest` in `agents/qa/models.py`. `QuestionRequest` does not live in `models.py`; it is defined in `agents/worker/main.py` (line ~135). I added the field to the actual definition in `main.py`. Net effect is identical; `models.py` is untouched.

## Self code-review findings + fixes
- **[Info] CorpusBudgetExceededError handling.** It is NOT a subclass of `GroundingCorpusError`, so it falls into the generic `except Exception` branch of the corpus endpoint → still HTTP 200 empty block. This exactly matches how the existing Q&A route handles it (only `GroundingCorpusError` is explicit there too). Consistent; no change needed.
- **[Low] Cache-bypass logging.** Added a dedicated `qa_cache_bypassed_web_only` info log so the bypass is observable, mirroring the existing `qa_cache_bypassed_conversation` log.
- No critical/high issues found. Graceful-200 contract holds on every failure path of both changes; type hints + Google-style docstrings + structured logging with `fix_suggestion` present.

## Validation results

`ruff check agents/qa/ agents/worker/ tests/agents/qa/`:
```
All checks passed!
```

`ruff format --check` (touched files) — after running `ruff format`:
```
5 files already formatted
```

`python -m pytest tests/agents/qa/test_worker.py tests/agents/qa/test_agent.py -q`:
```
...................................                                      [100%]
35 passed, 1 warning in 0.69s
```

Full qa suite `python -m pytest tests/agents/qa/ -q`:
```
....................................................................     [100%]
68 passed, 1 warning in 0.59s
```

Tests added:
- Corpus endpoint: happy path (rendered block + token count), GroundingCorpusError → empty block, unexpected error → empty block, missing Supabase config → empty block.
- web_only on the route: `web_only=True` calls `answer_from_web_only`, skips `answer_question` + cache read/write; `web_only=False` keeps `answer_question` + cache write, never touches the web wrapper.
- `answer_from_web_only` wrapper: related question → web answer (no `call_gemini`, one `call_gemini_with_search`); unrelated → off-topic pushback. LLM mocked at the `LLMClient` boundary.

## Definition of done: PASS
- GET `/api/story/{id}/corpus` returns rendered context block + token count; empty block (HTTP 200) on any failure. PASS
- `QuestionRequest.web_only=True` routes to web-only path, skipping corpus answer+verify AND the answer cache; `web_only=False` unchanged. PASS
- Lint + tests pass. PASS

## Concerns for the orchestrator
- **CORS change is load-bearing for SP2.** The client's `fetchStoryCorpus.ts` (SP2) calls this GET cross-origin; the `allow_methods` now includes `GET`. If any other GET routes existed that were intentionally same-origin-only, this widens them too — but the worker currently has no other GET routes, so impact is nil.
- **Did not modify `models.py`** despite the file being listed; `QuestionRequest` simply isn't there. Flagging so the orchestrator's commit doesn't expect a models.py diff.
- The `StarletteDeprecationWarning` (httpx/testclient) is pre-existing, unrelated to this change.
