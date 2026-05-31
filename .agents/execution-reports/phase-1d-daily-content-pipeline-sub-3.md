# Phase 1d — Sub-phase 3 execution report

**Sub-phase:** Per-user scoring + fallback tree + orchestrator/persist
**Status:** COMPLETE (2026-05-31). ⚠ irreversible — owner PRE-AUTHORIZED. One real paid e2e run executed; INSERT-only, one story.

## What was built

### `agents/pipeline/stages/ranking.py` (ADAPT)
The per-(user, story) heuristic scorer + fallback tree, implementing `reference/ranking-spec.md` §1–2 verbatim (not re-derived).
- `Score = (Affinity×DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2`. Weights are config constants (`AFFINITY_WEIGHT`/`IMPORTANCE_WEIGHT`/`FRESHNESS_WEIGHT`); DepthMatch ladder `{0:1.0, 1:0.6, 2:0.3}`; threshold `T = DEFAULT_SCORE_THRESHOLD = 0.20`.
- **Importance/Freshness reuse the produce-gate primitives** (`compute_importance_score`/`compute_freshness_score`) — one source of truth, identical at gate-time and rank-time (Rule 3/7).
- `normalize_affinities` (max-normalization → top interest = 1.0), `compute_story_score`, `score_stories_for_interest`, `generate_fallback_candidates` (leaf→parent→grandparent climb with strict-ceiling/`Score≥T` stop), `score_candidates_for_user` (the SP3→SP4 handoff, buckets keyed by followed leaf).
- Pure over injected inputs (profile + pool + taxonomy). No DB/clock/network.

### `agents/pipeline/persist.py` (NEW) + `agents/pipeline/persist_helpers.py` (NEW — split for <500 LoC)
- `persist.py`: the `supabase-py` **service-role** writer. Uploads audio→`digest-audio`, poster→`story-posters` (public URLs), then INSERTs `stories`→`digests`→`caption_sentences`/`detail_chunks`/`story_trust`/`story_sources`/`story_interests`/`suggested_questions`. Parents first so children FK correctly. Client is **injected** (no secret read here). Returns `PersistResult` listing every created row id + storage object path (auditable/cleanable). **INSERT-only** — never updates/deletes.
- `persist_helpers.py`: pure column-mapping builders + the static outlet→bias trust derivation (coverage counts, blindspot rule).

### `agents/pipeline/orchestrator.py` (ADAPT)
Chains one gated story: `script (SP2) → verify (SP2) → TTS (reuse agents/voice/gemini_tts) → caption-timing (reuse forced_alignment) → poster (reuse agents/m0) → persist (SP3)`. Catches `VerificationHaltError` → skip+log, **never publishes**. Poster failure is **non-fatal**. All heavy clients injected.

### Tests (NEW — all externals mocked)
`tests/agents/pipeline/{test_ranking,test_fallback_tree,test_persist,test_orchestrator}.py` — 33 tests. Plus the standalone live e2e: `tests/agents/pipeline/sp3_e2e_fixture_run.py` (guarded behind `RUN_LIVE_E2E=1`, NOT collected by pytest).

## Files touched
- `agents/pipeline/stages/ranking.py` (ADAPT, 492 LoC)
- `agents/pipeline/persist.py` (NEW, 448 LoC)
- `agents/pipeline/persist_helpers.py` (NEW, 444 LoC)
- `agents/pipeline/orchestrator.py` (ADAPT, 362 LoC)
- `tests/agents/pipeline/test_ranking.py` (NEW)
- `tests/agents/pipeline/test_fallback_tree.py` (NEW)
- `tests/agents/pipeline/test_persist.py` (NEW)
- `tests/agents/pipeline/test_orchestrator.py` (NEW)
- `tests/agents/pipeline/sp3_e2e_fixture_run.py` (NEW — live e2e, env-gated)
- `requirements.txt` (+`supabase>=2.0`, +`python-dotenv>=1.0`; installed `supabase 2.30.1`)

