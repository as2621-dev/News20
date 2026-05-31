# Execution report — Phase 1d SP2: Produce-once gate → single-source script + verification

**Date:** 2026-05-31
**Status:** ✅ COMPLETE (mock-tested only; no live LLM, no cost, no writes)
**Phase file:** `plans/phase-1d-daily-content-pipeline.md` (SP2)

## What shipped
The SP2 produce-gate + single-source script/verification spine, fully unit-tested with the LLM mocked at the client boundary:

| File | Decision | Purpose |
|---|---|---|
| `agents/pipeline/json_utils.py` | PORT (verbatim) | 3-tier JSON extraction from LLM responses (no openai dep) |
| `agents/pipeline/models.py` | ADAPT | News20-native SP2 models: `DialogueTurn`, `DigestScript`, `ClaimVerification`, `VerificationReport`, `ProduceDecision` |
| `agents/pipeline/llm_clients.py` | ADAPT (minimal) | **Gemini-text-only** `LLMClient.call_gemini` (retry/backoff + structured logging kept) |
| `agents/pipeline/prompts.py` | ADAPT | `DIGEST_SCRIPTING_PROMPT` (single-source, ~140w/55s) + `DIGEST_VERIFICATION_PROMPT` (in-context grounding) |
| `agents/pipeline/produce_gate.py` | NEW | `evaluate_story_for_production` + `select_stories_to_produce` + importance/freshness scorers — pure over injected inputs |
| `agents/pipeline/stages/scripting.py` | ADAPT | `run_single_source_scripting`: one `CanonicalStory` → ALEX/JORDAN digest |
| `agents/pipeline/stages/verification.py` | PORT (retargeted) | `run_single_source_verification`: the hallucination guardrail |
| `agents/shared/exceptions.py` | edit (additive) | `VerificationHaltError` added |
| `tests/agents/pipeline/{conftest,test_produce_gate,test_scripting,test_verification}.py` | NEW | 23 SP2 tests, all mocked |

