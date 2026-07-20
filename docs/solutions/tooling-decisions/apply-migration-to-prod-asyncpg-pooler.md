---
title: Applying a Supabase migration to prod (asyncpg via session pooler + supabase_migrations record)
tags: [supabase, migration, ddl, asyncpg, schema_migrations, prod]
problem_type: tooling
symptoms: A new supabase/migrations/00NN_*.sql needs to be applied to prod and recorded, but `supabase db push` errors on the repo's older unapplied migrations (0016/0019/0022/0024) and there is no apply script
root_cause: n/a (recipe)
date: 2026-07-03
---

Established applying migration 0027 (slice #7). Reuse for any forward-only prod DDL when
`supabase db push` is unusable because it tries to replay earlier deliberately-unapplied
migrations.

**Connection.** The prod DB URL is `SUPABASE_DB_URL` in `.env` — the **IPv4 session pooler**
`aws-1-us-east-1.pooler.supabase.com:5432` (the direct host is IPv6-only; :6543 is the
transaction pooler, which breaks DDL). Connect with `asyncpg` and `statement_cache_size=0`
(the pooler rejects prepared-statement caching):

```python
import asyncpg, os
from dotenv import load_dotenv
load_dotenv("/Users/asheshsrivastava/News20/News20/.env")  # explicit path — cwd-relative load can miss it
conn = await asyncpg.connect(os.environ["SUPABASE_DB_URL"], timeout=20, statement_cache_size=0)
```

**The migrations ledger is NOT `public.schema_migrations`.** It is
`supabase_migrations.schema_migrations` with columns `(version text, statements text[], name text)`.
(There are decoy `schema_migrations` tables in the `auth` and `realtime` schemas — ignore them.)

**Apply + record, idempotently, in one transaction:**

```python
sql = open("supabase/migrations/0027_daily_feeds_section_metadata.sql").read()
async with conn.transaction():
    await conn.execute(sql)  # the file itself must be idempotent (use `add column if not exists`)
    await conn.execute(
        "insert into supabase_migrations.schema_migrations (version, name, statements) "
        "values ($1, $2, $3) on conflict (version) do nothing",
        "0027", "daily_feeds_section_metadata", [sql],
    )
```

`asyncpg`'s `conn.execute(multi_statement_sql)` runs a whole `--`-commented DDL batch in one
call (simple-query protocol) as long as there are no bind params. `on conflict (version) do
nothing` makes re-running a no-op. Then verify against `information_schema.columns`.

**Gotcha:** run the script with `PYTHONPATH=<repo root>` if it imports `agents.*` — a script
executed by absolute path puts its own dir (e.g. the scratchpad) on `sys.path[0]`, not the repo.

**Drift keeps recurring — verify before flag-flips (2026-07-07, slice #31):** a slice can
ship code + tests for a table whose migration never reached prod (`0031_x_cluster_sweeps`
was unapplied drift from #23 even though the LATER `0032` WAS applied — recorded versions
are not contiguous). Before wiring/enabling any feature that reads a recent table, check
`information_schema.tables` on prod first; apply ONLY your slice's expand-only migration
(leave foreign slices' drift — 0030/0033 at the time — for their owners, per the
concurrent-agents convention) and record it.
