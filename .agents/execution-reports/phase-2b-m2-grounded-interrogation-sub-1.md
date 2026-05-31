# Phase 2b — Sub-phase 1 execution report

**Sub-phase:** Load a story's grounding corpus into a citeable context block (no vector DB)
**Status:** SUCCESS
**Date:** 2026-05-31

## What I implemented

A per-story grounding-corpus loader (no chunking / embedding / vector store — the
re-scope's in-context approach). `load_grounding_corpus(story_id, supabase_client)`
reads a SINGLE story's grounding text via Supabase-direct, `story_id`-filtered,
server-ordered reads and assembles a labeled, citeable, bounded `GroundingCorpus`:

- `detail_chunks` ordered by `chunk_index` → the body-spine passages.
- `story_timeline` ordered by `timeline_event_index` → "when: what" passages.
- the current digest's `caption_sentences` (joined in `sentence_index` order) → one
  narration passage (this is the in-DB digest script; there is no separate script blob).
- `story_sources` → the citation manifest (outlet name + article URL + lean) AND the
  primary-outlet attribution for body/digest passages.

Each passage carries a stable `passage_id` (`"<origin>:<index>"`, e.g. `detail_chunk:0`)
so SP2's answer model can cite the exact passage and the verifier can re-locate it.
`GroundingCorpus.render_context_block()` emits the `[passage_id] text` block SP2 wraps
in its constrain-to-context system prompt.

**Bounded budget (Rule 12):** the assembled corpus's total char count is asserted
against `DEFAULT_CORPUS_CHAR_BUDGET = 24_000`; over-budget raises
`CorpusBudgetExceededError` (fail loud, never truncate). A story with NO grounding
passages at all raises `GroundingCorpusError`.

The Supabase client is **injected** (same pattern as `agents/pipeline/persist.py`) —
the module never reads a secret and the test suite never touches the network.

## Files created / modified

Created:
- `agents/qa/__init__.py`
- `agents/qa/models.py` — `PassageOriginKind`, `GroundingPassage`, `CitationTarget`, `GroundingCorpus` (Pydantic v2)
- `agents/qa/corpus.py` — `load_grounding_corpus(...)` + private per-table readers
- `tests/agents/qa/__init__.py`
- `tests/agents/qa/conftest.py` — `FakeSupabaseClient` boundary mock (records reads; enforces the `.eq(story_id)` filter)
- `tests/agents/qa/test_corpus.py` — 12 tests

Modified (additive only):
- `agents/shared/exceptions.py` — appended `GroundingCorpusError` + `CorpusBudgetExceededError` (no existing code touched)

Did NOT touch: `src/types/detail.ts`, anything under `agents/pipeline/`, `agents/shared/{settings,logger}.py`.

## Divergences (and why)

- **Digest script source.** The phase says "the digest script". There is no standalone
  script table; the persisted digest narration lives in `caption_sentences.sentence_text`
  (one current digest per story via the partial unique index). I join those sentences in
  `sentence_index` order into one `digest_script:0` passage. This is the faithful in-DB
  representation of the digest script.
- **Single-source body.** M1's persist (`build_detail_chunk_rows`) paragraph-splits the
  canonical single-source body INTO `detail_chunks` — so in the live data the single-source
  body IS the detail chunks; there is no separate body row to read in SP1. I defined a
  `PassageOriginKind.SOURCE_BODY` enum member as a forward-looking contract point but emit
  none (no source to read). Flagged for SP2 below.
- **`citation_targets` are attribution, not body text** (per `supabase-schema.md` §story_sources
  open-question #1) — they populate the chip outlet+URL; the passages carry the grounding text.

## Self-review findings + fixes

- **[fixed, medium]** First draft of `_load_citation_targets` left dead placeholder logic
  (an undefined `_primary_has_url` ref + a `pass`). Removed it; primary-outlet resolution is
  cleanly handled by `_resolve_primary_outlet_name` (first source with a URL, else first source).
- **[kept, low]** `PassageOriginKind.SOURCE_BODY` is defined but unused — intentional contract
  placeholder; documented in the docstring and flagged for SP2.
- **[ok]** Verbose naming, full type hints, Google-style docstrings with examples, structured
  JSON logging with `fix_suggestion` on the over-budget error — all per CLAUDE.md. All files
  < 500 LoC (corpus.py 361, models.py 237).

## Validation results

- `ruff check agents/qa tests/agents/qa` → **All checks passed!**
- `ruff format --check agents/qa tests/agents/qa` → **6 files already formatted**
- `pytest tests/agents/qa -q` → **12 passed in 0.06s**
- Sibling suites still collect after the exceptions edit (`tests/agents/ingestion` + `tests/agents/pipeline` → 129 tests collected).
- Did NOT run `next build` / `npm test` (shared tree; Python-only work) per the brief.

Tests cover: validated-Pydantic return; `chunk_index` ordering (fixture seeds 2,0,1);
citation manifest shape (outlet+URL, nullable URL); body/digest primary-outlet attribution;
timeline ordering + single joined digest passage; passage-id-labeled render block; bounded
budget; **only-this-story scope** (s2 decoy in every table must not leak — asserts on text,
citations, AND the recorded read-log story-id filter); failure (missing story → error); failure
(over-budget → error); edge (empty timeline still loads from chunks); edge (no sources → empty
manifest, unattributed body).

## Definition of done: PASS

`load_grounding_corpus('s1', mock_client)` returns a `GroundingCorpus` with all of s1's
`detail_chunks` as labeled passages in `chunk_index` order (`detail_chunk:0/1/2`), plus its
`story_sources` citation targets (`Reuters`+url, `CNN`+None); bounded (276 chars ≤ 24000);
only-s1 scope enforced (no DECOY leak) and proven by the test on the read-log filter. ruff +
pytest pass.

## Concerns for the orchestrator / corpus-shape contract SP2 consumes

1. **Public entry point:** `from agents.qa.corpus import load_grounding_corpus`
   — signature `load_grounding_corpus(story_id: str, supabase_client: Any, char_budget: int = 24_000) -> GroundingCorpus`.
   The client is **injected** (SP2's endpoint must build a service-role client and pass it).
2. **Context block for the prompt:** call `corpus.render_context_block()` → newline-joined
   `[<passage_id>] <text>` lines. The system prompt must forbid answering outside these
   passage ids. The model should cite by `passage_id`.
3. **Citations mapping:** `corpus.citation_targets: list[CitationTarget]` carries
   `source_outlet_name` + `source_article_url` (URL may be `None`) + `source_bias_lean`.
   These map onto `api-contracts.md` `AnswerCitation.source_url`. Body/digest passages also
   carry `source_outlet_name` (the primary outlet) so SP2 can trace a cited passage → an outlet
   chip even when the passage isn't a `story_sources` row.
4. **Fail-loud, not silent:** SP2 will see `GroundingCorpusError` (no grounding) /
   `CorpusBudgetExceededError` (over budget) — per the phase's "return HTTP 200 + graceful
   fallback" rule, SP2 must catch these at the endpoint boundary and return the refusal/fallback
   payload, not 500.
5. **`SOURCE_BODY` is reserved but unemitted** — if SP2 adds a raw single-source body read,
   emit passages with `origin_kind=PassageOriginKind.SOURCE_BODY` and id `source_body:<n>`.
6. **Verification (SP2):** the corpus is the ground-truth text the `verification` stage gates
   claims against; `render_context_block()` is the exact text to verify against.
