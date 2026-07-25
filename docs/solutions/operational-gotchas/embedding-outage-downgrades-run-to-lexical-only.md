---
title: An embedding outage does not fail the batch — it downgrades the run to lexical-only and the shortlist looks normal
tags: [gemini, embeddings, quota, billing, ingestion, relevance-key, clustering, daily-batch, operational]
problem_type: operational-gotcha
symptoms: a live batch completes "successfully" but the shortlist is full of off-topic
  and non-English rows; embeddings log 429 mid-run yet the run keeps going; two runs of
  the same code produce wildly different tag honesty
root_cause: the semantic relevance key (#51) and semantic clustering (#34) both fail SAFE
  to the lexical path on any embedding failure, by design — so an embedding outage removes
  the second key of the two-key lock without removing the run
date: 2026-07-24
---

Credit depletion is only one instance; a model retirement (the `text-embedding-004` 404)
or a transient 5xx lands in the same `except` and produces the same downgrade.

**Probe before the batch, never after.** One call is the whole test:

```python
import asyncio
from dotenv import load_dotenv; load_dotenv(".env")
from agents.pipeline.llm_clients import LLMClient
from agents.pipeline.clustering.embeddings import embed_texts

asyncio.run(embed_texts(["smoke"], llm_client=LLMClient()))   # 768-d vector == go
```

If it fails, stop. The batch will otherwise run to completion and write a shortlist that
*looks* like evidence: `apply_semantic_relevance_key` catches the failure and leaves the
lexically-matched interest ids intact (fail-safe, never fail-open), so every RC3-class
false positive the semantic half exists to kill is admitted again.

**Detecting it after the fact:** the run *log* is loud —
`agents/ingestion/interest_semantic.py` logs `semantic_relevance_embed_failed` at error
level with a `fix_suggestion` naming the skipped tightening. Grep for it before trusting
any shortlist. The *artifact* is what's silent: the returned
`SemanticRelevanceStats.fell_back_to_lexical` is dropped at the bare-`await` call site in
`agents/ingestion/interest_keyed_pipeline.py`, so it never reaches `DailyPipelineResult`
or the shortlist JSON — a shortlist file alone cannot tell you which mode produced it.

**Read the 429 body; the two causes need different people.**

- `"Your prepayment credits are depleted…"` → **billing**. Project-wide: the *text*
  surface (`gemini-3.5-flash`) 429s with the same message, not just embeddings. Only the
  founder can fix it (top up, or a key on a funded project). Retries are pointless.
- `"quota … per day/minute"` with a `QuotaFailure` `quotaId` → **rate/free-tier limit**.
  Waiting or a smaller batch can work; dump the details to get the `quotaId`.

A passing *probe* still does not imply a passing *batch* — a key can serve a single
`generateContent` and be out of batch quota (memory `news20-gemini-credits-depleted-2026-06-17`).

**Scope:** only `scripts/run_live_batch.py` arms the semantic relevance key at all. The
deployed worker (`agents/worker/pipeline_routes.py`) passes neither `llm_client` nor
`enable_semantic_relevance_key` to `ingest_active_interests`, so production ingestion is
lexical-only every run, outage or not — a separate defect, not a symptom of this one.