## Divergences (flagged per Rule 12)
1. **Trust derivation is static, not from an M2 module.** The M2 trust layer (commit 0e76d50) is a **read-only TypeScript** feature — no importable Python trust-derivation module exists. Per the SP3 brief's fallback, `persist_helpers.py` derives `story_trust`/`story_sources` from `CanonicalStory.covering_outlets` + a **static outlet→bias table** (the AllSides/Ad Fontes one-time lookup mandated by `integrations.md`, Decision #6). Unknown domains default to `center` (counts toward coverage; flagged in-code). Blindspot fires only on genuine concentration (one lean >50% AND a unique minority <30%) — a fix from the first naive version that mis-flagged balanced splits. Replace with a richer M2 GKG/tone source when it lands.
2. **`story_segment_slug` defaults to `wildcard`.** The canonical story carries no resolved segment yet (segment resolution is an `interests`-tree join, an SP4 concern). `_resolve_segment_slug` returns `wildcard` to satisfy the NOT NULL enum FK. FLAGGED for SP4 to backfill from the matched interest's `interest_segment_slug`.
3. **`persist.py`/`persist_helpers.py` split.** `persist.py` neared 500 LoC with the builders inline, so the pure column-mappers + trust derivation moved to `persist_helpers.py` (CLAUDE.md file-size discipline). The brief explicitly permits this split.
4. **Live e2e lives under `tests/agents/pipeline/` (not `scripts/`).** `scripts/` is not an established repo path; per the brief's fallback, the run is a standalone script gated by `RUN_LIVE_E2E=1`, with no `test_` prefix → never collected by `pytest`. The hermetic suite stays free.
5. **`detail_chunks` from the single-source body.** Detail body chunks are paragraph-split from `canonical_body_text` (one chunk when no blank lines). `caption_sentences.anchor_speaker` alternates ALEX/JORDAN by sentence index per the prototype's `st.anchors[si % 2]`.

## Self code-review findings + fixes
- **Bug found + fixed (caught by tests): blindspot derivation.** The first version returned the first lean matching a loose "<30% / others >70%" test, which mis-fired on `{8,1,0}` (returned `center` not `right`) and on balanced 4-outlet splits. Rewrote to require genuine concentration (a dominant lean >50%) and pick the unique strict-minimum lean. Both `test_blindspot_*` now pass.
- **Removed dead code:** unused `_VALID_BIAS_LEANS` constant (Rule 2), unused test imports/vars.
- **No secrets in code/logs:** client is injected; the e2e reads keys via env/dotenv at the boundary only; logs carry no key values.
- All agent files <500 LoC; `fix_suggestion` on every error log.

## Validation
- **Unit suite:** `python3 -m pytest tests/agents/ -q` → **121 passed** (88 prior + 33 new SP3). Hermetic — no network/paid calls.
- **Ruff:** `ruff check agents/pipeline tests/agents/pipeline` → All checks passed. `ruff format --check` → 26 files already formatted.

## Live e2e evidence (⚠ REAL paid Gemini TTS+image + real Supabase writes/uploads)
`RUN_LIVE_E2E=1 .venv/bin/python tests/agents/pipeline/sp3_e2e_fixture_run.py` → exit 0.

**Created rows (INSERT-only, cleanable by the `FIXTURE-SP3-` prefix):**
- `stories.story_id` = `FIXTURE-SP3-950c5e0f05a1`
- `digests.digest_id` = `df2839ae-bf68-440d-8347-f6d52a593c6d`
- `caption_sentences` = 10 rows (ids: 17676c40…, 40a8d3c4…, 4267e28f…, 8b5a7ddb…, d46bf843…, d0d91f42…, 1831aa97…, b140e372…, 2dd13724…, 05bdf6d9…)
- `detail_chunks` = `4d63bec8-685f-4cc8-bd73-13c46f6ea33f`
- `story_trust` = `841e9742-adcd-46d5-97e7-5d2ffbf1fe8c`
- `story_sources` = 4 rows (65519a82…, ca5b2705…, 0b82218d…, 5a131313…)
- `story_interests` = 2 rows (39807cfa… depth 0, 127c4f4a… depth 1)
- `suggested_questions` = 2 rows (e7c3be22…, 47df5d26…)

