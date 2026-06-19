# Phase 0c — Sub-phase 4 execution report

**Sub-phase:** 4 (final) — Wire canonical photo into poster generation (L5 wire + L3 redirect + SERP fallback)
**Status:** SUCCESS
**Date:** 2026-06-18

## Implemented

When the resolved primary subject is a **person** with a VERIFIED canonical photo (from SP3's
`get_or_fetch_entity_reference_image`), the image model is now conditioned on THAT photo's bytes instead of
the best-effort SERP winner. Otherwise the existing SERP seed path runs **byte-for-byte unchanged**. No
symbolic / faceless fallback was introduced.

A single shared selection seam, `resolve_canonical_reference_seed(concept, supabase_client, genai_client)`,
was added to `build_poster_from_news.py` and consumed by BOTH the synchronous path
(`build_poster_for_digest`) and the batch path (`batch_posters.prepare_poster_generation`) so the two can
never diverge (DoD requirement). It returns `(bytes, "image/jpeg")` for a verified photo, or `None` to fall
back to SERP.

The helper returns `None` (→ unchanged SERP path) when any of:
- no `supabase_client` injected (the default — see "Production wiring" below);
- `concept.entity_kind != "person"` or `concept.entity_key` empty;
- SP3 returns `None` (no verified photo) — logs `canonical_reference_absent_serp_fallback`;
- the verified photo's public URL fails to download — logs `canonical_reference_fetch_failed`.

On a verified hit it logs `canonical_reference_used` with the confidence + URL.

**Async bridge:** the SP3 function is `async` but both poster entry points are sync. `_run_coroutine_blocking`
runs the coroutine via `asyncio.run` when no loop is running (fill_batch_posters worker threads, tests), and
offloads to a throwaway thread+loop when a loop IS already running (the orchestrator drives the sync builder
from inside its event loop) — avoiding `RuntimeError: asyncio.run() cannot be called from a running event
loop`. No new async framework introduced.

**URL byte-fetch:** reuses the existing `download_candidates._fetch` (already imported in
`build_poster_from_news.py`) — a simple httpx GET — kept simple per the brief.

## L3 redirect (post-generation identity check)

