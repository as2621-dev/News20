# M1 live audit (issue #47) — 2026-07-24 attempt: **BLOCKED, no batch run**

The fresh founder batch that slice #47 gates on **did not run**. Every Gemini surface on
the key in `.env` returns `429 RESOURCE_EXHAUSTED — "Your prepayment credits are
depleted"`, so the run was stopped at the pre-flight probe rather than executed in a
silently degraded mode. No stories were selected, no rows were written, nothing was
hand-tuned to pass. This file is the durable record of that stop.

Run constraints in force (founder, 2026-07-24): text-only — **no reels, no TTS/dialogue,
no posters or images**; halt at story selection via `SHORTLIST_ONLY`; credit frugality.
Repo state: branch `claude/feed-source-revamp-plan-388edf`, HEAD `b5a2ee1`. Issue #47 was
left OPEN, moved back to `status:backlog`.

## Verdict

| Check | Result |
|---|---|
| Fresh founder batch executed | **NO** — blocked on Gemini billing |
| Shortlist / briefing rows produced | **0** (nothing written to `.agents/shortlists/`) |
| ≥28/30 honest tags | **NOT VERIFIED** — no rows to audit |
| Zero masthead/fragment headlines | **NOT VERIFIED** — no rows to audit |
| Batch-wide category counts, zero `wildcard`/`markets` | **NOT VERIFIED** — no rows to audit |
| Chip/headline on separate lines, on-device | **NOT VERIFIED THIS RUN** — out of scope by founder directive (text-only, no reel production, no device build) |
| Py/TS twin tests green | **PASS, scoped** — see below |

Test scope, stated honestly (Rule 12): at HEAD `b5a2ee1`, `pytest tests/agents/pipeline`
= **631 passed** and `npx vitest run` = **724 passed / 90 files**. The repo-wide `pytest`
additionally has **1 pre-existing failure unrelated to this slice** —
`tests/agents/worker/test_interview_route.py::test_deeper_turn_returns_terminal_with_gemini_mocked`
(asserts `terminal`, gets `question`). This attempt changed no code, so that failure is
inherited, not caused; it is recorded here rather than rounded off to "green".

## Evidence — the blocker

Two probes, one call each (cheapest possible), against the key loaded from `.env`:

```
embeddings  gemini-embedding-001 :batchEmbedContents  → 429 RESOURCE_EXHAUSTED
            "Your prepayment credits are depleted. Please go to AI Studio at
             https://ai.studio/projects to manage your project and billing."
            (3 attempts, sleeps 2s then 4s, then PipelineStageError)

text        gemini-3.5-flash  generateContent          → 429 RESOURCE_EXHAUSTED
            same "prepayment credits are depleted" message
```

This is **billing, not per-minute rate limiting and not the free-tier daily cap**: the
message names depleted prepayment credits, and it hits the *text* surface too, so it is
project-wide rather than embedding-specific.

**The key was never rotated in this repo.** `.env` last modified `Jul 19 11:56:37 2026`
— before the 2026-07-24 statement that the key had been updated. There is exactly one
`.env` in the tree (plus `.env.example`), and no `GEMINI_API_KEY` in the shell
environment that could shadow it.

## Why the run was stopped instead of attempted

