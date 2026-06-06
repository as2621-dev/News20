# Progress: phase-5a-build-your-30-and-entity-ranker

**Phase file:** plans/phase-5a-build-your-30-and-entity-ranker.md
**Started:** 2026-06-05
**Mode:** Sequential (SP1→SP2→SP3→SP4 is a strict dependency chain; no parallelizable group)
**Status:** COMPLETE — commit 00b492a (2026-06-05)

## Open questions — resolved (owner: "use common sense, keep moving")
1. Slug maps: climate→World&Politics, health→Tech&Science, wildcard→Culture (defaults)
2. ENTITY_BONUS_WEIGHT = 0.3 first draft, tune at sim
3. Default allocation = balanced fallback (breaking 4 + even split across non-source categories with stories)
4. 0008 = 5a; 5b renumbered to 0009 (master-plan done; phase-5b/5e docs still say 0008 internally — out of scope, flagged)
5. breaking user-budgetable, default 4

## Sub-phase progress
- [x] 1: Migration 0008_feed_allocation + live apply (0007 + 0008 + seeds) — COMPLETED (also applied pending 0006; 248 entities live; RLS allow/deny proven; report sub-1.md)
- [x] 2: Entity hydration + entity-aware Score + story→category classifier — COMPLETED (27 ranking tests pass; 271 agents tests no-regress; report sub-2.md)
- [x] 3: Category-budget + sequence allocator (feed_assembly rewrite) — COMPLETED (11/11 allocator tests; DoD 4/4; report sub-3.md). Carries 2 SP4 hand-offs: (a) orchestrator forward of followed_entities+category_allocation; (b) retire exploration assertion in test_ranking_simulation.py
- [x] 4: Live e2e + offline sim + spec updates + supersede 5e — COMPLETED (live e2e + sim invariants pass; ranking-spec §3a; banners on 5e/control-surface; report sub-4.md). Applied 2 SP3 hand-offs (orchestrator forward + retired exploration test).

## Phase-level checks
- DoD: PASS (live e2e per-category counts/order + Nvidia lift; 166 pipeline / 277 agents tests green)
- Slop scan: PASS (1 surfaced exception: vestigial exploration shim, deferred)
- CSO: PASS (owner-all RLS proven live; env-based creds, no leakage; no new deps/secrets/SQLi)
- Commit: 00b492a

## Follow-ups surfaced (not blockers)
- Vestigial exploration machinery (build_exploration_candidates wired through daily_batch→orchestrator→sim, ignored by new allocator) — remove in a later cleanup phase.
- phase-5b.md + phase-5e.md plan docs still reference 0008 internally; master-plan renumbered 5b→0009 — reconcile when 5b runs.
- ENTITY_BONUS_WEIGHT=0.3 confirmed at sim/e2e (clean lift, base not drowned).
- youtube/x source categories budgeted-but-empty until phase-5d.
