# Progress: phase-5b-source-data-model-catalog

**Phase file:** plans/phase-5b-source-data-model-catalog.md
**Started:** 2026-06-05
**Execution mode:** SEQUENTIAL (SP1 ⚠ irreversible → parallel refused; SP2←SP1, SP3←SP1,SP2, SP4←SP1)

## Pre-phase facts passed to sub-agents
- **Migration number:** SP1 creates `0008_content_sources.sql`. Latest on disk is `0006`. Do NOT renumber to 0007 — `0007` is reserved for Phase 5's entity registry (not yet on disk). 0008 must apply on a 0006 base (no FK to 0007).
- **No local DB:** no `supabase/config.toml`/psql/Docker → migration runtime-apply DEFERRED to owner manual ops; SP1 = offline SQL validation only.
- **FK/RLS convention:** match what `0003`/`0005` actually do (Rule 11). Verify whether user FKs target `auth.users(id)` or a `users` table before writing; phase file says `users` but the shipped convention wins — flag divergence.
- **Validation:** TS → `npm run lint` (biome), `npm test` (vitest run), `npm run build`. Python → `ruff check`, `pytest`.
- **Seed dir** `supabase/seed/` exists (SP2 adds `archetypes.sql`). **`scripts/` does not exist** (SP3 greenfields `scripts/seed_catalog/`). `agents/shared/settings.py` + root `requirements.txt` exist (SP3 extends).
- **External APIs (YouTube/iTunes/Wikipedia) MUST be mocked in tests** (CLAUDE.md mocking rule). X handles stored as `x_account` rows with NO live resolution (no resolver until 5c/5d).

## Decisions & divergences (live)
- **Migration renumbered 0008 → 0009.** During SP1, Phase 5 (`58e1b16`, `0007_entity_registry`) and Phase 5A (`00b492a`, `0008_feed_allocation`) committed. SP1 took next-free `0009_content_sources.sql`. No FK to 0007/0008; applies on a 0006 base. **Phase file's "0008" references are now stale — schema lives in `0009`.** Downstream (SP2 seeds `archetypes`, SP4 types) read columns from `0009`.
- **User-FK convention:** `auth.users(id)`, owner column `user_id`, RLS via `auth.uid()` (matches 0005/0007/0008, overrides phase file's literal `users`). SP4 data layer scopes via `auth.uid()`, no `public.users` join.
- `content_source_items` created empty (Phase 5d populates).
- **Test-file scope:** phase file "Files touched" omits test paths but DoDs require mock/SQL assertions. Orchestrator authorizes each sub-agent to add the minimal mirrored test (SP4 → `tests/lib/sources.test.ts`); SP2 uses in-SQL `DO` assertions (deferred-offline, no DB).
- **SP2 ∥ SP4 run in parallel in-tree** (disjoint files, both depend only on SP1, sub-agents don't commit). SP3 waits on SP2.

## Sub-phase progress
- [x] 1: Migration 0009 — content-source + personality + archetype schema — COMPLETE (report sub-1)
- [x] 2: Archetype definitions + vectors seed — COMPLETE (report sub-2; 12 slugs, normalized vectors)
- [x] 3: Per-archetype catalog seeder (port TL;DW seed_catalog) — COMPLETE (report sub-3; 12 tests, 289 repo pytest green)
- [x] 4: Typed source data layer + types — COMPLETE (report sub-4; lint/283 tests/build green)

## Phase-level gates (after all 4)
- [x] 3a Phase-level DoD — PASS (seeder + data layer fully green; migration schema+RLS static-PASS, **live apply DEFERRED-offline** — no local DB, same constraint 5/5A shipped under)
- [x] 3b Slop scan — PASS (no TODO/console.log/any-casts/dead-code/hardcoded-secrets; iTunes None is counted+logged at call site, not swallowed)
- [x] 3c CSO lite — PASS (RLS owner-scoped via auth.uid(), public-read no-write; no new deps; key stripped from logs). 1 LOW logged: youtube_api_key → SecretStr (.agents/cso-findings/phase-5b-source-data-model-catalog.md)
- [x] Single atomic commit — `15531a4` (28 files, 3679 insertions)

**STATUS: COMPLETE** (2026-06-05)