**Noted-as-ABSENT.** There is NO post-generation identity check anywhere in the current poster pipeline
(`build_poster_from_news.py` goes generate → `grade_and_brand` → write; `grade_and_brand` is a deterministic
cover-fit + brand pass with no identity verification). Per the sub-phase brief ("If NO post-gen identity
check currently exists in the code, do NOT invent one — that's out of scope; just note it"), the L3 redirect
was deliberately NOT implemented. The L5 grounding seam itself already eliminates the stale-prior substitution
at conditioning time, which is the phase's core fix.

## Files touched

- `agents/m0/build_poster_from_news.py`
- `agents/m0/batch_posters.py`
- `tests/agents/m0/test_build_poster_from_news.py` (new)

(SP1/SP2/SP3 artifacts — `poster_models.py`, `story_concept.py`, `entity_reference_images.py`,
`master-plan.md`, `reference/poster-pipeline.md`, migration 0019, eval script — were already in the
uncommitted working tree from earlier sub-phases and were NOT touched here. SP2's `story_body=summary,
story_date=None` edits in both poster files are intact.)

## Production wiring (divergence — noted, not a regression)

The brief constrained edits to ONLY the 3 files above. Threading a real `supabase_client` into production runs
requires touching `agents/pipeline/orchestrator.py::generate_poster_bytes` (it has `supabase_client` available
in `render_phase`) and `scripts/fill_batch_posters.py` (it constructs a service-role client). Those are OUT of
this sub-phase's file scope, so `supabase_client` defaults to `None` at both call sites today — which keeps the
SERP path byte-for-byte unchanged (no regression) but means the canonical grounding does NOT yet activate in
production. **Follow-up (1-2 lines each):** pass `supabase_client=supabase` from `generate_poster_bytes` and
from `fill_batch_posters`'s `prepare_poster_generation` call to turn the feature on. The seam, logging, and
tests are all in place; only the two call-site wires remain.

⚠ Also note: when activated, `generate_poster_bytes` is called synchronously from inside the async
`render_phase` coroutine — the `_run_coroutine_blocking` thread-offload path handles this correctly, but it
adds a per-person blocking SP3 call (SERP + verify) on a cache miss inside the render wave. Acceptable for v1
(cache hits are zero-network), flagged for the activation step.

## Self code-review (Step B) — findings + fixes

- ✅ SP2's `story_body`/`story_date` edits intact in both files (grep-verified).
- ✅ SERP fallback path is byte-for-byte the old behaviour when no canonical photo (tests
  `test_canonical_absent_*` + `test_no_supabase_client_*` assert exact SERP winner bytes/mime; `_fetch` is
  rigged to explode if touched on the fallback path, proving purity).
- ✅ Canonical bytes actually reach `generate_from_reference` (test asserts byte-equality to the canonical
  bytes AND inequality to the SERP winner bytes — Rule 9).
- ✅ Clients threaded cleanly via an optional keyword-only `supabase_client` param; existing 2-arg builder
  injection in the orchestrator/tests is unaffected.
- ✅ No symbolic fallback introduced.
- ✅ Structured logging present (`canonical_reference_used` / `_absent_serp_fallback` / `_fetch_failed`), each
  with `fix_suggestion`.
- ✅ No circular import (`import agents.m0.batch_posters` + `build_poster_from_news` succeeds).
- **No critical/high findings.** Medium/low: the production non-wiring (documented above) and the per-render
  blocking SP3 call on activation.

## Validation (Step D)

**Ruff lint:** `ruff check ... --line-length 120` → **All checks passed!** (the brief notes the repo has no
ruff config and 120 is the codebase convention).

**Ruff format:** `ruff format --check` flags the two source files — BUT a `git stash` check confirmed both were
ALREADY format-dirty at line-length 120 *before* this sub-phase (pre-existing ~88-char style; no ruff config in
repo). The format diff touches ONLY pre-existing lines (SP2 concept calls, `_image_config`, etc.) — none of my
added lines appear in it. Reformatting would rewrite untouched SP2 lines and violate Rule 3 (surgical), so it
was deliberately NOT applied. My new code is 120-format-clean.

**Pytest (this file):** `pytest tests/agents/m0/test_build_poster_from_news.py -v` → **8 passed**.
- `test_canonical_present_conditions_on_canonical_bytes_not_serp` (sync, Rule 9 byte-equality)
- `test_canonical_absent_passes_serp_winner_bytes_unchanged` (sync, no regression)
- `test_no_supabase_client_keeps_serp_path_unchanged` (sync, default wiring)
- 5 shared-helper tests (the batch path's exact seam): canonical present / absent / non-person skip / no
  client / URL-fetch-failure fallback.

**Pytest (full m0 dir):** `pytest tests/agents/m0/ -v` → **33 passed** (SP2 `test_story_concept` and SP3
`test_entity_reference_images` all still green).

## Definition of done

**PASS.** When a verified canonical photo exists for the resolved entity, the bytes handed to
`generate_from_reference` equal the canonical bytes (NOT the SERP winner) — asserted by
`test_canonical_present_conditions_on_canonical_bytes_not_serp`. When none exists, the SERP winner bytes are
passed exactly as today — asserted by `test_canonical_absent_passes_serp_winner_bytes_unchanged`. Both sync and
batch paths covered (batch via the shared `resolve_canonical_reference_seed` seam it consumes verbatim).

## Concerns

1. **Production not yet wired** (file-scope constraint) — canonical grounding is dormant until the two call
   sites pass `supabase_client`. SERP path unchanged meanwhile = no regression. Recommend the activation as the
   immediate next step (also needed for the phase-level DoD, which runs the live path for the two reported
   stories).
2. **L3 redirect absent by design** — no post-gen identity check exists to hook; not invented (per brief).
3. **Per-render blocking SP3 call on activation** — a cache miss adds a synchronous SERP+verify inside the
   async render wave (handled correctly by the thread bridge, but flagged for the activation step).
