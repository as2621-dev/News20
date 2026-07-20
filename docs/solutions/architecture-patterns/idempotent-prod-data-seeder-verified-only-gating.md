---
title: Idempotent prod DATA seeder — verified-only member gating + double-run proof
tags: [seed, idempotent, asyncpg, pooler, prod, upsert, yt-dlp, data-integrity]
problem_type: architecture
symptoms: A slice must seed net-new taxonomy/source/relationship rows into prod (not DDL) from a hand-authored catalog, some of whose entries can't be honestly verified; re-runnable without duplicating; must never write unverifiable data
root_cause: n/a (recipe)
date: 2026-07-04
---

Established building the greenfield v2 catalog seeder (slice #15, `scripts/seed_catalog/seed_v2.py`).
Reuse for any re-runnable prod DATA seed (interests, content_sources, clusters, members).
Distinct from `apply-migration-to-prod-asyncpg-pooler.md` (that's DDL; this is rows).

**Connection + write path — reuse, don't rebuild.** Same IPv4 session pooler as migrations
(`SUPABASE_DB_URL`, `.venv/bin/python`, `asyncpg` + `statement_cache_size=0`, jsonb codec).
`seed_via_pooler._connect` / `_flush` already implement `insert … on conflict (natural_key)
do update` with `text[]` UNION accumulation + `Decimal` coercion for `numeric`. Call `_flush`
directly with `[("table", rows)]` — the `_PoolerClient().table().upsert().execute()` shim only
exists to emulate supabase-py for the *archetype* seeder; a greenfield seeder holding plain
dict rows should skip it.

**Idempotency = deterministic keys + `on conflict`, PROVEN by a double-run.**
- Every write keys on a natural key: `interest_slug`; `(content_source_type, external_id)`;
  `cluster_slug`; the `(cluster_id, source_id)` member composite.
- Slugs must be a **deterministic** `_slugify` (lower, `&`→`and`, collapse non-alnum to `-`)
  so the same catalog entry maps the same key every run.
- Mint-or-converge = `on conflict (slug) do nothing`; a pre-existing row (e.g. `sport.cricket`)
  converges silently. **Guard the silent-swallow failure mode:** fail loud if the *batch itself*
  contains two entries slugifying to one key (else the second's data is lost, miscounted as a
  converge).
- **Proof, not assertion:** snapshot `select count(*)` on every touched table, run `--live`
  TWICE, assert before==after on the second run. This is the ground-truth idempotency check the
  acceptance step needs — mock-DB unit tests can't prove the real `on conflict` clauses.

**Verified-only relationship gating (never write unverifiable data).** A relationship row
(cluster member) may reference ONLY a followable that **already exists in prod** — verification
by existence, the honest ground truth (a JSON `already_seeded` flag is an annotation, not proof;
prefer prod state). Two rules that bit:
- The plan-time gate (`handle in verified_set`) and the seed-time lookup (`select … where
  lower(external_id)=lower($1)`) must share ONE predicate. If plan normalizes `ltrim('@')` but
  the lookup compares verbatim, a handle can pass plan yet miss seed → empty structure.
- Resolve members BEFORE upserting the parent structure; if zero resolve, **skip the structure**
  (don't commit it empty). "Not seeded empty" enforced at the write boundary, not just at plan.

**Keyless yt-dlp channel resolve + sanity gate.** Resolve `@handle` → `UC…` via
`youtube_resolve.resolve_many` (opts metadata-only, thread-pooled, concurrency ~4). A 404/
`DownloadError` → clean `None` → drop + log with reason, never guess. **Before any write**, abort
if `dropped / unique_attempted > 0.20` (denominator = deduped unique handles, matching the deduped
drop numerator) — a mass failure is probable IP-throttle, not genuine death. Genuine minority
deaths (10% of recognizable renamed orgs) are fine to drop.

**Safety shape.** Dry-run is the DEFAULT (resolve + full per-root/per-type report + every drop
with reason); `--live` required to write. Per-parent transaction so a mid-parent failure rolls
back structure + children together (no orphan). Never log the connection string.
