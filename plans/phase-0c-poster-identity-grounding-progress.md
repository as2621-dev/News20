# Progress: phase-0c-poster-identity-grounding

**Phase file:** plans/phase-0c-poster-identity-grounding.md
**Started:** 2026-06-18
**Mode:** Sequential (SP1 is ⚠ irreversible — parallel refused)

## Sub-phase progress
- [x] 1: Canonical reference-image store (schema + bucket) — COMPLETED (migration 0019 + bucket live on remote)
- [x] 2: Real-name resolution in concept extraction (L1) — COMPLETED (live eval 4/4; story_date=None today, param wired)
- [x] 3: Fetch + verify + cache canonical photos (L5) — COMPLETED (5 tests; reject writes nothing; returns URL not bytes)
- [x] 4: Wire canonical photo into poster generation (L3 + SERP fallback) — COMPLETED (byte-equality both ways; 33 m0 tests)

## Phase-level activation + checks
- [x] Activation wire: live Supabase/genai clients threaded at orchestrator + fill_batch_posters call sites (canonical path reachable in prod)
- [x] Slop scan: PASS (no findings)
- [x] CSO: PASS — 1 low finding fixed (`_normalize_entity_key` strips path separators / non-printables)
- [ ] Phase-level LIVE DoD: PENDING — billed prod run (regen G7 + Fed-chair posters, verify correct faces) awaiting user go-ahead
- L3 post-gen identity check: noted ABSENT by design (no such check exists in pipeline; not invented)
- story_date=None at call sites today (Digest carries no structured date) — param wired, follow-up to thread a real date

Status: CODE COMPLETE + COMMITTED. Live verification pending user authorization.
