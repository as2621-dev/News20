---
title: A SQL prefilter that runs BEFORE its Python twin must be the LOOSER of the two
tags: [bigquery, sql-python-twin, gdelt, ingestion, filtering, fail-open, language]
problem_type: pattern
symptoms: rows vanish from a batch with no log line anywhere; the Python gate that
  "owns" the decision never sees the rows it was supposed to judge; a code path is
  provably correct in tests yet the live pool is quietly smaller than expected
date: 2026-07-25
---

The #50 lexical key established the SQL⇄Python twin: a pure module is the tested
specification, `_BATCH_SQL` is its runtime twin. Slice #63 (English-only admission)
added a case that twin pattern does NOT cover — a filter split across BOTH sides,
where SQL runs first.

**The asymmetry that matters.** When two stages filter the same rows and the first
stage is a SQL `WHERE`, its rejections are *unrecoverable and unlogged*: the row never
reaches Python, so no counter increments, no warning fires, and the only symptom is a
smaller pool. Therefore the stages are not interchangeable and "keep them identical"
is the wrong goal. The rule is:

> **The earlier/cheaper stage must admit a strict SUPERSET of the later one.**
> Every rule it applies must be one the authoritative stage also applies, evaluated
> at least as loosely. New tightening always goes in the LATER stage.

In #63 the near-miss was tiny and typical: `_ENGLISH_SOURCE_CODES = {"eng", "en"}` in
Python, but the emitted SQL compared `= 'eng'`. A row stamped `srclc:en` would have
been dropped in BigQuery with nothing to grep for. Fix: generate the SQL's accepted
list *from the Python constant* (`IN ('en', 'eng')`), so the two cannot drift, and pin
it with a test asserting the SQL accepts every code Python accepts. Generating the
predicate from the shared constant is strictly better than asserting two literals
match — there is one place the rule is written.

**Corollaries worth reusing**

- **Split by what each side can actually express, not by convenience.** The SQL half
  took the rule needing no string processing (regex-extract a metadata field); the
  Python half took everything needing HTML-entity decoding + Unicode script analysis,
  which in RE2 is where a twin genuinely drifts. Pushing the hard half into SQL to
  "keep it symmetric" would have bought worse code and a real drift risk.
- **A filter that fails open still has to be countable.** Ties admit, but the verdict
  carries *why* (`language_evidence`) so the caller can log an aggregate
  `blind_admissions` count. Compare
  [[no-signal-returns-none-not-default-plus-twin-drift-test]]: a classifier that
  cannot classify returns None rather than its consumer's default — same instinct,
  applied to a gate instead of a labeler.
- **Aggregate per call, never per row.** 5000-row batches make per-row rejection logs
  useless. `{reason: count}` maps keep the signal readable and still let you tell
  *which rule* is doing the work in production.
- **Capitalization is a free proper-noun discriminator.** For the diacritic-density
  rule, counting only *lowercase* accented words separates "Beyoncé, Céline win
  Grammys" (English, accents in names) from "programy zaczęły raportować" (foreign
  prose) at zero cost. Useful anywhere a heuristic must not fire on proper nouns.
- **When a live schema cannot be verified (zero-API guardrails), prefer the failure
  that is loud.** #63 reads GKG `TranslationInfo` unverified; a wrong column name
  fails the whole query with `Unrecognized name:` rather than silently returning
  nothing — that is an acceptable unknown, and it is recorded in
  `docs/residual-review-findings/issue-63.md` instead of being smoothed over.
