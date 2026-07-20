---
title: Deterministic taxonomy-label → interest_search_query backfill (scoped, idempotent, no LLM)
tags: [interests, interest-search-query, ingestion-skip, backfill, prod-write, idempotent, split-anchor-terms, match-terms, seed-catalog]
problem_type: pattern
symptoms: taxonomy interest nodes are seeded (slug/label/depth/parent) but have a NULL
  interest_search_query, so ingestion SILENTLY SKIPS them and the sub-niches are
  seeded-but-unfollowable; the feed never surfaces those interests
root_cause: seeders mint interest rows without a search query; both ingestion paths require
  usable terms — gdelt_bigquery.match_terms empties → "gdelt_bq_interest_no_terms" skip,
  anchor_scalpel.split_anchor_terms empties → no DOC anchor
date: 2026-07-05
---

When a seeder mints interest nodes with a NULL `interest_search_query`, ingestion skips
them (see [[gdelt-bigquery-workhorse-doc-scalpel-split]]). Back-fill the column
**deterministically — NO LLM (Rule 5)**; a label→query map is code, not a judgment call.
Reference impl: `scripts/seed_catalog/backfill_interest_query.py` (issue #28).

**The query must satisfy BOTH ingestion tokenizers**, or the write is pointless:
- `agents/ingestion/adapters/gdelt_bigquery.py::match_terms` — lower-cases, keeps
  alphanumeric tokens **≥3 chars**, drops `_QUERY_STOPWORDS` + `_GENERIC_BROAD`. So a
  2-char root like "AI" contributes ZERO match terms — don't rely on it as the only anchor.
- `agents/ingestion/anchor_scalpel.py::split_anchor_terms` — splits on **comma** into
  exact-phrase DOC anchors. So emit **comma-separated** phrases, not one long string
  (a whole-label exact phrase rarely matches an article).

Label→query heuristic that works (traceable, same input→same output):
- Split the label into comma phrases on a **spaced** ` & `, a `/`, or an en/em-dash. Match
  the ampersand ONLY with surrounding spaces so a spaceless entity `&` (e.g. `M&A`) is
  preserved, not shattered into `M`/`A`. Turn intra-word hyphens into spaces
  (`Foreign-policy`→`Foreign policy`). Regex: `\s+&\s+|\s*/\s*|\s*[–—]\s*`.
- Qualify a **generic** label (every ≥3-char token is a stopword/event-word/format-filler,
  e.g. "News & analysis") with the root label — but skip the prepend if the root term is
  already in the label (avoids "AI, AI news, analysis").
- **Assert ≥1 term from both tokenizers at plan time** and raise if empty — a skippable
  query must fail loud, never reach prod.

**Scoped idempotent prod backfill pattern** (reuse for any one-shot column fill):
- Re-derive the exact target key set from the SAME function the seeder used (here
  `seed_v2.subniche_interest_slug`/`_slugify`) so you target precisely the intended rows.
- `UPDATE ... SET col=$2 WHERE key=$1 AND col IS NULL` — the `IS NULL` predicate makes it
  idempotent (re-run = 0 rows) AND non-clobbering (a converged row that already has a value
  is untouched). Parameterized only; never log `SUPABASE_DB_URL`.
- Dry-run by DEFAULT (compute + print every key→value + change count), `--live` to write.
- Prove scope with a before/after count of rows OUTSIDE the target set that already have a
  value (`WHERE col IS NOT NULL AND key != ALL($1)`) — must be identical.
- Connect via `seed_via_pooler._connect` (IPv4 session pooler, `SUPABASE_DB_URL`),
  `.venv/bin/python` + asyncpg (system python lacks pg drivers). See [[news20-interest-query-gap]].

Verified on prod (#28): dry-run 118 write + 2 skip; live changed 118; non-v2 snapshot
109==109; second live run changed 0.

**Sweep addendum (issue #36, 2026-07-07)** — `scripts/seed_catalog/backfill_queryless_interests.py`
generalizes this to ALL queryless interests (not a known key set):
- **The SQL "empty" predicate must mirror the consumer's Python check exactly.** The
  pipeline skips on `.strip()` (ALL whitespace) — so the sweep predicate is
  `col IS NULL OR col ~ '^\s*$'`, NOT `btrim(col) = ''` (btrim trims only spaces; a
  `"\t"` row would be pipeline-skipped yet sweep-invisible). Make any FakeConn test
  double mirror the SQL, not just the Python.
- Roots need CURATED comma-phrase queries (`ROOT_QUERIES` in the sweep script — a
  1-word root label like "AI" can't be derived; fail loud on an unmapped root).
  User-minted `mint_interest_ladder` rungs are context-free single segments
  ("Business" under ai.*) — ALWAYS root-qualify them (word-boundary presence check,
  qualifier expanded via `ROOT_QUERY_QUALIFIERS` so "AI" → "artificial intelligence"
  survives the ≥3-char tokenizer).
- Skip the before/after table-wide count "proof": it races concurrent writers
  (mint RPC) and the per-row re-checked predicate already IS the no-clobber
  guarantee. `rows_changed` + a re-fetch of remaining queryless rows suffice.
- The loud-fail twin: `build_active_interest_set` WARNING-logs each
  `queryless_interest_skipped` and the batch summary + `IngestionResult` carry
  `skipped_queryless_interests` **explicitly 0 when none** — absence of the field is
  never proof of no skips (Rule 12).
