# Phase 5f · Sub-phase 2 — YouTube (channels) + Podcasts full population

**Status:** SUCCESS (completed by the orchestrator after the SP2 sub-agent hit a session limit mid-run, then survived two environment issues).

## What shipped
Both axes populated for all 12 archetypes, ≥50 resolved rows/cell on remote.

- **Channels (YouTube Data API v3):** generated ~75–95 candidates/cell, resolved via `channels.list?forHandle` (1 quota unit). 633 rows, **100% thumbnail + subscriber coverage**, 12/12 cells ≥50.
- **Podcasts (iTunes Search API, free):** 1032 rows, **100% artwork coverage**, 12/12 cells ≥50 (89–121/cell). SP2 hardened `itunes_resolve.py` (+94 lines: a process-wide pace-gate at 3.2s/req to stay under iTunes's ~20 rpm/IP cap, a 429-aware backoff schedule, and a `THROTTLED` sentinel separating transient throttle from a genuine miss) and `seed_catalog.py` (+5: opt-in pacing for the live podcast seed). Reviewed — solid, not slop.

## Environment issues found + fixed during the run (see progress file for detail)
1. **Supabase REST host unreachable on this IPv4-only machine** (`<ref>.supabase.co` has no A record without IPv6) → the seeder's supabase-py upsert DNS-failed for every row. Fix: new `scripts/seed_catalog/seed_via_pooler.py` routes upserts through the IPv4 **session pooler** via asyncpg (selftest-validated), reusing `run_seed` unchanged.
2. **Persona-clobber bug:** the driver's per-archetype `--all-archetypes` loop re-upserted cross-tagged sources cell-by-cell, and plain `= excluded.personas` stripped the other archetypes' tags (dropped startup-operator channels to 45). Fix: `--all-archetypes` now does ONE **merged pass** (`archetype_filter=None`) — the in-memory merge unions personas and each unique source is resolved + upserted once (clobber-proof). SP3 also added a `_set_expr` array-union as defense-in-depth. Both axes re-seeded via the merged pass.

## Definition of done — PASS
- Every 12 archetypes ≥50 `youtube_channel` AND ≥50 `podcast` rows (persona-tagged). Per-cell min thumbnail-bearing: yt 57, podcast 68.
- Thumbnail coverage 100% both axes; `topic_tags[0]` ∈ the 8 keys, 0 violations.
- YouTube quota: well within the 10k/day free cap (merged pass resolves each unique channel once).

## Concerns for SP4
- Resolved by SP4: persona-union correctness verified across all axes after the merged re-seed.
- Reproducible artifact: committed `data/{channels,podcasts}.*.json` candidate inputs.
