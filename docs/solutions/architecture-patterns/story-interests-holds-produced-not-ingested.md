---
title: story_interests holds PRODUCED/gated stories, not all ingested candidates
tags: [story_interests, shared-pool, coverage, analytics, ingestion, persistence]
problem_type: pattern
symptoms: a census/analytics read over story_interests shows far fewer niche tags than
  the ingestion batch reported (e.g. batch logs "675 candidates tagged" but the table has
  ~0 new rows for that day); coverage looks structurally low
root_cause: story_interests rows are written by persist.py step 9 ONLY for the gated story
  subset that goes through production (write→render→persist); ingested-but-not-produced
  candidates are tagged in memory (IngestionResult.story_interest_tags) but never persisted
date: 2026-07-03
---

The niche pipeline stamps every ingested candidate with its interest node + depth
**in memory** (`agents/ingestion/interest_keyed_pipeline.py` → `IngestionResult.
story_interest_tags`), but those tags only reach the `story_interests` table for stories
that survive gating and get PRODUCED — persistence is `agents/pipeline/persist.py` step 9,
called per-produced-story inside `_produce_story_pool` (`agents/pipeline/daily_batch.py`),
not per-ingested-candidate. There is no separate ingested-candidate store; `story_interests`
is the only persisted node-tagged pool.

Consequence for anything reading the pool (coverage census slice #5, future ranking/
analytics): a count over `story_interests` measures **produced** direct-niche stories
(what actually fills feed sections), NOT ingestion recall. Production/gating attrition
happens before the count. Don't read a low `story_interests` count as "ingestion can't
find niche stories" — it may be "ingestion found them but gating/produce-cap dropped them
pre-persist". If you need ingestion recall specifically, that's a different measurement
(instrument the in-memory IngestionResult, or persist the full pool first).

Two adjacent facts that bit here:
- Cross-day identity (migration 0006) reuses a story_id, and story_interests inserts (not
  upserts) with `uq_story_interest UNIQUE(story, interest)` — so a tag row is created once,
  with `story_interest_created_at` = the FIRST pull that produced it. That timestamp is the
  honest "pull day" key for time-series reads.
- PostgREST caps a single `.select()` at 1000 rows by default; any unpaginated read of a
  growing table (story_interests) silently truncates. Paginate with `.range(offset, offset+
  999)` until a short page. See `_fetch_all` in `agents/pipeline/coverage_census.py`.
