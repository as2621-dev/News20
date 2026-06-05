# Phase 5a — Sub-phase 1 Execution Report

**Migration `0008_feed_allocation` + live apply (0007 + 0008 + entity seed)**

**Status:** SUCCESS · **Validation:** PASS · **Definition of done:** PASS
**Date:** 2026-06-05

---

## 1. What was implemented

- **New migration** `supabase/migrations/0008_feed_allocation.sql` — additive / forward-only:
  - enum `feed_category` with the 8 owner-locked keys (in order): `breaking`, `world_politics`, `tech_science`, `youtube`, `markets`, `sport`, `x`, `culture`.
  - table `user_feed_allocation` (`follow_user_id uuid → auth.users(id) on delete cascade`, `allocation_category feed_category not null`, `allocation_slot_count int not null check (>=0 and <=30)`, `allocation_sort_order int not null`, `allocation_updated_at timestamptz default now()`, PK `(follow_user_id, allocation_category)`).
  - index `idx_user_feed_allocation_user on user_feed_allocation (follow_user_id)`.
  - owner-all RLS `user_feed_allocation_owner_all` — `for all using (follow_user_id = auth.uid()) with check (follow_user_id = auth.uid())`, mirroring `0005`/`0007` exactly.
  - Header doc block in the same documentary style as `0007` (sources, additive/forward-only note, key design decisions surfaced per Rule 7).
- **Live DB apply** via the IPv4 session pooler (`aws-1-us-east-1.pooler.supabase.com:5432`), project ref `cerfennlcgureyifraqy`, using `supabase db push --db-url ...` (DB password read from `.env`, never printed).
- **Entity seed** `supabase/seed/entities.sql` applied idempotently (it already carries `on conflict (entity_id) do nothing` on all 7 insert batches) → 248 rows.

## 2. Files created / modified

- **Created:** `supabase/migrations/0008_feed_allocation.sql`
- **Live DB mutated:** applied migrations `0006`, `0007`, `0008`; seeded `entities` (248 rows). No other source files touched.

## 3. DIVERGENCE FROM PLAN (surfaced per Rule 7 / Rule 12) — IMPORTANT

**The orchestrator stated "0001–0006 should be live, 0007 should NOT be." Reality: only 0001–0005 were live. `0006_story_url_aliases` was genuinely UNAPPLIED** (neither the `story_url_aliases` table nor a `schema_migrations` row for 0006 existed).

`supabase migration list` before apply:
```
   Local | Remote
   0001  | 0001
   0002  | 0002
   0003  | 0003
   0004  | 0004
   0005  | 0005
   0006  |          ← pending (NOT live, contrary to orchestrator's expectation)
   0007  |          ← pending
```

**Decision:** `supabase db push` applies the whole pending set atomically and records each in `schema_migrations`. Applying only 0007+0008 (via hand-rolled psql + manual `schema_migrations` inserts) would have left a **permanent gap** (0007 recorded, 0006 forever pending-but-skipped) — a broken migration history. I verified `0006` is strictly additive/forward-only (a service-role-only `story_url_aliases` table, **no** DROP/destructive ALTER — see scan below), so applying it alongside 0007+0008 is safe and leaves a clean, gap-free history. I let `db push` apply **0006 + 0007 + 0008**.

Destructive-statement scans (pre-apply, blast-radius confirmation):
- `0006`: `CLEAN: 0006 has no destructive statements`
- `0007`: only match is a comment (`-- reversible only by manual drop of the new objects`)
- `0008`: only match is a comment (`-- reversible only by manual drop`); `create` statements = `create type` + `create table` + `create index` + `create policy` (+ non-destructive `enable row level security`).

Post-apply `migration list` (clean, no gap):
```
   0001 | 0001    0005 | 0005
   0002 | 0002    0006 | 0006
   0003 | 0003    0007 | 0007
   0004 | 0004    0008 | 0008
```

## 4. Code-review findings + fixes