The semantic relevance key (#51) **fails safe to strict lexical**, by design
(`agents/ingestion/interest_semantic.py:196-220`, called at
`agents/ingestion/interest_keyed_pipeline.py:463-467`). Semantic clustering
(`ENABLE_SEMANTIC_CLUSTERING`, #34) is wrapped the same way, and the pre-halt dedup judge
is fail-open (`agents/pipeline/produce_dedup.py`).

So with Gemini dead the batch would **not** have errored — it would have completed as a
lexical-only run and written a shortlist that looks like a valid audit artifact while
exercising only half of the two-key lock. That is the same shape as
`.agents/shortlists/2026-07-20-shortlist.json` (19 candidates, **10 non-English**, "Fish
And Chips Spots Worth Knowing About At The Jersey Shore" tagged `environment`) — whether
that artifact was itself a lexical-only fallback is consistent with the evidence but not
proven from the artifact, which carries no fallback flag. Re-running under the same
outage would burn GDELT/Supabase work to produce a second artifact of the same unusable
kind, so the run was halted at the probe (founder directive: stop and report if
embeddings still 429).

**Detection, if a future run degrades:** the fallback is logged loudly —
`semantic_relevance_embed_failed` (error level, with a `fix_suggestion` naming the
skipped tightening). Grep the run log for it before trusting any shortlist. The *artifact*
is what lacks the signal: `SemanticRelevanceStats.fell_back_to_lexical` is discarded at
the bare-`await` call site, so it never reaches `DailyPipelineResult` or the shortlist
JSON (filed as a follow-on, see bottom).

## What did run (all free, no Gemini calls)

Dry-run preflight, scoped to the founder profile:

```bash
ONLY_USER_EMAIL=ash@gmail.com SHORTLIST_ONLY=1 DISABLE_POSTER_GEN=1 MAX_PRODUCE=0 \
  .venv/bin/python scripts/run_live_batch.py
```

```
=== LIVE BATCH (4a-SP3) — feed_date=2026-07-24 ===
mode=DRY-RUN (free)  max_produce=uncapped  lookback_days=1

--- SCOPED TO 1 USER(S): ash@gmail.com ---

--- PREFLIGHT ---
  ingest source ................. bigquery (BigQuery (unthrottled))
  interests in taxonomy ......... 237
  interest→segment lookup ....... 237
  active users (profiles) ....... 1
  distinct followed interests ... 26
  outlets bias lookup ........... 240
  produce cap headroom .......... 2.0x (demand → render pool)
  per-category produce caps ....
      ai               10
      arts             8
      business         12
      geopolitics      8
      sport            10
      tech             4
      x                2
      youtube          6

DRY-RUN complete — no paid calls made. Re-run with RUN_LIVE_BATCH=1 to produce.
```

The configuration is healthy — 26 followed interests, caps summing to 60 against a
30-slot feed. The only missing input is Gemini credit.

## Prod state after this attempt (nothing was written)

- `daily_feeds` for `2026-07-24`: **0 rows**. For `2026-07-25`: **0 rows**.
- `SHORTLIST_ONLY=1` halts above `_produce_story_pool` (`agents/pipeline/daily_batch.py`,
  shortlist-only halt) and returns `feeds=None`, so it writes no `daily_feeds`.

Consequence for the next attempt: **the produce-once gate is not armed for today** — a
full run can still claim `feed_date=2026-07-24` (or whichever local date it starts on)
without colliding with pre-existing rows.

Cost note for the credit-frugality rule: shortlist mode removes the *expensive tail*
(script LLM → TTS → posters), not all spend. The halt sits below ingestion, the
relevance-key embeddings and the LLM dedup judge, so a resume run does bill Gemini —
just at a small fraction of a producing run.

## To resume (after credits are restored)

1. Put the funded key in `.env` as `GEMINI_API_KEY`, then confirm the rotation actually
   landed — `load_dotenv` does **not** override an already-set shell variable, so read
   the file directly rather than the process env:

   ```bash
   stat -f "%Sm %N" .env    # BSD/macOS; use `ls -l` elsewhere
   .venv/bin/python -c "import hashlib;from dotenv import dotenv_values;\
   print(hashlib.sha256(dotenv_values('.env')['GEMINI_API_KEY'].encode()).hexdigest()[:10])"
   ```

   An unchanged mtime or digest means the new key never landed. Then re-probe with one
   cheap `embed_texts` call before committing to a batch.

2. Text-only audit run. Start it so it **finishes before UTC midnight**: the script picks
   `target = date.today()` in *local* time with no env override (`scripts/run_live_batch.py`,
   edit the line if you need a different date), while the client asks for "today" in UTC —
   see `docs/solutions/operational-gotchas/batch-straddling-utc-midnight-splits-pull-day.md`.

   ```bash
   RUN_LIVE_BATCH=1 SHORTLIST_ONLY=1 DISABLE_POSTER_GEN=1 MAX_PRODUCE=0 \
     ONLY_USER_EMAIL=ash@gmail.com LOOKBACK_DAYS=1 \
     ENABLE_SEMANTIC_RELEVANCE_KEY=1 ENABLE_SEMANTIC_CLUSTERING=1 \
     nohup .venv/bin/python scripts/run_live_batch.py > /tmp/m1-audit.log 2>&1 &
   disown
   ```

3. Audit `.agents/shortlists/<date>-shortlist.json` row by row: tag honest? headline
   informative / non-masthead / non-fragment? English? recent? relevant to the matched
   interest? Then batch-wide: non-zero ai/business/environment/politics/arts, zero new
   `wildcard`/`markets`. Grep the log for `semantic_relevance_embed_failed` first — if it
   fired, the run was lexical-only and the audit is void.

**Known gap the next audit will surface:** issue #63 (English-only filter at GDELT
admission) is not built, so non-English candidates are expected in the shortlist. They
are an honest failure to report against #63 — not something to hand-remove from the rows.

**Delta from #47's literal wording:** the shortlist is the *selected candidate pool*
(headline, outlet count, resolved category, matched interests), not a 30-row produced
briefing with generated story text. Under the text-only directive that pool is what the
tag-honesty and headline criteria can be judged on; the on-device chip/headline criterion
needs a full producing run plus a device build and stays deferred.

## Defects found while preparing this run (not caused by it)

Both are in the **deployed worker path**, which no live-batch env flag reaches:

1. `agents/worker/pipeline_routes.py` calls `ingest_active_interests` without
   `llm_client` or `enable_semantic_relevance_key`, and both default off
   (`agents/ingestion/interest_keyed_pipeline.py`, guard at the call site) — so the
   two-key relevance lock (#51) is **never armed in production**. Only
   `scripts/run_live_batch.py` arms it.
2. The same file calls `run_daily_pipeline` without `shortlist_only` (library default
   `False`), so a worker/cron run **produces and writes `daily_feeds` unconditionally** —
   the founder's shortlist-first rule is enforced only in the local script.

Filed as **#65** (semantic key never armed in the worker) and **#66** (worker produces
unconditionally). A third, **#67**, stamps `fell_back_to_lexical` into the shortlist
artifact so a future audit can prove which mode produced it.
