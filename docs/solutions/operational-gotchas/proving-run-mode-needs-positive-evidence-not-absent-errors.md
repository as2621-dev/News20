---
title: Proving a shortlist ran semantic needs POSITIVE log evidence — absence of the failure event proves nothing
tags: [audit, shortlist, semantic-relevance, embeddings, run-mode, evidence, scratchpad, operational]
problem_type: operational-gotcha
symptoms: an audit must certify a shortlist was produced with the two-key (semantic) lock;
  grepping the run log for semantic_relevance_embed_failed finds nothing and this is
  presented as proof of a healthy semantic run
root_cause: zero `semantic_relevance_embed_failed` events is ambiguous — the key may have
  run cleanly OR never been armed at all (it defaults OFF; only scripts/run_live_batch.py
  arms it, the worker path does not — issue #65). Silence is consistent with both.
date: 2026-07-25
---

Used in the 2026-07-25 M1 audit (`docs/ops/m1-live-audit-2026-07-25.md`, #47). The
companion entry `embedding-outage-downgrades-run-to-lexical-only.md` says grep for the
failure event; this entry is the other half: a clean grep is necessary, never sufficient.

**The proof standard — three positives, one negative:**

1. `semantic_relevance_key_completed` (info level) present, with sane numbers
   (`stories_checked`, `pairs_evaluated`, `pairs_rejected` > 0, `embed_call_count` > 0);
2. real HTTP lines: `POST …/models/gemini-embedding-001:batchEmbedContents` returning 200;
3. the log's tail names the exact artifact path it saved (ties log to artifact — mtimes
   should agree);
4. and zero `semantic_relevance_embed_failed` / zero error-level events.

**Where the log lives, and why you must quote it:** live-batch runs started by a Claude
session write to that session's scratchpad —
`/private/tmp/claude-501/<project-slug>/<session-uuid>/scratchpad/` — which is ephemeral
and session-scoped (it is NOT `/tmp/m1-audit.log` even when a runbook says so; `find`
across all session dirs, filter by mtime). Until #67 stamps `fell_back_to_lexical` into
the shortlist JSON itself, the ONLY durable move is to quote the proof lines verbatim
into the committed ops doc. An audit that merely cites the log path certifies nothing
once the scratchpad is reaped.
