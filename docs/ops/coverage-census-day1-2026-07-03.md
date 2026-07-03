# Coverage census — Day-1 run (2026-07-03)

The per-niche coverage census (slice #5) — the instrument for the PRD's riskiest
assumption, the ≥60% direct-niche hit rate — run for the first time against prod's
stored `story_interests` tags. This file is the durable ops record; the 3-day
go/no-go read-off completes after 2 more daily batch runs (tracked in the follow-up
issue, see bottom).

## How to run

```bash
.venv/bin/python scripts/coverage_census.py --days 3          # human report, last 3 UTC days
.venv/bin/python scripts/coverage_census.py --days 3 --json   # machine JSON (slice #10 / archival)
.venv/bin/python scripts/coverage_census.py --start-date 2026-07-03 --end-date 2026-07-05
```

Read-only: the census only issues `.select()` reads (`users`,
`user_interest_profile`, `interests`, `story_interests`) — it never triggers
ingestion or mutates the pool.

## What the census measures

- **Direct hit** = a story carrying a `story_interests` row on that exact interest
  node (ancestor tagging only walks UP, so a row on a leaf interest is a genuine
  niche hit, never inherited). Counted per interest per UTC pull-day.
- **Day key** = the UTC calendar date of `story_interest_created_at` (the day the
  batch stamped the tag). Decided here to resolve the known UTC-vs-local `feed_date`
  gotcha — this is the honest "per day of real pulls" axis.
- **Hit rate** = fraction of `(followed-interest, eligible-present-day)` cells that
  drew ≥1 direct hit, vs the 60% target. Each interest is one feed section, so this
  answers "would each persona's sections fill from direct niche stories that day?"
- **Missing day** (no batch ran, 0 tags pool-wide) is distinguished from a **dry
  niche** (batch ran, this interest drew 0). An interest is only scored from the day
  it was followed (`profile_created_at`), so a mid-window addition is not charged a
  miss for days before it existed.

## Day-1 finding (honest, not faked)

The 3 personas were seeded **today (2026-07-03)** — all 9 followed micro-interests
have `profile_created_at = 2026-07-03`. The only present pull-days currently in the
pool are **2026-06-20 and 2026-06-22** (latest `story_interests` row: 2026-06-22),
both of which **predate** the personas. So:

- Over the last-3-days window (2026-07-01 → 2026-07-03) every day is **MISSING** — no
  batch has produced+persisted niche tags in that window.
- Over a wide window (2026-06-20 → 2026-07-03) the instrument reads the real pool
  correctly and the **ladder context lights up** (roots have material — `sport:23`,
  `tech:33`, `business:23`, `sport.cricket:6`) while **every freshly-seeded persona
  leaf is DRY** (0 direct hits). This is expected: the daily batch has not yet
  produced+persisted stories tagged to the new leaf interests.
- **Hit rate is `0/0` (no eligible cells)** because no present pull-day is on/after
  the personas' seed date. The ≥60% read-off is therefore **not yet computable** — it
  needs ≥3 real pull-days AFTER 2026-07-03. This is the operational residual; it
  cannot complete today and is NOT faked.

Note on the store: `story_interests` rows are persisted for the **produced/gated**
story subset (persist step 9, `agents/pipeline/persist.py`), keyed on the pull day.
The census reads that node-tagged store — the only persisted tag store that exists.
If a future slice persists the full ingested candidate pool (not just produced
stories), the census picks it up automatically (same table, same columns).

### Actual output — wide window (proves the instrument reads real stored tags)

```
========================================================================
PER-NICHE COVERAGE CENSUS
window: 2026-06-20 → 2026-07-03 (UTC pull dates)
present days (2): 2026-06-20, 2026-06-22
MISSING days (12) — no batch run: 2026-06-21, 2026-06-23, ... 2026-07-03
========================================================================

[founder] persona.founder@news20.seed
  hit rate   0.0% (0/0 interest-day cells) vs 60%  → BELOW
    ai.foundation-models         hits=  0 days=0/0  DRY
        ladder:  ai.foundation-models:0 ai:0
    business.venture-capital     hits=  0 days=0/0  DRY
        ladder:  business.venture-capital:0 business:23
    tech.developer-tools         hits=  0 days=0/0  DRY
        ladder:  tech.developer-tools:0 tech:33

[cricket] persona.cricket@news20.seed
  hit rate   0.0% (0/0 interest-day cells) vs 60%  → BELOW
    sport.cricket.india-team     hits=  0 days=0/0  DRY
        ladder:  sport.cricket.india-team:0 sport.cricket:6 sport:23
    sport.cricket.ipl            hits=  0 days=0/0  DRY
        ladder:  sport.cricket.ipl:0 sport.cricket:6 sport:23
    sport.cricket.world-cup      hits=  0 days=0/0  DRY
        ladder:  sport.cricket.world-cup:0 sport.cricket:6 sport:23

[chip] persona.chip@news20.seed
  hit rate   0.0% (0/0 interest-day cells) vs 60%  → BELOW
    geopolitics.chip-export-controls hits=  0 days=0/0  DRY
        ladder:  geopolitics.chip-export-controls:0 geopolitics:0
    tech.semiconductors.nvidia   hits=  0 days=0/0  DRY
        ladder:  tech.semiconductors.nvidia:0 tech.semiconductors:0 tech:33
    tech.semiconductors.tsmc     hits=  0 days=0/0  DRY
        ladder:  tech.semiconductors.tsmc:0 tech.semiconductors:0 tech:33

------------------------------------------------------------------------
OVERALL hit rate   0.0% (0/0) vs 60%  → BELOW TARGET
------------------------------------------------------------------------
```

## 3-day go/no-go read-off (residual — completes after merge)

Run `scripts/coverage_census.py --days 3` after the next 2 daily batches (targets
2026-07-04 and 2026-07-05), append each day's output below, and read off the
overall hit rate vs 60%. Tracked in the follow-up issue so it is not silently
dropped.

- [ ] 2026-07-03 batch persisted → census re-run — **FAILED: batch ran (slice #10) but
      produced 0 stories (Gemini prepay credits depleted mid-run, 429). No tags
      persisted; this day does NOT count as a pull-day. See
      `docs/ops/m4-persona-validation-2026-07-03.md`.**
- [ ] 1st successful post-top-up batch persisted → census re-run
- [ ] 2nd successful batch day persisted → census re-run
- [ ] 3rd successful batch day persisted → census re-run → **go/no-go read-off recorded**
      (earliest 2026-07-06 if batches run 07-04/05/06)
