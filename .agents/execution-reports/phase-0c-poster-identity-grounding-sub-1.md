# Execution report — phase-0c-poster-identity-grounding · Sub-phase 1

**Sub-phase:** 1 — Canonical reference-image store (schema + bucket)
**Status:** SUCCESS
**Date:** 2026-06-18

## What was implemented

A canonical, demand-driven reference-image store so the poster pipeline can condition
the image model on a VERIFIED current photo of the person a story names (instead of the
model's stale training prior).

### Files created / modified (the two authorized files only)

1. **`supabase/migrations/0019_entity_reference_images.sql`** (new) —
   - Table `entity_reference_images`: `reference_id` uuid pk `default gen_random_uuid()`,
     `entity_key` text **NOT NULL UNIQUE** (normalized resolved person name, lowercased+trimmed),
     `entity_kind` text NOT NULL default `'person'`, `reference_storage_path` text NOT NULL,
     `reference_public_url` text NOT NULL, `source_page_url` text, `verified_at` timestamptz
     NOT NULL default now(), `valid_as_of` date, `verification_confidence` real,
     `created_at`/`updated_at` timestamptz NOT NULL default now().
   - No extra index: the UNIQUE on `entity_key` already creates the btree that serves the
     only access pattern (point lookup by normalized name). Matches the "don't over-index"
     convention in 0007/0018.
   - RLS: `enable row level security` + a single `for select using (true)` public-read
     policy; **no** INSERT/UPDATE/DELETE policy → service-role-only write (service role
     bypasses RLS). Mirrors `entities` (0007) and the content tables (0002).
   - Storage bucket `entity-reference-images` created in SQL via
     `insert into storage.buckets ... on conflict (id) do update set public = excluded.public`
     (the repo's existing convention for `digest-audio` / `story-posters` in 0002), plus a
     public-read `storage.objects` policy `"public read entity-reference-images"`.

2. **`reference/poster-pipeline.md`** (appended) — new §15 "Canonical reference-image store
   (phase-0c — identity grounding)" documenting the table, the bucket, what `entity_key`
   means, and that the store holds VERIFIED current reference photos used to condition the
   image model (with the SERP-fallback-when-absent behavior).

## Divergences from the plan

- **None material.** The plan left the bucket-creation mechanism open ("via SQL or
  out-of-band — prefer the repo's existing convention"). The repo creates buckets in SQL
  (`insert into storage.buckets` in 0002), so the bucket is created in the migration. No
  out-of-band bucket call was needed.
- Doc section was appended as **§15** (after the existing §14 Sources), keeping Sources as
  the doc's final reference list. The doc's section numbering was already non-strict
  (§13 Named creatives → §14 Sources); §15 continues it.

## Code review findings + fixes

Reviewed `git diff` for SQL correctness, RLS safety, naming, CLAUDE.md adherence.

- **SQL correctness:** `gen_random_uuid()` default confirmed as the established repo
  convention (used in 0001/0005/0009). Column types match the spec exactly. No issues.
- **RLS safety (high-priority check):** verified post-apply that exactly ONE policy exists
  (`entity_reference_images_public_read`, cmd `r`/SELECT) and NO write policy — no accidental
  public WRITE. PASS.
- **Naming:** verbose snake_case throughout (`reference_storage_path`, `verification_confidence`,
  etc.). PASS.
- **No critical/high issues found.** No fixes required.

## Validation (actual output)

Applied via the IPv4 **session pooler on port :6543** (derived from `.env`'s `SUPABASE_DB_URL`,
which points at `aws-1-us-east-1.pooler.supabase.com` but on `:5432`; swapped to `:6543` per
the supabase-ddl memory). `supabase db push` could not be used cleanly because the CLI's
tracking table only knows 0001–0015 (0016–0018 were applied out-of-band and are unrecorded),
so `db push` tried to re-apply 0016 and errored on an already-existing policy. The connection
itself authenticated fine. The 0019 DDL was therefore executed directly over the same session
pooler via `asyncpg` (the only Postgres driver available, in `.venv`).

Verification queries (actual output):

```
APPLY_OK: 0019 executed
DOD_ROWCOUNT: entity_reference_images count = 0
DOD_BUCKET: {'id': 'entity-reference-images', 'name': 'entity-reference-images', 'public': True}
RLS_ENABLED: True
POLICIES: [('entity_reference_images_public_read', 'r')]   # 'r' = SELECT only; no write policy
STORAGE_OBJECT_POLICY: public read entity-reference-images
BUCKETS_LISTABLE: True | present buckets: ['digest-audio', 'story-posters', 'entity-reference-images']
COLUMNS: reference_id uuid NOT NULL; entity_key text NOT NULL; entity_kind text NOT NULL;
         reference_storage_path text NOT NULL; reference_public_url text NOT NULL;
         source_page_url text NULL; verified_at timestamptz NOT NULL; valid_as_of date NULL;
         verification_confidence real NULL; created_at timestamptz NOT NULL; updated_at timestamptz NOT NULL
```

## Definition of done: PASS

- Migration applies on remote (via :6543 session pooler): **PASS** (`APPLY_OK`).
- `select * from entity_reference_images` returns 0 rows without error: **PASS** (count = 0).
- `entity-reference-images` bucket is listable: **PASS** (present in `list_buckets()`,
  `public=true`).

## Concerns for the orchestrator

1. **CLI migration-tracking gap (pre-existing, not introduced here).** The remote
   `supabase_migrations.schema_migrations` table tracks only 0001–0015. Migrations 0016,
   0017, 0018 (and now 0019) are applied to the DB but UNRECORDED in CLI tracking. A future
   `supabase db push` will try to re-apply 0016–0019 and error. I did **not** mutate the
   tracking table (out of scope of this sub-phase's two authorized files, and the 0016–0018
   gap predates this work). Recommend the orchestrator run, when convenient:
   `supabase migration repair --status applied 0016 0017 0018 0019 --db-url "<:6543 session pooler>"`
   to reconcile, so later pushes are clean.

2. **Foreign uncommitted change in the working tree.** `git diff` shows `plans/master-plan.md`
   modified (added Phase 0b / Phase 0c bullet links) — this was already present before this
   sub-phase started (session-start status was reported clean, but the file is dirty now). I
   did NOT touch it and left it for its owner. The orchestrator should decide whether it
   belongs in the phase commit.

3. **Do NOT commit** — per instructions, no `git add`/`git commit` was run. The migration is
   already LIVE on remote (irreversible additive change, authorized by the locked plan), so
   the committed migration file and the remote state are in sync.

4. Storage bucket was created in-SQL (repo convention). No out-of-band service-role bucket
   call was needed.
