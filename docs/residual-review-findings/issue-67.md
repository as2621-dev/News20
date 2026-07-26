# Residual review findings — issue #67 (semantic-relevance run mode in the shortlist artifact)

Commit: `dcd1cc6` · branch `claude/feed-source-revamp-plan-388edf` · reviewed inline
(three lenses: three-state correctness, does-the-test-actually-guard, artifact contract).

All blocking findings were fixed before the commit. Residuals below are accepted, not open bugs.

## Fixed during review (not residual)

- **Empty pool stamped `disabled` while the key was ON.** The first draft only set the mode
  inside `if key_active and canonical_stories:`, so a run whose pool came back empty read as a
  deliberately lexical-only run. Now the mode reports configuration first (`semantic` with zero
  counts), and the counts — not the mode — say nothing was checked. Covered by
  `test_empty_pool_with_the_key_on_still_reports_semantic`.
- **Guard verified by mutation, not by assertion.** Restoring the bare
  `await apply_semantic_relevance_key(...)` at the call site was run against the suite: it fails
  `test_embedding_outage_run_is_stamped_degraded` AND
  `test_healthy_run_is_stamped_semantic_with_counts` (2 failed, 2 passed). The tests genuinely
  encode the WHY.

## Residuals (accepted)

1. **Two artifact shapes exist on disk.** Artifacts written before this commit are bare JSON
   lists; new ones are the `{"shortlist_run": …, "shortlist_entries": […]}` envelope. No
   programmatic reader of the artifact exists today (only `scripts/run_live_batch.py` writes it;
   the replay tests hardcode fixtures), so nothing broke — but any future audit tooling must
   handle both. The envelope is documented on `ShortlistArtifact` in `agents/pipeline/shortlist.py`.

2. **`docs/ops/m1-live-audit-2026-07-24.md` / `-07-25.md` still describe the artifact as a list.**
   Deliberately not touched (out of this slice's staging scope). The next audit doc should read
   `shortlist_run.run_semantic_relevance_mode` and quote it in its verdict — that is the whole
   point of the slice: an audit can now say "proven", not "consistent with".

3. **The stamp rides an OPTIONAL third element of the `ingest_fn` seam.** A future `ingest_fn`
   that returns the two-element form silently gets the default `disabled` stamp. That is honest
   for every fixture ingest (no semantic key runs there), and the alternative — a required third
   element — would break every existing injected ingest for no audit gain. A live ingest path that
   forgets it would under-report, so: any NEW live ingest_fn must return the stamp.

4. **Nothing asserts the artifact is actually written to disk with the header.** The builder is
   unit-tested and `scripts/run_live_batch.py` is a thin call site (`build_shortlist_artifact(result)`
   → `model_dump()`), verified by a manual JSON smoke dump, not by a test — the script has no test
   harness. First live `SHORTLIST_ONLY=1` run should eyeball the `relevance mode ....` line it now
   prints beside `shortlist saved:`.