Reviewed `git diff` of the new migration. No critical/high/medium issues found; nothing required fixing.
- Additive-only safety: PASS (no DROP, no destructive ALTER; only `create *` + non-destructive `enable row level security`).
- RLS correctness: PASS (predicate on BOTH `USING` and `WITH CHECK`, both `follow_user_id = auth.uid()`).
- Enum keys: PASS (8 keys, verbatim, correct order).
- CHECK bounds: PASS (`>= 0 and <= 30`).
- PK / FK / index: PASS (PK `(follow_user_id, allocation_category)`; FK `auth.users(id) on delete cascade`; index `idx_user_feed_allocation_user`).
- Style match with 0007: PASS. Low-severity note (no fix): `create type`/`create table` are unguarded (no `if not exists`) — this **matches** 0007's prevailing style exactly (0007 only guards the `create extension`); migration-level idempotency is handled by `schema_migrations`, so a guard would diverge from the codebase convention (Rule 11). No change made.

## 5. Validation — EXACT captured live-DB output

### 5a. Entity count (DoD: 248)
```
=== entities count BEFORE seed === 0
=== applying seed (idempotent on conflict do nothing) ===
INSERT 0 8
INSERT 0 38
INSERT 0 16
INSERT 0 121
INSERT 0 51        (+ 2 earlier batches; 7 batches total)
=== entities count AFTER seed (DoD: must be 248) === 248
```

### 5b. Table definition (`\d user_feed_allocation`)
```
                        Table "public.user_feed_allocation"
        Column         |           Type           | Nullable | Default
-----------------------+--------------------------+----------+---------
 follow_user_id        | uuid                     | not null |
 allocation_category   | feed_category            | not null |
 allocation_slot_count | integer                  | not null |
 allocation_sort_order | integer                  | not null |
 allocation_updated_at | timestamp with time zone | not null | now()
Indexes:
    "pk_user_feed_allocation" PRIMARY KEY, btree (follow_user_id, allocation_category)
    "idx_user_feed_allocation_user" btree (follow_user_id)
Check constraints:
    "user_feed_allocation_allocation_slot_count_check" CHECK (allocation_slot_count >= 0 AND allocation_slot_count <= 30)
Foreign-key constraints:
    "user_feed_allocation_follow_user_id_fkey" FOREIGN KEY (follow_user_id) REFERENCES auth.users(id) ON DELETE CASCADE
Policies:
    POLICY "user_feed_allocation_owner_all"
      USING ((follow_user_id = auth.uid()))
      WITH CHECK ((follow_user_id = auth.uid()))
```

`feed_category` enum values (in enumsortorder):
```
breaking | world_politics | tech_science | youtube | markets | sport | x | culture
```

### 5c. RLS allow/deny proof
Method: connected as `postgres` via the session pooler (RLS-bypassing service role). Created two disposable `auth.users` (`aaaaaaaa-…-000a` = user_A, `bbbbbbbb-…-000b` = user_B). Impersonated each user inside a transaction with `set local role authenticated` + `set local request.jwt.claims = '{"sub":"<uid>","role":"authenticated"}'` so `auth.uid()` resolves and RLS applies.

```
=== RLS ALLOW: insert (user_A, 'tech_science', 5, 2) AS user_A ===
INSERT 0 1
 user_A sees own rows: 1

=== RLS DENY (read): SELECT AS user_B — must return ZERO of user_A's rows ===
 user_B sees user_A rows (must be 0): 0

=== CONTROL: service-role (RLS bypass) sees the row ===
aaaaaaaa-0000-0000-0000-00000000000a | tech_science | 5 | 2

=== RLS WITH CHECK DENY: user_B inserts a row owned by user_A — must FAIL ===
ERROR:  new row violates row-level security policy for table "user_feed_allocation"
```
→ ALLOW (own insert + read) works; DENY (cross-user read = 0 rows) works; WITH CHECK blocks cross-user insert. RLS proven both directions.

### 5d. CHECK exercise
```
=== slot_count = 31 must FAIL ===
ERROR:  new row for relation "user_feed_allocation" violates check constraint "user_feed_allocation_allocation_slot_count_check"
=== slot_count = 0 must SUCCEED ===  inserted slot_count=0 OK / INSERT 0 1
=== slot_count = 30 must SUCCEED === inserted slot_count=30 OK / INSERT 0 1
```

