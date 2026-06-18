# Phase SP1 — Sub-phase 3 execution report (AUTHOR-ONLY)

**Status:** SUCCESS (author-only scope — migration file written + statically validated; NOT applied)
**Scope limit honored:** No DB connection was opened. No `supabase db push` / `psql` / remote mutation was run. Live apply is DEFERRED to the orchestrator's human checkpoint.

## File created (only one)
- `supabase/migrations/0017_drop_breaking_allocation.sql`

## What the migration does
1. **Deletes** the orphaned breaking allocation rows:
   ```sql
   delete from user_feed_allocation
   where allocation_category = 'breaking';
   ```
   Their default slot budget (`breaking 4`) is absorbed by SP1's new even-split default (`_default_allocation` evenly splits 30 across topic categories) plus each user's remaining per-category budgets, so feeds still total 30.
2. **Retains** the `feed_category` enum's now-unused `'breaking'` value on purpose (documented in a SQL comment block) — Postgres can't cheaply DROP an enum value; an unused value is harmless, safe, reversible; the full enum swap is deferred / out of scope.
3. **Keeps** `stories.story_is_breaking` (from 0015) — documented in the comment block as the future velocity flag feeding CoverageMomentum → `story_importance` (M4). Explicitly NOT dropped.
4. Touches **only** `user_feed_allocation`. No enum drop, no column drop, no other table.

## Column-name verification (against real migrations, not guessed)
- Table + column: `user_feed_allocation.allocation_category` — defined at
  **`supabase/migrations/0008_feed_allocation.sql:76`** (`allocation_category feed_category not null`). PK at `0008:80`, by-user index at `0008:85`.
- Enum `feed_category` with `'breaking'` as a member: **`0008_feed_allocation.sql:57-66`** (`'breaking'` at `0008:58`) — confirms the literal value to match in the WHERE clause, and confirms the value being intentionally retained.
- `stories.story_is_breaking`: **`0015_story_detail_category.sql:22`** (`alter table stories add column story_is_breaking boolean not null default false;`) — the column kept as the velocity flag.
- Numbering: highest existing migration is `0016_content_sources_user_added_write.sql` (confirmed via `ls supabase/migrations/`), so this file is correctly `0017`.

## Why it's idempotent
A plain `DELETE ... WHERE allocation_category = 'breaking'` is naturally idempotent: the first run removes the breaking rows; every re-run matches zero rows and succeeds without error. No `IF EXISTS` / `DO` guard is needed because the table and column are guaranteed to exist (created in 0008) — only the row set shrinks, never the schema. No accidental broad delete: the WHERE predicate is pinned to the single literal `'breaking'`.

## Static validation
- No local SQL tooling available (`psql`, `sqlfluff` both absent) — and per scope, no DB connection is permitted. Validation is by inspection against the verified schema: the statement is a single plain `DELETE` with a literal predicate; syntactically trivial and correct.
- Confirmed: correct table (`user_feed_allocation`) + correct column (`allocation_category`, verified at 0008:76), idempotent, no broad/unfiltered delete, no enum drop, no `story_is_breaking` drop, no other table touched.

## Definition of done (author-only scope): PASS
- File exists at `supabase/migrations/0017_drop_breaking_allocation.sql` with number 0017. ✅
- DELETE targets the verified column `allocation_category = 'breaking'`. ✅
- Enum-retain + `story_is_breaking`-keep decisions documented as SQL comments. ✅
- Live apply + `select count(*) = 0` verification noted as DEFERRED to the human checkpoint. ✅

## DEFERRED — for the orchestrator's live human checkpoint
Run **after** SP1/SP2 land (so code no longer writes breaking rows). Use the IPv4 session pooler (`news20-supabase-ddl-connection`; pooler `:6543`, not direct host):

**Apply command:**
```bash
supabase db push --db-url "$SUPABASE_SESSION_POOLER_URL"
```
(or apply just this file via the session-pooler `psql` per the team's standard migration runner.)

**Verification query (expected: 0):**
```sql
select count(*) from user_feed_allocation where allocation_category = 'breaking';
```

**Post-apply sanity (column must still exist):**
```sql
select column_name from information_schema.columns
where table_name = 'stories' and column_name = 'story_is_breaking';
-- expected: one row (story_is_breaking kept)
```
