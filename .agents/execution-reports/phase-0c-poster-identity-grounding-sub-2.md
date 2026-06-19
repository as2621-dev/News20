# Phase 0c — Sub-phase 2: Real-name resolution in concept extraction (L1)

**Status:** SUCCESS
**Date:** 2026-06-18

## What I implemented

Made `extract_story_concept` resolve the SPECIFIC named person a story is about
as of the story's date — never a role/title, never the model's own (often stale)
prior of who holds an office. For multi-leader/event stories it resolves the
single primary named person.

1. **`agents/m0/poster_models.py`** — added two optional fields to `StoryConcept`:
   - `entity_key: str` (default `""`) — normalized (lowercased, whitespace-collapsed)
     form of `entity_name`, the lookup key into the SP1 reference-image store.
   - `entity_as_of: str | None` (default `None`) — ISO date the resolution is
     anchored to (the story's date).
   Both defaulted, so existing callers/serialization are unaffected.

2. **`agents/m0/story_concept.py`**:
   - Amended `_CONCEPT_INSTRUCTION` with an explicit IDENTITY-RESOLUTION rule:
     resolve role/title references (Fed chair, US president, PM, CEO) to the person
     NAMED IN THIS STORY'S TEXT as of `{story_date}`; never use the model's own
     knowledge of who holds the office; never return the role itself; for a
     summit/confrontation pick the single PRIMARY person.
   - Added params `story_body: str | None = None` and `story_date: str | None = None`
     (keyword-only, safe defaults). The full body + date are threaded into the
     prompt (`{story_date}` placeholder substituted; body appended as
     "Full story body:"). Body defaults to `summary` when not supplied — back-compat.
   - New pure helper `_normalize_entity_key` derives `entity_key` in CODE (the LLM
     is not trusted to normalize); `entity_as_of` is set to the supplied `story_date`.
     Both the success path and the headline fallback populate the new fields.
   - Logging extended with `story_date`, `entity_name`, `entity_key`.

3. **Callers updated (both inside `agents/m0`, allowed scope):**
   - `agents/m0/build_poster_from_news.py::build_poster_for_digest`
   - `agents/m0/batch_posters.py::prepare_poster_generation`
   Both now pass `story_body=summary, story_date=None`. The joined narration IS
   the full story body; `Digest` has NO date field (the date lives only inside
   the free-text `digest_source` citation), so `story_date=None` and resolution
   relies on the text — documented inline.

4. **`scripts/eval_entity_resolution.py`** (new) — runnable LLM eval over 4 inline
   fixtures, guarded behind `__main__`, repo-root `sys.path` bootstrap matching the
   existing scripts convention. Prints PASS/FAIL per fixture, exits non-zero on any
   failure.

5. **`tests/agents/m0/test_story_concept.py`** (new) — 10 mock-based unit tests
   (no network). Mocks the genai client and captures the prompt text.

## Files touched
- `agents/m0/poster_models.py`
- `agents/m0/story_concept.py`
- `agents/m0/build_poster_from_news.py` (caller — in allowed scope)
- `agents/m0/batch_posters.py` (caller — in allowed scope)
- `scripts/eval_entity_resolution.py` (new)
- `tests/agents/m0/test_story_concept.py` (new)

## Divergences from the brief
- **`story_date` is `None` at the call sites.** The brief asked to pass the story
  date "where callers already have it." Neither caller (`Digest`) carries a
  structured date field — only a free-text source citation. Rather than parse a
  date out of free text (out of scope / brittle), I thread `None`; the prompt
  handles unknown-date and resolution leans on the body text. The plumbing for a
  real date is in place (keyword param) the moment a dated story object exists.
- **`entity_key` normalized in code, not by the LLM.** The brief said "populate
  `entity_key` (normalized)". I compute it deterministically from the model's
  `entity_name` so the store key is stable regardless of LLM casing/spacing —
  more reliable and unit-testable.

## Self-review findings + fixes
- **Format churn (HIGH, fixed):** running `ruff format` initially reformatted many
  PRE-EXISTING `Field(...)` lines in `poster_models.py` (repo has no ruff config →
  88-col default; several existing lines were already non-conformant). Per Rule 3
  (surgical) I reverted and re-applied only my additions, then wrapped only the one
  long line I authored. Final diff: `poster_models.py` +11 (my 2 fields only), no
  churn on pre-existing code. Pre-existing non-conformant lines left untouched.
- Callers not broken: both still compile; defaults keep any other caller working
  (unit test `test_body_defaults_to_summary_when_not_supplied` proves it).
- Types correct, no `any`-equivalent, structlog logging present, Google-style
  docstrings with examples on both new/changed functions.

## Validation output
- **Ruff check** (all 6 touched files): `All checks passed!`
- **Ruff format --check**: my new files + the two callers + `story_concept.py` are
  clean. `poster_models.py` still reports a diff, but ONLY on pre-existing
  non-conformant lines (verified my `entity_key`/`entity_as_of` are NOT in the
  diff) — intentionally left untouched for surgicality.
- **Pytest** `tests/agents/m0/test_story_concept.py`: **10 passed in 0.96s.**
  Includes Rule-9 tests that FAIL if the body or date is dropped before the model
  (`test_story_body_is_in_the_prompt`, `test_story_date_is_in_the_prompt`).
- **Live LLM eval** `python scripts/eval_entity_resolution.py`: **RAN** (API key
  present in `.env`) → **4/4 fixtures PASS, exit 0**:
  - `fed_chair_named_person_not_powell`: entity_name=`'Kevin Warsh'` (not Powell, not "Fed chair")
  - `g7_summit_primary_leader_trump_not_biden`: entity_name=`'Donald Trump'` (not Biden)
  - `control_company_story`: entity_name=`'Nvidia'`
  - `control_single_named_person`: entity_name=`'Jensen Huang'`

## Definition of done: PASS
- Eval script asserts the two reported failures resolve correctly + 2 controls — ✅ exists and PASSED live.
- Mock unit test proves body + date are threaded into the prompt — ✅ exists, PASSES, and is written to fail if either is dropped (Rule 9).
- Live eval is LLM-dependent (flagged) — it RAN this session and passed 4/4.

## Concerns / follow-ups
- **No caller could not be updated.** Both `extract_story_concept` callers were in
  the allowed `agents/m0` scope and were updated. No out-of-scope caller edits were
  needed (grep confirmed only these two callers exist).
- **`story_date` is structurally unavailable at call sites** (see Divergences).
  Follow-up for a later sub-phase: when the pipeline carries a real per-story
  publication date, thread it into both call sites (the param already accepts it).
- **`poster_models.py` pre-existing format drift** (lines I did not author) remains
  non-conformant under ruff's 88-col default; left untouched per Rule 3. A separate
  housekeeping pass (or adding an explicit `line-length = 120` ruff config to match
  CLAUDE.md) would resolve it project-wide.
