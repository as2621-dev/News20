# Residual review findings — Slice #31 (wire X theme reels into the nightly batch)

Panel: correctness/logic, simplicity/reuse, data-integrity — run as sequential
independent passes at commit `3d3b9fb`. No correctness or data-integrity defect
survived verification; all three acceptance criteria hold (end-to-end integration
test through the real `run_daily_pipeline` path, R3 FK-guard test, same-day re-run
idempotency test). The findings below are ADVISORY / ops (no concrete code fix).

## Advisory (ops / follow-on)

### A1 — No production sweep runner yet (flip-on precondition)
The gather reads today's `x_cluster_sweeps` rows; `sweep_followed_clusters`
(slice #23) still has NO production caller, so prod has 0 sweep rows. Flipping
`RUN_X_THEMES=1` today is safe but yields empty ladders (every eligible user's x
slots roll honestly to the news floor). A follow-on must wire the sweep stage
(once daily, before/inside the batch — note the temporal coupling: the sweep must
be keyed to the SAME `sweep_date` as the batch's `target_date`).

### A2 — Prod migration drift: 0030 + 0033 remain unapplied/unrecorded
Found while validating the join/read stages against prod: `0030_user_deferred_questions`
and `0033_user_source_clusters` are neither applied nor recorded. `0031_x_cluster_sweeps`
(this slice's dependency, unapplied drift from #23) was applied + recorded during this
slice (expand-only new table). 0030/0033 belong to other slices' surfaces — left for
their owners (concurrent-agents convention).

### A3 — Flag-on semantics for eligible users' per-account X reels (product sign-off)
By #24's design (unchanged here), a user PRESENT in `x_theme_candidates_by_user`
has their x slots owned by the theme ladder, so their individually-followed
x_account SOURCE reels are ignored for the x budget. Slice #31 narrows this to
users following >= 1 X cluster (absent users keep the legacy x fill), but a
cluster-follower who ALSO follows individual X handles loses those per-account x
reels when the flag is on. Related to #24's R2 (roundup label divergence); flag
for product sign-off with the RUN_X_THEMES rollout.
