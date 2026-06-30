# Execution report — Phase FSR-M1, Sub-phase 1

**Migration 0022 — `source_clusters` + `source_cluster_members` schema**
Branch: `claude/feed-source-revamp-plan-388edf` (not committed — orchestrator commits).

## STATUS: SUCCESS

## What was built
1. **`supabase/migrations/0022_source_clusters.sql`** — additive, forward-only migration (no down). Mirrors 0009's header-comment style + RLS pattern.
   - `source_clusters`: `cluster_id uuid pk default gen_random_uuid()`, `cluster_slug text not null unique`, `cluster_label text not null`, `cluster_category text not null check (... in 8 roots)`, `cluster_sort_order smallint not null default 0`, `is_curated boolean not null default true`, `cluster_created_at timestamptz not null default now()`. Index on `(cluster_category)`.
   - `source_cluster_members`: `cluster_member_id uuid pk`, `cluster_id ... references source_clusters on delete cascade`, `source_id ... references content_sources on delete cascade`, `personality_id ... references personalities on delete cascade`, XOR `check ((source_id is not null) <> (personality_id is not null))`, `member_sort_order smallint not null`, `member_created_at timestamptz` (plain, no default — per contract of record). Two partial-unique indexes (`where source_id is not null` / `where personality_id is not null`) + an index on `(cluster_id, member_sort_order)`.
   - RLS: `enable row level security` + `for select using (true)` public-read on both tables; NO write policy (service-role only).
2. **`supabase/tests/0022_source_clusters_assertions.sql`** — apply-time assertion script mirroring 0007 (`\set ON_ERROR_STOP on`, `do $$ ... raise exception ... $$` blocks, HOW-TO-RUN header). Asserts: tables + category CHECK exist; trial cluster + source member + personality member FK resolution (then rollback); XOR rejects both-null and both-set (each in an expectation sub-block); anon SELECT succeeds. Leaves no residue. **This is the LIVE-E2E residual — NOT run here.**
3. **`tests/supabase/test_migration_0022_source_clusters.py`** + **`tests/supabase/__init__.py`** (empty) — the GATED OFFLINE DoD. Pure stdlib text parse (`pathlib`, `re`) + `agents.pipeline.categories.TOPIC_CATEGORIES` import. 6 tests, each docstring encodes WHY (Rule 9).

## Divergences from spec
None material. `member_created_at` kept as plain `timestamptz` (no default) exactly per the SP1 contract of record. The assertion script's trial inserts use `member_created_at := now()` explicitly since the column has no default — consistent with the plain-column choice.

## Self code-review findings + fixes
- **XOR correctness** (high-priority to verify): `(a is not null) <> (b is not null)` is boolean inequality = exactly-one-of. Confirmed correct, and proven at runtime in the optional bare-PG apply (both-null rejected, both-set rejected).
- **8-root set-equality** (load-bearing): test parses the `check (cluster_category in (...))` literals via regex and asserts `set(literals) == set(TOPIC_CATEGORIES)` — fails on a drop OR an add. Verified.
- **No write-policy leak**: test asserts `for insert/update/delete/for all` all absent and exactly two `for select using (true)`. Pass.
- No critical/high issues found. No fixes required.

## Validation
- `pytest tests/supabase/test_migration_0022_source_clusters.py -q` → **6 passed in 0.12s** (PASS)
- `ruff check tests/supabase/test_migration_0022_source_clusters.py` → **All checks passed!** (PASS)

## Optional strengthening (extra confidence — NOT live e2e)
Applied the migration DDL on an ephemeral bare PG16 (`pg_virtualenv`) with minimal stub `content_sources(source_id uuid pk)` + `personalities(personality_id uuid pk)` parents. Result: all CREATE TABLE/INDEX/POLICY succeeded; runtime checks confirmed the XOR rejects both-null and both-set, and the source partial-unique rejects a duplicate `(cluster_id, source_id)` pair. The `using (true)` policies applied without an `anon` role present. This proves the DDL + FK + XOR + partial-unique are valid, enforcing SQL. (Anon-select still needs the live DB — that is the deferred residual.)

## Definition of done (OFFLINE): PASS
All SP1 OFFLINE DoD points hold: both `create table` present with named columns; `cluster_category` CHECK ↔ `TOPIC_CATEGORIES` set-equality; XOR member CHECK present; both tables public-read + no write policy; 0022 unused before this file (0021 is prior latest).

## Concerns / notes for M6a
- **Resolver callability**: the no-dup resolver (SP2) is pure Python consuming fixture rows — it is **app-callable**, not SQL. There is no DB-side resolver function in 0022. M6a's onboarding read path will need either the Python resolver behind a `CatalogRepo` (planned SP4) or a TS port of the same fixture-tested contract.
- **Partial-unique semantics**: dedup is per-leg — `(cluster_id, source_id)` unique only where `source_id is not null`, same for personality. The XOR guarantees exactly one leg is non-null per row, so a member can't be double-counted; but the SAME underlying followable can appear in two DIFFERENT clusters (allowed in data — deduped at selection by SP2, "first cluster wins").
- **`member_created_at` has no default** — the writer/seed must set it explicitly (the assertion script and seed must supply it).
- **RLS on bare PG**: anon-select assertion in the `.sql` test requires the `anon` role + a populated catalog; cannot run in this sandbox. It is the named LIVE-E2E residual.
