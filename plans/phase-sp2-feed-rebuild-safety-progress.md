# Progress: phase-sp2-feed-rebuild-safety

**Phase file:** plans/phase-sp2-feed-rebuild-safety.md
**Started:** 2026-06-18

## Sub-phase progress
- [x] 1: Preserve + merge existing source rows in `_rebuild_feed` — COMPLETED
- [x] 2: Fail-safe write + non-clobbering backup — COMPLETED
- [ ] 3: Make `allocate_test_feeds.py` source-aware (or refuse real users) — IN PROGRESS
- [ ] 4: Regression test — two-run source survival — IN PROGRESS

## Execution
Mode: sequential (1→2→4 chain on produce_source_reels.py; 3 independent file)
30-slot split (for SP3, not this phase): ai4 tech4 geopolitics4 business4 politics2 environment2 sport3 arts3 youtube2 x2