## DoD mapping (phase file SP2) — all PASS via a named test
1. ✅ **Produce-gate skips a story with a current digest** — `test_produce_gate.py::TestProduceGateSkips::test_skips_story_with_current_digest` (same story/tags/scores as the happy-path test; only `has_current_digest=True` differs → asserts `skip_reason == has_current_digest`; a skip-logic regression flips it and fails — Rule 9).
2. ✅ **Produce-gate skips a story with zero `story_interests`** — `test_produce_gate.py::TestProduceGateSkips::test_skips_story_with_zero_interests` (empty tags → `serves_no_active_interest`) + `test_interest_check_ignores_other_stories_tags` (another story's tags don't count).
3. ✅ **Scripting output is speaker-tagged (ALEX/JORDAN), within ~140w/55s, single-source-constrained** — `test_scripting.py::test_output_is_speaker_tagged_alex_jordan` + `test_word_count_and_duration_within_budget` (≤`MAX_WORDS`, ≤90s) + `test_prompt_forbids_outside_facts_and_carries_only_this_source` (captures the exact system prompt sent to Gemini; asserts the single-source rule text AND that only this one story's body is embedded — the locked Decision #4 guardrail actually reaches the model).
4. ✅ **`verification` flags an injected out-of-source claim** — `test_verification.py::test_unsupported_claim_marks_ungrounded_and_halts` (injected claim absent from the source → `VerificationHaltError`) + `test_unsupported_claim_report_when_not_raising` (`is_grounded=False`, counted) + `test_contradicted_claim_is_ungrounded`.

Also: ✅ Ruff `check` + `format --check` clean on `agents/pipeline tests/agents/pipeline`; ✅ every new agent file < 500 LoC (largest: `produce_gate.py` 291).

## Divergences from the written phase file (flagged — Rule 12/7)
1. **`llm_clients.py` is Gemini-text-only, NOT a full PORT.** The donor imports `openai` at module top and ships OpenAI + multi-speaker-TTS + Google-Search methods. `openai` is **not installed** in the agent venv and News20 has no OpenAI dependency — a verbatim port would be a hard ImportError + dead code (Rule 2). SP2's two stages need only `call_gemini`. TTS is SP3's reuse of M0's `agents/voice/gemini_tts.py`; web-search grounding is dropped because News20 grounds **in-context** (memory: `news20-qa-incontext-grounding`).
2. **`models.py` is News20-native, slimmed.** Donor models a multi-source/multi-story 12-min briefing (`RankedStory` w/ N sources, `BriefingScript` ~2050w, quality-gate + pipeline-state models). News20's unit is one canonical story → one ~55s single-source digest, so I shipped `DigestScript` (not `BriefingScript`) and dropped ranking/quality/pipeline-state models (ranking is SP3's different per-user scorer; the rest are out of scope — porting now = dead code).
3. **No Supabase I/O in scripting/verification.** The donor writes `briefing_scripts` / `briefing_quality_log`. SP2 is explicitly mock-only / no writes; News20 `Settings` has no `supabase_*` fields. Persistence is SP3 (`persist.py`).
4. **Verification dropped the cleanup/edit pass + `NEEDS_HEDGE` status.** News20's guardrail is binary publish/block on a single source (SUPPORTED vs UNSUPPORTED/CONTRADICTED), not a hedge-and-rewrite loop over a multi-source briefing. `VerificationHaltError` signature is News20-shaped (`unsupported_count`/`contradicted_count`).
5. **`scripting.py` parses JSON only** (dropped the donor's XML-dialogue parse path, which existed solely for the donor's verification XML round-trip we don't use).

## Review findings + fixes (self code-review, Step B/C)
- **[low, fixed]** Unused `import scripting` in `test_scripting.py` → removed (ruff F401).
- **[low, fixed]** Format drift vs the repo's de-facto 88-col ruff style → `ruff format` applied (whitespace/wrapping only).
- **[medium, verified-safe, no change]** `make_llm_client` fixture uses `LLMClient.__new__` to skip `__init__` (no `Settings()`/key needed). Confirmed both stages touch only `llm_client.call_gemini` (mocked), never `self.settings` — so the unset attribute is never read.
- **[low, by-design]** Verification fail-safe: an unparseable/garbled claim status coerces to UNSUPPORTED, never SUPPORTED (asserted by `test_unknown_status_coerced_to_unsupported`) — a guardrail must not wave a hallucination through on a malformed response.
- No critical/high findings. All error paths carry `fix_suggestion`; Google-style docstrings + type hints throughout; structured `structlog` logging.

## Validation output
- `pytest tests/agents/pipeline/` → **39 passed** (23 SP2 + 16 pre-existing forced_alignment in the same dir), 0 failures.
- `pytest tests/agents/` (full agent suite) → **88 passed**, 1 pre-existing unrelated pydub/audioop DeprecationWarning. No regressions (SP1 left 65 green; +23 SP2 = 88).
- `ruff check agents/pipeline tests/agents/pipeline` → All checks passed.
- `ruff format --check agents/pipeline tests/agents/pipeline` → 17 files already formatted.
- All SP2 imports resolve via a smoke import (no circular import).

## NOT done in SP2 (by design — deferred / out of scope)
- **No live Gemini call, no cost, no DB writes** — every test mocks `LLMClient.call_gemini`.
- The Supabase `digest_is_current` read that feeds `has_current_digest` is **injected** here; the actual read is SP3/SP4 glue (the gate is a pure function, mirroring SP1).
- Scripting does not regenerate when over-budget — it logs a `scripting_over_word_budget` warning and returns; SP3's orchestrator owns any tighten-and-retry decision (no silent truncation, which would break SP3 caption alignment).

## SP2 → SP3 contract (for the orchestrator)
SP3 consumes three SP2 entry points, all pure/async and mock-tested. (1) `select_stories_to_produce(stories, story_interest_tags, has_current_digest_lookup, now_utc=...)` filters the SP1 `IngestionResult.canonical_stories` pool to `(stories_to_produce, decisions)` — SP3 must build `has_current_digest_lookup: dict[story_id, bool]` from a Supabase read on `digests.digest_is_current` and pass it in. (2) For each surviving `CanonicalStory`, `await run_single_source_scripting(story, llm_client)` returns a `DigestScript` (ALEX/JORDAN `turns`, `word_count`, `estimated_duration_seconds`, `digest_story_id`, `source_url`) — this is the input to SP3's reused-M0 TTS + forced-alignment caption path. (3) `await run_single_source_verification(script, source_story, llm_client)` raises `VerificationHaltError` on an ungrounded digest (SP3 should catch → skip+log that story, do NOT publish) or returns a `VerificationReport(is_grounded=True)` to proceed. SP3 supplies a single shared `LLMClient()` (real Gemini key from `Settings`) and decides the over-budget regeneration policy. Open: the importance/freshness floor constants (`_DEFAULT_MIN_IMPORTANCE=0.05`, `_DEFAULT_MIN_FRESHNESS=0.10`, `_IMPORTANCE_SATURATION_OUTLET_COUNT=12`) are first-draft — confirm against the SP4 2-user manual run.

## Next
**SP3 — Per-user scoring + fallback tree + orchestrator/persist.** ⚠ irreversible: real Gemini TTS/image + Supabase writes — checkpoint with the owner before spending.
