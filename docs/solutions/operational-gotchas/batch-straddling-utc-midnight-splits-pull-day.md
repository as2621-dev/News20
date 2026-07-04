---
title: A batch run straddling UTC midnight splits one pull into two census days and hides the feed from Today
tags: [pipeline, census, feed-date, utc, coverage-census, daily-batch, operational]
problem_type: operational-gotcha
symptoms: coverage census reports a deflated windowed hit rate (misses charged on two
  half-days for one batch); the Today reel shows ReelEmpty minutes after a successful
  batch wrote feeds; story_interests rows for one run appear under two pull-day keys
root_cause: three clocks are coupled to raw UTC dates — batch feed_date (chosen at
  start), story_interest_created_at (stamped per row at persist time), and the
  client's todayFeedDate() (UTC "today") — so a run that starts before and ends after
  UTC midnight writes feed_date=D, tags split across D and D+1, and the client starts
  requesting D+1 at midnight
date: 2026-07-04
---

Observed on the M4 day-1 validation batch (2026-07-03 23:36 → 00:13 UTC, i.e. started
16:36 PT): feed_date=2026-07-03, tags split 11 rows (07-03) / 34 rows (07-04), census
windowed read 27.8% while the honest single-run rate was 55.6%, and the personas' Today
reel showed ReelEmpty because "today UTC" was already 07-04 by walkthrough time.

**Rules of thumb:**

- Start persona/production batches so they FINISH before UTC midnight (17:00 PT start
  is already too late for a ~40 min run; be done by ~16:00 PT).
- When reading the census after any run near midnight, check the per-day split
  (`story_interest_created_at::date`) before trusting the windowed rate; compute the
  single-run rate (each niche: ≥1 hit across the run's whole span) as the honest number.
- The Today reel requests `new Date().toISOString().slice(0,10)` — after UTC midnight
  yesterday's feed is only reachable via Archive, and ReelEmpty currently has NO
  navigation to Archive (dead end, recorded defect in
  `docs/ops/m4-persona-validation-2026-07-03.md`).

Related: the long-standing UTC-vs-local feed_date gotcha (go-live-check 2026-06-09);
`docs/ops/coverage-census-day1-2026-07-03.md` (day-key decision).
