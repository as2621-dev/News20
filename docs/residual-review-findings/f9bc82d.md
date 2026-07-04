# Residual review findings — f9bc82d (M4 persona validation, day-1 resume)

Docs-only commit; correctness fixes (verification-halt count 4 not ~6; tag split
11/34 verified against prod) were applied before committing. Deferred:

1. **Census pull-day keying vs batch runs.** A batch straddling UTC midnight is one
   pull but two `story_interest_created_at` UTC dates, so the windowed census
   charges misses on both half-days (27.8% windowed vs 55.6% single-run on day 1).
   Operational workaround recorded (finish batches before UTC midnight). If a third
   day gets contaminated the same way, consider keying census days by batch run
   (e.g. cluster tag timestamps) instead of raw UTC date — decide at the go/no-go
   read-off, not before (Rule 2).
2. **ReelEmpty navigation dead end** and the **UTC-midnight Today-reel cutoff** are
   product defects recorded in `docs/ops/m4-persona-validation-2026-07-03.md`
   (browser walkthrough section) — candidate follow-on slices, out of this slice's
   scope.
3. **Multi-niche produce-cap under-provisioning** (feeds 13/13/12 of 26+ slots)
   remains the recorded cap-machinery defect; tuning deliberately deferred until
   the multi-day hit-rate read so the baseline stays uncontaminated.