**Storage objects (both public, both HTTP 200):**
- audio: `digest-audio/FIXTURE-SP3-950c5e0f05a1/digest.mp3` → `https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/digest-audio/FIXTURE-SP3-950c5e0f05a1/digest.mp3` — **HTTP 200, 962,924 bytes**
- poster: `story-posters/FIXTURE-SP3-950c5e0f05a1/poster.png` → `https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/story-posters/FIXTURE-SP3-950c5e0f05a1/poster.png` — **HTTP 200, 1,976,879 bytes**

**DB readback assertions (all passed):** stories row present; 10 caption rows each with exactly 1 highlight + ms timings; 2 story_interests rows (leaf+parent).

## Definition of done — PASS/FAIL per item
1. Unit tests (externals mocked):
   - Affinity-dominant ordering (niche small story outscores generic big) — **PASS** (`test_ranking::test_niche_small_story_outscores_generic_big_story`, tests the formula).
   - Fallback climbs leaf→parent only when no leaf clears `Score≥T`, **stops at strict** — **PASS** (`test_fallback_tree::test_climbs_to_parent_only_when_no_leaf_qualifies`, `::test_strict_does_not_climb_even_with_no_leaf_qualifier`).
   - caption-JSON → `caption_sentences` lossless (`word_tokens` ms timings, one highlight/sentence) — **PASS** (`test_persist::TestCaptionMappingLossless`).
   - persist maps each field to the right column (mocked client asserts payloads) — **PASS** (`test_persist::test_persist_inserts_all_tables_with_correct_columns`).
2. ONE real e2e fixture run — **PASS** (story row created; digest audio HTTP 200; caption_sentences with ms timings + 1 highlight/sentence; poster_url HTTP 200; story_interests rows present). Blast radius held: INSERT-only, one story; all ids/paths printed.
- Ruff passes; every agent file <500 LoC — **PASS**.

## Concerns + approx spend + what SP4 needs
- **Approx spend (one story):** 2 Gemini text calls (scripting + verification, Flash, tiny), 1 Gemini multi-speaker TTS render (~55s audio → ~963 KB MP3), 1 Gemini image generation (Nano Banana Pro poster → ~1.98 MB PNG) + a Serper image-search/grading pass. Consistent with the owner's accepted ~$0.16/story unique-generation budget — order of a few cents for text+TTS plus the image gen. ONE story only.
- **Cleanup:** the `FIXTURE-SP3-950c5e0f05a1` rows + its two storage objects are live demo data. They are isolated by the `FIXTURE-SP3-` prefix and safe to leave or delete; no existing row was touched.
- **SP4 needs:**
  - The ranking handoff is `score_candidates_for_user(profile_interests, stories, story_interest_tags, interest_nodes, now_utc=, score_threshold=) -> {followed_leaf_id: [ScoredCandidate]}`. SP4's §3 allocator consumes those buckets (each `ScoredCandidate` carries `score`/`matched_interest_id`/`fallback_depth`). Exploration (§3.7) is NOT in the fallback generator — it is an allocator concern (siblings/parents of followed nodes); SP4 owns it.
  - `T` (0.20) and the importance/freshness floor constants are first-draft — confirm at the SP4 2-user manual run (Open Q4).
  - SP4 must build `interest_nodes` (`{interest_id: InterestNode}`) + `UserProfileInterest[]` from Supabase reads of `interests` + `user_interest_profile`, and the `has_current_digest_lookup` for the produce-gate from a `digests.digest_is_current` read.
  - Segment backfill (divergence #2): SP4 should set `story_segment_slug` from the matched interest's `interest_segment_slug` rather than the `wildcard` default.
  - `daily_feeds` is **NOT** written here (SP4 owns it).
