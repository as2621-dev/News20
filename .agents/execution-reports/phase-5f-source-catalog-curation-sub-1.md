# Phase 5f — SP1 execution report (LLM candidate generator + ONE proven cell)

**Status: SUCCESS** · 2026-06-07

## Mission recap
Build the missing LLM candidate generator that writes `data/{type}.{archetype}.json`
inputs for the EXISTING seeder, lock the taxonomy, and prove ONE cell
(`channels.ai-frontier-tech`) end-to-end live with ≥50 resolved YouTube rows.

## What I implemented
- **`scripts/seed_catalog/generate_candidates.py`** (349 lines) — orchestration +
  file I/O + CLI. Reuses `agents.pipeline.llm_clients.LLMClient.call_gemini` to
  over-generate candidates per `(entry_type, archetype)` cell, unions with any
  curated entries already in the target file (curated picks like `lexfridman`
  preserved + ranked first), dedupes on the seeder's identity key, caps to
  `--count`, and writes pretty ranked JSON. Public API: `generate_cell(...)`,
  `generate_many(...)`; CLI: `--type / --archetype / --all / --count`.
- **`scripts/seed_catalog/candidate_validation.py`** (241 lines) — pure parsing +
  per-candidate validation (split out of the generator to keep both files < 500
  lines / < 500 LOC per CLAUDE.md). `parse_candidate_array` (strips ```json
  fences, tolerates surrounding prose, never raises), `normalise_candidate`
  (identity field required; `topic_tags` filtered to the 8 keys with `[0]`
  coerced to a valid key when possible, else dropped; stray keys stripped),
  `candidate_dedup_key` (mirrors `seed_catalog._dedup_key_fn`, strips X `@`).
- **`scripts/seed_catalog/prompts.py`** (212 lines) — `CANDIDATE_SYSTEM_PROMPT`
  (anti-hallucination: propose only real entities, exact handles/slugs/titles),
  per-axis `AXIS_INSTRUCTIONS` (mirror the 4 JSON schemas exactly), per-archetype
  `ARCHETYPE_GUIDANCE` (explicit cultural/regional diversity baked in — cricket +
  football for sports, Bollywood + global cinema for arts, global voices for
  geopolitics/AI/startups), and `build_candidate_prompt`.
- **`reference/source-catalog-taxonomy.md`** — locks the 12 archetypes, the
  Q1 keep-12 + intra-archetype-diversity policy, the 8 topic-tag keys + curated
  sub-niches per category, and resolved Q2–Q6.
- **`.env.example`** — added a `YOUTUBE_API_KEY=` line under a new "Source catalog
  seeder" section. Did NOT touch the concurrent foreign `NEXT_PUBLIC_ONBOARDING_SKIP_AUTH` hunk.
- **`scripts/seed_catalog/data/channels.ai-frontier-tech.json`** — regenerated
  live (90 candidates, curated 7 preserved at top).
- **`tests/scripts/seed_catalog/test_generate_candidates.py`** — 11 mocked tests.

## Files touched
- `scripts/seed_catalog/generate_candidates.py` (new)
- `scripts/seed_catalog/candidate_validation.py` (new)
- `scripts/seed_catalog/prompts.py` (new)
- `reference/source-catalog-taxonomy.md` (new)
- `tests/scripts/seed_catalog/test_generate_candidates.py` (new)
- `scripts/seed_catalog/data/channels.ai-frontier-tech.json` (modified — regenerated live)
- `.env.example` (modified — one YOUTUBE_API_KEY hunk only)

## Divergences from the phase file (brownfield re-scope)
The phase file assumed a greenfield `agents/catalog/` package emitting SQL. That
is wrong: a complete seeder already exists at `scripts/seed_catalog/` (phase-5b).
Per the re-scoped progress file, I REUSED + EXTENDED it: I added only the missing
candidate generator that feeds the existing resolve→upsert harness. NO
`agents/catalog/`, NO SQL-emit rewrite, NO new LLM provider (reused the repo's
Gemini `LLMClient`). One sub-divergence within SP1's own spec: I split the
generator into `generate_candidates.py` + `candidate_validation.py` to honor the
"< 500 lines" constraint (a single file was 557 total) — clean parse/validate vs
orchestrate/CLI responsibility split, not arbitrary.

## Measured numbers (the SP2-sizing data)
- **YouTube forHandle resolution rate: 74 / 90 = 82.2%** (channels,
  ai-frontier-tech). 16 unresolved (hallucinated/wrong handles e.g. `deepmind`,
  `yannlecun`, `aieplained`, org channels) were dropped by the resolver as designed.
- **Over-generation factor chosen: ~75–90 per channels cell.** At 82% resolve,
  ≥61 proposals are needed to clear 50; `DEFAULT_CANDIDATE_COUNT = 75` leaves a
  comfortable margin. The proven cell used 90 → 74 survivors.
- **Quota units spent: 90** (`channels.list?forHandle` = 1 unit/call; one call per
  proposed channel). No `search.list` (100-unit) fallbacks fired.
- **SP2 projection (channels axis, all 12 archetypes):** 12 × ~90 = **~1,080
  units ≈ 11% of the 10,000/day free quota.** Even at 120/cell (~1,440 units) it
  fits one day with re-run headroom. Podcasts (iTunes), personalities (Wikipedia),
  and X (no resolver / unavatar) consume ZERO YouTube quota. **Quota is NOT a
  blocker for the full SP2/SP3 run.**

## Code-review findings + fixes
- **[high, fixed] File-size rule breach:** generator was 557 lines (> 500). Fixed
  by extracting `candidate_validation.py`; now 349 / 241 / 212, all < 500.
- **[med, fixed] Dedup parity with seeder:** `candidate_dedup_key` mirrors the
  seeder's X-handle `@`-stripping + lowercasing so the generator dedupes on the
  SAME identity the seeder upserts on (verified by `test_x_handle_dedup_ignores_leading_at`).
- **[low, by design] Generator does not verify existence** — anti-hallucination is
  a two-stage contract: generator proposes, seeder's resolver drops. Documented.
- No hardcoded secrets; keys read via `Settings` / env at the boundary; no secret
  logging (model text preview truncated to 200 chars on the error path only).
- All functions: verbose names, type hints, Google-style docstrings, structured
  `structlog` logging with `fix_suggestion` on every error/warn path.

## Validation
- `ruff check` (all new files + reference/): **All checks passed.**
- `ruff format --check`: **4 files already formatted.**
- `pytest tests/scripts/seed_catalog/`: **24 passed in 0.27s** (11 new + 13
  existing seed_catalog tests — no regression). New tests mock `LLMClient.call_gemini`
  at the boundary; no live service hit. Coverage: happy path, fenced-JSON unwrap,
  garbage→empty (no crash), non-array→empty, invalid-`tags[0]` coerce, no-valid-tag
  drop, missing-identity drop, case-insensitive dedup, X `@` dedup, curated-union
  preservation, count cap.

## Definition of done: PASS
- Generator (+ prompts + validation) exist, reuse `LLMClient`, emit correct
  per-axis schema, drop invalid with logged counts. ✅
- **Remote `content_sources` holds 74 `youtube_channel` rows tagged
  `ai-frontier-tech`** (≥ 50), **100% with real (non-null) thumbnails +
  subscriber counts** (verified via asyncpg). ✅
- Resolution rate measured (82.2%), over-gen factor chosen (~75–90), quota spend
  reported (90 units), SP2 projection shown to fit free quota. ✅
- `reference/source-catalog-taxonomy.md` written with locked decisions. ✅
- Mocked unit tests pass; ruff clean on all files. ✅

## Concerns for SP2 / SP3
1. **Resolve rate varies by axis + archetype.** Channels/ai-frontier-tech hit 82%,
   but niche or non-US archetypes (e.g. `geopolitics-world`, `arts-culture` with
   Bollywood/global asks) may resolve lower because handles are harder for the
   model to get exactly right. SP2/SP3 should measure per-cell resolve rate and
   bump `--count` for any cell that lands < 50; the CLI already supports it.
2. **Quota fits comfortably for channels** (~1,080–1,440 units of 10k/day). If SP2
   re-runs many cells in a day after low-resolve retries, watch the daily total —
   still has wide margin but is the one axis that consumes YouTube quota.
3. **X axis (SP3) has no live resolver** — handles upsert as `external_id` with
   null thumbnail/followers. Taxonomy Q2/Q3 decided: avatar via `unavatar.io`
   hot-link, followers null/approx. That avatar wiring is NOT in the current seeder
   — SP3 must add it (or accept null thumbnails on X until then). Flag for SP3 DoD
   (the phase-level DoD wants ≥90% thumb coverage on X).
4. **Personalities (SP3) resolve via Wikipedia slug** — accuracy of the model's
   `wikipedia_slug` drives thumbnail coverage; the prompt asks for the exact slug,
   but SP3 should measure Wikipedia photo-hit rate the same way SP1 measured YouTube.
