---
title: A safety default read INLINE at one entry point is not a default — every other entry point silently gets the library's
tags: [config, env-flags, entry-points, spend-safety, worker, drift, defaults]
problem_type: architecture-pattern
symptoms: a rule is "enforced" and provably works when you run the local script; the deployed
  worker running the same library does the opposite (spends, or skips a quality gate) and no
  env var can stop it; nothing errors, so the gap survives every audit that reads only the
  script
root_cause: the flag was read inline at ONE call site (`os.environ.get(...)` inside
  `scripts/run_live_batch.py`) and passed down. The library parameter kept its own default,
  which is what EVERY other caller silently got — so "the default" existed in the script, not
  in the system
date: 2026-07-25
---

Found in issues #66 + #65: `scripts/run_live_batch.py` read `SHORTLIST_ONLY` (default 1 = halt
before any paid production) and `ENABLE_SEMANTIC_RELEVANCE_KEY` (default 1 = two-key relevance
lock). `agents/worker/pipeline_routes.py` — the DEPLOYED path, the one a cron fires — passed
neither. It got `shortlist_only=False` and `enable_semantic_relevance_key=False` from the
library signature, so the founder's shortlist-first rule and #51's relevance lock were both dead
in production while being demonstrably true locally.

**The tell:** a safety default that lives in a call site is a *usage convention*, not a default.
Grep the library parameter, not the script: if the library default is the UNSAFE value and one
caller overrides it, every caller you did not read is unsafe.

**The fix that holds:** put the read in ONE module both entry points import
(`agents/pipeline/run_flags.py`, mirroring the older `poster_gate.poster_generation_disabled()`).
Changing the default then changes both paths by construction. The library parameter default is
left alone — it is the API's neutral value, not the product's policy.

**Parse safety defaults as explicit-falsy, never `== "1"`.** The idiom being replaced,
`os.environ.get("SHORTLIST_ONLY", "1") == "1"`, reads `SHORTLIST_ONLY=true` as *produce* and
`ENABLE_SEMANTIC_RELEVANCE_KEY=true` as *off* — both backwards, and both plausible things for an
operator to type into a Railway dashboard. For an opt-out flag, only `0`/`false`/`no`/`off`
(stripped, lowercased) may turn it off; unset, empty and unrecognized values keep the safe
behavior.

**Test the DEFAULT, not the mapping.** `setenv("X", "1") → True` passes even after someone flips
the default to unsafe. The load-bearing tests are `delenv(...) → safe` and a
mutation check that the argument actually reaches the callee (see
`tests/agents/worker/test_pipeline_run_flags.py`: a fake `run_daily_pipeline` that AWAITS the
worker's `ingest_fn`, so ingest kwargs are recorded only because the pipeline really invoked the
closure). Related: [[proving-run-mode-needs-positive-evidence-not-absent-errors]] — once the
flag is armed, log the mode positively so an audit can tell "ran clean" from "never ran".