### 5e. Cleanup (live DB left with schema + seed only, no junk)
```
DELETE 3   (test allocation rows)
DELETE 2   (disposable auth.users)
user_feed_allocation rows: 0
auth.users rows: 0
entities rows (must stay 248): 248
```

## 6. Definition of done — PASS

| DoD item | Result |
|---|---|
| `select count(*) from entities` == 248 (live) | PASS — 248 |
| `\d user_feed_allocation` shows table + `feed_category` enum column + owner-all policy + index | PASS |
| Authenticated insert of `(user, 'tech_science', 5, 2)` succeeds; a 2nd user's select returns 0 of the first user's rows (RLS allow/deny proven live) | PASS |
| slot_count CHECK exercised (31 fails; 0 and 30 succeed) | PASS |
| Migration additive-only / no DROP (⚠ irreversible safety) | PASS |
| Live DB left clean (test rows + test users removed) | PASS |

No checks were skipped or masked (Rules 9 & 12).

## 7. Concerns for SP2

1. **0006 is now live** (applied as a side-effect of `db push`; see §3). This is consistent with `reference/supabase-schema.md` which documents `story_url_aliases` as migration 0006. No action needed by SP2, but the orchestrator should be aware the live DB advanced 0005→0008 (not 0007→0008).

2. **`entities` columns SP2 hydrates** (verified live, from migration 0007): `entity_id text` (PK, path-derived slug), `entity_slug text`, `entity_label text`, `entity_kind entity_kind` (enum: `company, team, person, league, org, asset, event, brand, franchise, conflict, genre, product`), `entity_ticker text` (nullable; set only for some `company`/`asset` rows), `entity_parent_slug text`, `entity_search_query text`, `entity_is_curated bool`, `entity_created_at`. Note: Nvidia exists as a `company` with `entity_ticker = 'NVDA'` at id `ai/ai-hardware-compute/companies-topics/nvidia` — SP2's `entity_kind == 'company'` ticker-match gate will fire on it.

3. **`user_entity_follows` shape SP2 reads** (live, migration 0007): `follow_user_id uuid → auth.users(id)`, `entity_id text → entities(entity_id)`, `follow_path text[]`, `follow_source entity_follow_source` (enum `seed | more | custom`), `follow_weight numeric default 1.0`, `follow_created_at`, PK `(follow_user_id, entity_id)`. RLS = owner-all on `follow_user_id`. SP2's "custom > seed" bonus rule keys off `follow_source` (or the `follow_weight` it implies) — note `follow_weight` defaults to `1.0` for ALL sources today; nothing in 0007 differentiates custom vs seed by weight, so SP2 must encode the custom>seed weighting itself (in the loader/normalizer), not assume the DB pre-weights it.

4. **`user_feed_allocation` contract SP2/SP3 hydrate** (this migration): owner-all RLS, PK `(follow_user_id, allocation_category)`, `allocation_slot_count` 0..30, `allocation_sort_order` int (manual sequence, NOT unique). The **cross-category `SUM(slot_count) == 30` invariant is NOT enforced by the DB** (a per-row CHECK can't see siblings; an interactive edit needs intermediate states) — the writer (UI/seed) and the allocator's roll-over logic own it. SP3's allocator must not assume the DB guarantees the sum.

5. **Same-day entity duplication (from 0007's design #2):** a label reachable via multiple paths (e.g. Nvidia under AI-hardware vs Business-earnings) is **multiple `entities` rows with distinct `entity_id`s, all sharing label+ticker**. SP2's per-user title/ticker match should dedupe on identity (label+ticker/kind) so one story isn't double-bonused for two followed paths of the same underlying entity.

---

**Return to orchestrator:** STATUS SUCCESS · files: `supabase/migrations/0008_feed_allocation.sql` (new) + live DB (0006/0007/0008 applied, entities seeded) · Validation PASS (248 entities; RLS allow=1 / deny=0 + WITH-CHECK insert denied; CHECK 31 fails, 0 & 30 succeed) · DoD PASS · Key concern: 0006 was NOT previously live and was applied alongside (see §3).
