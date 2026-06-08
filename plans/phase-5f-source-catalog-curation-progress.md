# Progress: phase-5f-source-catalog-curation

**Phase file:** plans/phase-5f-source-catalog-curation.md
**Started:** 2026-06-07
**Execution mode:** SEQUENTIAL (SP2 & SP3 both append to `supabase/seed/content_sources.sql` → file overlap → not eligible for clean worktree-parallel)

## Pre-flight (verified before spawning)
- `YOUTUBE_API_KEY` — live (resolved @MKBHD via channels.list, 1-unit). Casing fixed `YouTube_→YOUTUBE_`.
- `SUPABASE_DB_URL` — live session-pooler (`aws-1-us-east-1.pooler.supabase.com:5432`); was mis-keyed as `SUPABASE_DB_PASSWORD` + unencoded → renamed + percent-encoded. DSN-string connect OK.
- `GEMINI_API_KEY` — valid (repo LLM is Gemini, not OpenAI/Anthropic as plan text assumed).
- `SUPABASE_SERVICE_ROLE_KEY` — present.
- Remote `content_sources`: exists (migration 0009 live), **0 rows**, 14 columns → confirms "0 sources" bug at data layer.
- `.venv` has `supabase` 2.30.1; `asyncpg` installed for verification; `psql` NOT on PATH (apply via asyncpg or supabase CLI).
- `settings.py` ALREADY declares `youtube_api_key` field (plan's "add it" is half-done).

## YouTube quota guardrail (10,000 units/day free)
- `channels.list?forHandle` = 1 unit; `search.list` = 100 units.
- Sub-agents MUST prefer handle-based resolution; cap `search.list` fallbacks with a logged daily budget; over-generate to clear ≥50 after drops.

## Re-scoped to brownfield (user decision: reuse + extend scripts/seed_catalog/)
The phase file assumed greenfield `agents/catalog/`. Reality: `scripts/seed_catalog/` (committed, phase-5b commits 15531a4 + 359de83) already loads `data/{type}.{archetype}.json` → resolves (YouTube/iTunes/Wikipedia, drop-on-fail) → dedup+persona-union merge → upserts to remote `content_sources` via service-role. Candidate input contract: ranked JSON array, e.g. `[{"youtube_handle":"lexfridman","topic_tags":["ai","tech"]}]`; array position = popularity rank. Validators built in: ALLOWED_ARCHETYPES (12), ALLOWED_TOPIC_TAGS (8). Reusable Gemini LLM at `agents/pipeline/llm_clients.LLMClient`.
The ONLY real gap: candidate data at scale (today ~7 cells × 4–7 entries; need 48 cells × ≥50 surviving). New code = an LLM candidate generator that writes those JSONs. NO new agents/catalog/, NO SQL-emit rewrite. Work happens in `scripts/seed_catalog/`.

## Sub-phase progress (re-scoped)
- [x] 1: LLM candidate generator + taxonomy doc + proven cell — COMPLETED. Remote ai-frontier-tech youtube_channel = 74 (100% thumb+subs). YT forHandle resolve rate 82.2% (74/90); over-gen factor 75; 90 units spent; SP2 channels projection ~1,080u = 11% of daily quota (not a blocker). 24 tests pass, ruff clean. New: generate_candidates.py, candidate_validation.py, prompts.py, reference/source-catalog-taxonomy.md, tests. ⚠ SP3 concern: X axis has NO live avatar resolver yet (unavatar wiring must be ADDED in SP3 to hit ≥90% X thumb coverage).
- [~] 2: Full population YouTube(channels)+Podcasts across 12 archetypes — IN PROGRESS. CHANNELS DONE via MERGED pass: 12/12 ≥50 (633 rows, 100% thumb, 105 multi-tagged). PODCASTS: re-seeding now via MERGED pass (background bonvdv1tx, ~37min). SP2 improved itunes_resolve.py (+94: pace-gate + 429 backoff + THROTTLED) and seed_catalog.py (+5: opt-in pacing) — reviewed, good.
  - **CLOBBER BUG FOUND + FIXED:** the driver's old `--all-archetypes` LOOP seeded one archetype per upsert; a cross-tagged source got its `personas` overwritten cell-by-cell (plain replace), stripping other archetypes' tags (my own `--archetype ai-frontier-tech` validation run dropped startup-operator channels to 45). FIX: `--all-archetypes` now does ONE **merged pass** (`archetype_filter=None`) — load_entries merges all 12 files, unions personas, resolves+upserts each unique source once → clobber-proof. SP3 also added a `_set_expr` array-union as belt-and-suspenders. Channels + podcasts re-seeded merged → fixed. X/personalities already correct (SP3 loop+union; 79/99 multi-tagged).
  - **SP2 COMPLETE:** channels 633 rows (100% thumb), podcasts 1032 rows (100% thumb), 12/12 each ≥50.

- [x] 4: Remote assertions + reproducibility + e2e — COMPLETED. All 48 cells ≥50; total 3360 rows; topic_tags[0] 0 violations; e2e proxy (listSourcesByArchetype for balanced-generalist) = full 12-card all-thumbnail deck on all 4 axes → "0 sources" gone. Slop PASS, CSO PASS (asyncpg added to requirements; SQL params bound + col-name validated). iOS simulator render DEFERRED to cloud-Mac (per news20-phase3b-ios-deferred). 30 tests pass. Reports: sub-{1,2,3,4}.md.

## STATUS: COMPLETE — ready for single phase commit.
- [x] 3: Full population X+Personalities across 12 archetypes — COMPLETED (SUCCESS). X: 12/12 ≥79, thumb 100% (unavatar hot-links — see caveat). Personalities: 12/12 ≥80, thumb 85.8% global (SHORT of 90% in crypto-fintech 64.5% + markets-macro 74.7% — real Wikipedia ceiling, surfaced). 0 topic-tag violations. 30 tests pass, ruff clean. New: x_resolve.py (unavatar) + tests; wired async X resolver into seed_catalog.py. ⚠ CAVEATS for SP4: (a) unavatar free tier = 25 req/day/IP → exhausted → X handles NOT verified real (hot-links render regardless) — anti-hallucination gap, needs unavatar key/X API later; (b) 2 personality cells <90% thumb (data-real, decide curate-vs-accept); (c) report: .agents/execution-reports/...-sub-3.md.
- [ ] 4: Remote assertions (every archetype×axis ≥50, thumb coverage, topic_tags[0]∈8) + reproducibility (commit data JSONs, optional content_sources.sql dump) + e2e SourceSwipe incl balanced-generalist — PENDING

## ⚠ ENV BLOCKER + FIX (discovered during resume, 2026-06-08)
The seeder's live path upserts via the **supabase-py REST client** (`<ref>.supabase.co`). This machine is **IPv4-only (no IPv6 egress)** and that REST host does NOT resolve here → every upsert DNS-fails (`[Errno 8] nodename nor servname`). The earlier SP1/SP2-channels upserts (707 rows) succeeded under the prior session's network. **Fix:** new committed driver `scripts/seed_catalog/seed_via_pooler.py` injects an asyncpg/session-pooler upsert shim into the existing `run_seed` (reuses all resolve/merge logic; jsonb codec + float→Decimal + enum/array handling; `--selftest` validates write path). **All seeding now goes through `seed_via_pooler.py`, NOT `seed_catalog.py` main().** This is the IPv4-pooler rule from memory `news20-supabase-ddl-connection`.
- SP2 podcasts: re-seeding all 12 via pooler driver (background, in progress).
- SP3 (x + personalities): launched as background sub-agent, instructed to seed via the pooler driver + add the unavatar.io X resolver (build_x_account_row had thumbnail=None).

## Reproducibility decision
Existing harness upserts directly (no SQL emit). Reproducible artifact = committed `data/*.json` candidate inputs (+ optional `supabase/seed/content_sources.sql` dump in SP4 for review). Do NOT rewrite the seeder to emit SQL as a blocker.

## Phase-level DoD (verify before commit)
≥50 thumbnail-bearing, archetype-tagged rows for every 12 profiles × 4 axes on remote (in-SQL assertions, per-cell counts logged); live onboarding shows full decks incl. `balanced-generalist`; reproducible from committed seed + JSON.

## Commit policy
Commit ONLY 5f files by explicit path (concurrent tree — foreign WIP present). `.env.example`: stage only the YOUTUBE_API_KEY hunk, not the foreign SKIP_AUTH hunk.
