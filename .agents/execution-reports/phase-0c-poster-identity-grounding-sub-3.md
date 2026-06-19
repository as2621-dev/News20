# Phase 0c — Sub-phase 3 execution report: L5 population (fetch + verify + cache canonical photos)

**Status:** SUCCESS
**Date:** 2026-06-18

## Implemented
`get_or_fetch_entity_reference_image(...)` — given a resolved person, returns a VERIFIED current
reference photo, fetching/verifying/uploading/caching it in the SP1 store, or `None` (writing
nothing) when no candidate clears the confidence threshold.

Behaviour:
1. **Cache hit (fresh):** lookup by `entity_key`; if `verified_at` is within `REFERENCE_REFRESH_DAYS`,
   return the row with ZERO network calls.
2. **Miss/stale:** SERP-search `"{entity_name}" {year}` (year from injected `current_year` or `as_of`),
   download candidates (reusing `download_candidate`), capped at `CANDIDATE_LIMIT`.
3. **Verify:** one Gemini-Flash multimodal pass per candidate (`GEMINI_LLM_MODEL`, mirrors
   `image_scorer._score_one` call style) returning `is_match` + `confidence`; keep highest-confidence match.
4. **Accept:** if best confidence `>= REFERENCE_MIN_CONFIDENCE`, upload bytes to the
   `entity-reference-images` bucket at `{entity_key}/reference.jpg` (mirrors `_upload_poster` upsert)
   and UPSERT the row `on_conflict="entity_key"`; return the `ReferenceImage`.
5. **Reject (safety guarantee):** nothing clears the threshold → return `None`, write NOTHING, log a
   structured warning with `fix_suggestion`.

`ReferenceImage` Pydantic model mirrors the migration-0019 columns. `_VerificationResponse` is the
Flash JSON schema. Env-tunable module constants: `REFERENCE_MIN_CONFIDENCE` (0.7),
`REFERENCE_REFRESH_DAYS` (30). Structured structlog at each step (started/cache_hit/cache_stale/
serp_completed/candidate_verified/cached/rejected); `fix_suggestion` on the verification-fail and
reject logs.

## Files touched
- `agents/m0/entity_reference_images.py` (new)
- `tests/agents/m0/test_entity_reference_images.py` (new)

(No other files modified — build_poster_from_news.py / batch_posters.py left for SP4.)

## Divergences from the brief
- **Signature adds two injected, keyword-only params** beyond the brief's listed args:
  `genai_client: genai.Client` (the verification call needs a Gemini client — passed in rather than
  constructed internally, matching how `image_scorer`/`generate_posters` receive the client), and
  `current_year`/`now` (keyword-only, default None) so year-derivation and staleness are deterministic
  in tests (the brief explicitly required no nondeterministic clock call). Defaults preserve a simple
  call site.
- **Ruff line length:** repo has no ruff config (ruff default = 88), but the codebase + CLAUDE.md
  convention is **120**, and existing m0 files (image_scorer.py) are written to 120 and DO NOT pass
  ruff-format at 88. Per Rule 11 (match codebase conventions) the two new files are formatted to
  `--line-length 120` to match. Flagged so the orchestrator can standardize a ruff config if desired.

## Review findings + fixes
Self-review (Step B) against the checklist — all PASS, no critical/high issues:
- Failure path writes nothing: upload+upsert are inside the `confidence >= threshold` guard; reject
  path only logs+returns None. Proven by the reject test.
- Threshold + refresh are env-tunable (module constants from `os.environ`).
- No nondeterministic time/year call on the test path (`now`/`current_year` injected).
- Full type hints; Pydantic v2 models; Google-style docstrings with examples.
- Reuse: imports `search_images`, `download_candidate`, `GEMINI_LLM_MODEL`, `CANDIDATE_LIMIT`; verify
  mirrors image_scorer; upload mirrors `_upload_poster`. No duplicated SERP/download/upload logic.

Medium/low notes (not fixed, intentional):
- The verifier is called per-candidate sequentially (no parallelism) — fine for ≤5 candidates;
  matches the existing scorer's sequential style.
- `entity_key` is used verbatim in the storage object path (e.g. `"kevin warsh/reference.jpg"`,
  contains a space). Supabase storage accepts spaces in object keys; kept as-is for a human-readable,
  deterministic path. Low risk; flag if a future key contains slashes.

## Validation
- **Ruff check:** `ruff check agents/m0/entity_reference_images.py tests/agents/m0/test_entity_reference_images.py` → **All checks passed!**
- **Ruff format:** `ruff format --line-length 120 --check <both>` → **2 files already formatted** (PASS at the codebase's 120 convention; see Divergences re: the missing repo config).
- **Pytest:** `pytest tests/agents/m0/test_entity_reference_images.py -v` → **5 passed in 0.87s**.
  - `test_cache_hit_returns_without_any_network_call` — fresh row, asserts SERP/verify NOT called, no upsert/upload.
  - `test_miss_with_passing_verification_uploads_and_upserts` — miss, strong candidate wins over weak; asserts 1 upload (winner bytes) + 1 upsert with confidence 0.91 and `on_conflict="entity_key"`.
  - `test_all_below_threshold_returns_none_and_writes_nothing` — one below-0.7 match + one high-conf NON-match → None, asserts NO upload + NO upsert (Rule 9 safety test).
  - `test_stale_row_triggers_a_refetch` — stale row, asserts SERP called once despite existing row.
  - `test_empty_entity_key_short_circuits_without_lookup` — empty key → None, no writes (edge).

## Definition of done
**PASS.** The four mandated tests (happy/cache-hit, fetch/miss, failure/reject, edge/stale) exist and
pass; the reject path provably writes nothing (asserted: `uploads == []` and `upserts == []`).

## Concerns for SP4
**Exact signature SP4 must call:**
```python
async def get_or_fetch_entity_reference_image(
    entity_key: str,
    entity_name: str,
    entity_kind: str,
    as_of: str | None,
    supabase_client: Any,        # service-role Supabase client (table + storage)
    genai_client: genai.Client,  # Gemini client for the verification call
    *,
    current_year: int | None = None,  # optional; falls back to year(as_of)
    now: datetime | None = None,      # optional; defaults to datetime.now(UTC)
) -> ReferenceImage | None
```
Notes for SP4:
- It is **async** — call sites must `await` it.
- Pass `concept.entity_key`, `concept.entity_name`, `concept.entity_kind`, `concept.entity_as_of` from
  the SP2-extended `StoryConcept`. Only call it when `entity_kind in {"person"}` and `entity_key` is
  non-empty (an empty key short-circuits to None anyway, but gating avoids a wasted call).
- On a non-None return, condition generation on the canonical photo: either re-download
  `reference_public_url` to bytes, or — cheaper — have SP4 keep the bytes via a thin wrapper, since
  this function downloads them internally. **The returned `ReferenceImage` exposes the URL/path, NOT
  the raw bytes.** If SP4 needs the exact bytes byte-for-byte (its DoD asserts byte-equality), it must
  fetch `reference_public_url`. Consider whether SP4 wants a variant that also returns bytes to avoid a
  re-download — current contract returns metadata only.
- On `None`, run the existing SERP path UNCHANGED (no regression).
- It does its own SERP + verification network I/O; SP4 should mock this function (not its internals) in
  the integration test.
