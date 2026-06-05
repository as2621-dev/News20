# Phase 5 — Sub-phase 1 execution report

**Sub-phase:** Migration 0007 — entity registry + entity follows schema
**Status:** SUCCESS
**DoD:** PASS (offline) — runtime apply DEFERRED to owner manual ops
**Date:** 2026-06-05

---

## What I implemented

Additive, forward-only migration `0007_entity_registry.sql` adding:

- **`pg_trgm` extension** (`create extension if not exists`) — none of 0001–0006 created it (verified by grep); required before the trigram index.
- **`entity_kind` enum** — 12 values verbatim from spec §2/§5: `company, team, person, league, org, asset, event, brand, franchise, conflict, genre, product`.
- **`entity_follow_source` enum** — `seed, more, custom` (spec §7 intent signal).
- **`entities` table** (public-read registry): `entity_id text PK`, `entity_slug text unique`, `entity_label`, `entity_kind`, `entity_ticker text null`, `entity_parent_slug text null`, `entity_search_query text not null`, `entity_is_curated bool default true`, `entity_created_at`. Two indexes: `idx_entities_parent_kind (entity_parent_slug, entity_kind, entity_id)` for `listEntities` keyset paging, and `idx_entities_search_trgm` GIN `(entity_search_query gin_trgm_ops)` for `searchEntities`.
- **`user_entity_follows` table** (owner-all): `follow_user_id uuid → auth.users(id)`, `entity_id text → entities(entity_id)`, `follow_path text[] default '{}'`, `follow_source entity_follow_source`, `follow_weight numeric default 1.0`, `follow_created_at`, **PK `(follow_user_id, entity_id)`**, plus `idx_user_entity_follows_user`.
- **One ALTER on `interests`**: `add column interest_node_type text not null default 'topic'` (additive, back-compatible — lets the picker render the existing topic taxonomy as `type:'topic'` nodes).
- **RLS**: `entities` public-read (`using (true)`, no write policy → service-role only); `user_entity_follows` owner-all (`for all using (follow_user_id = auth.uid()) with check (follow_user_id = auth.uid())`) — mirrors 0005's `follows_owner_all` exactly.

Seed `supabase/seed/entities.sql`: **248 entity nodes** lifted programmatically from `interest_picker.html`'s `DATA` const (not re-authored), idempotent `on conflict (entity_id) do nothing`, grouped by category.

### Seeded entities by kind (248 total)
| kind | count | | kind | count |
|---|---|---|---|---|
| person | 74 | | league | 10 |
| team | 71 | | conflict | 4 |
| company | 61 | | asset | 3 |
| org | 12 | | event | 2 |
| genre | 7 | | brand | 2 |
| product | 1 | | franchise | 1 |

By category: AI 14, Geopolitics 8, Business 38, Tech 16, Sport 121, Arts 51. **Environment** and **Politics** seed **zero** entities — every node there is a pure *topic* (no `kind` in `DATA`: "Solar", "Immigration", "Supreme Court"), so they live in the `interests` taxonomy, not the entity registry. This is the §2 topic-vs-entity split working correctly, not a gap.

---

## Files created / modified

- **Created** `supabase/migrations/0007_entity_registry.sql`
- **Created** `supabase/seed/entities.sql`
- **Created** `supabase/tests/0007_entity_registry_assertions.sql` (runnable DoD assertion script — permitted by the task; repo has no DB test harness)

No other files touched.

---

## Decisions made (and why)

1. **`entity_id` is `text`, not `uuid`** — diverges from `interests` (0003: uuid PK + `text slug`). Justified by `reference/supabase-schema.md` §0, which explicitly sanctions "short text PKs where the prototype already uses a stable human-readable slug." The spec §5/§7 keys nodes/follows by exactly this path-derived id, so a text PK lets the §7 `followId` *be* the `entity_id` and makes the seed deterministic + idempotent with no UUID juggling. `entity_id == entity_slug == prototype chip id`.

2. **`entity_id` is PATH-derived, not identity-derived** (faithful to the prototype's `idBase + '/' + slug(label)` chip id). Consequence: a label reachable via multiple paths (Nvidia under AI-hardware, Business-earnings, Tech-semis) is **3 rows** with 3 ids, all carrying ticker NVDA. **This is the key conflict (Rule 7)** vs. spec §11/SP3's "dedupe to one underlying id with both paths." I resolved it by keeping the registry 1:1 with the prototype (so SP2 `listEntities({parent})` can page a parent's children cleanly) and treating dedupe as a **follow/payload-layer** concern that SP3/SP4 own (the picker dedupes selections on a canonical identity — label+ticker/kind — before persisting; the recorded `follow_path text[]` is where multiple paths land). SP1's actual DoD does not require one-Nvidia-row. **Surfaced here, not blended.**

3. **`follow_user_id → auth.users(id)`**, not `public.users(user_id)` — mirrors the *most recent* shipped user-scoped table (0005 `follows`), per Rule 7 (newer/tested pattern). Both resolve to the same uuid.

4. **Search index = trigram GIN** over `entity_search_query` (`= label [+ ' ' + ticker]`), not tsvector. Chosen because SP2's `searchEntities` is substring/fuzzy ("Nvidia", partial typed input in Add-your-own) — trigram serves both `ILIKE '%q%'` and `similarity()`/`word_similarity()` ranking, which fits entity-name search better than full-text stemming.

---

## Divergences from the plan

- **`entity_id` uuid vs text** — the plan said "CONFIRM against how `interests` keys its rows in 0003 and mirror that decision." `interests` uses uuid; I chose text and documented why (decision #1 above). The plan itself flagged text as "appropriate" given the path-derived id, and schema §0 sanctions it — so this is a confirmed, documented choice, not an unflagged deviation.
- **`idx_user_entity_follows_user` is technically redundant** with the PK's leading `follow_user_id` prefix. Kept anyway to mirror 0005's `idx_follows_user` (which is redundant for the same reason against `uq_follow_user_story`). Convention-conformance (Rule 11) over micro-optimization. Low severity.

---

## Self-review findings + fixes

- **CRITICAL — RLS owner-all predicate**: verified `using (...) with check (...)` both pin `follow_user_id = auth.uid()`. Correct on both read and write sides. No leak. ✓
- **HIGH — FK type resolution**: statically verified all FKs against 0001–0006. `auth.users(id)` (uuid, used identically by 0005); `entities(entity_id)` (text == FK text). All resolve. ✓
- **Investigated `entity_parent_slug` semantics** (initially suspected a bug): confirmed uniform across all 248 rows — `entity_id = entity_parent_slug + '/' + <set-label-slug> + '/' + <entity-label-slug>`. `entity_parent_slug` = the expandable parent node (the `registry.parent` scope in spec §5/§6), NOT `entity_id` minus one segment. This is spec-correct and is the clean contract for SP2's `listEntities({parent})`. No bug. ✓
- **MEDIUM — SQL escaping**: one label needs escaping (`A'ja Wilson → A''ja Wilson`); verified present. Tickers with dots (`BRK.B`, `ENR.DE`) are plain text. ✓
- **LOW — `entity_search_query` redundancy** (e.g. `'AMD AMD'` when label == ticker). Harmless for search; left as-is (Rule 3, no gold-plating).

---

## Validation results

**Environment has NO database** (no psql, no `config.toml`; `docker`/`supabase` present but production must not be touched and this migration is ⚠ irreversible). All validation is **static + offline**.

1. **Static FK/syntax validation** — PASS. Every FK resolves to an existing PK of matching type; `entity_kind`/`entity_follow_source` enum syntax valid; `pg_trgm` created before `gin_trgm_ops` index; `text[]` array default, composite PK, and `alter table ... add column` all valid Postgres. The `interests` table exists in 0003 (the ALTER target).
2. **Seed integrity** — PASS. Programmatic check: 248 data rows, all parse, every `entity_kind` token is a valid enum value, INSERT column list (7) matches every tuple (7 values), all 248 `entity_id`s unique, escaping correct.
3. **Assertion script** `supabase/tests/0007_entity_registry_assertions.sql` — authored, encodes all three DoD checks, fails loud via `raise exception`:
   - **DoD-1**: trial insert into `user_entity_follows` (borrows a real `auth.users` row + the seeded NVDA entity) proves FKs resolve; rolls back.
   - **DoD-2**: `ILIKE '%nvidia%'` over `entity_search_query` returns ≥1 row and the matched company carries `entity_ticker = 'NVDA'`; also exercises `word_similarity()` (the trigram path).
   - **DoD-3**: switches to `anon` role via `request.jwt.claims`; asserts anon SELECT on `entities` succeeds (public-read) AND anon-as-other-user sees **zero** of another user's `user_entity_follows` rows (owner-all isolation).
4. **`npm run lint`** (biome) — PASS (93 files, no errors). SQL is out of biome scope, as expected; confirms no tracked JS/TS broke.

---

## Definition of done

**PASS (offline).** Static validation is clean and the assertion script faithfully encodes all three DoD checks (migration-applies/FK-resolve, Nvidia/NVDA search, anon allow/deny).

**Runtime application is DEFERRED to the owner** — manual `supabase db push --db-url` via the IPv4 session pooler (per MEMORY: aws-1-us-east-1 session pooler; direct host is IPv6-only), then run `supabase/seed/entities.sql`, then `supabase/tests/0007_entity_registry_assertions.sql` with `ON_ERROR_STOP=1`. I did **not** observe a runtime apply and do **not** claim runtime PASS (Rule 12). ⚠ Forward-only / no down migration.

---

## Concerns for the orchestrator (esp. for SP2 — the data layer)

**Exact schema names/types SP2 must consume:**

- Table `entities`: `entity_id text PK`, `entity_slug text`, `entity_label text`, `entity_kind entity_kind` (enum), `entity_ticker text null`, `entity_parent_slug text null`, `entity_search_query text`, `entity_is_curated bool`, `entity_created_at timestamptz`.
- Table `user_entity_follows`: PK `(follow_user_id uuid, entity_id text)`, `follow_path text[]`, `follow_source entity_follow_source` (`seed|more|custom`), `follow_weight numeric`.

**`listEntities({parent, kind})` contract** — filter `where entity_parent_slug = :parent [and entity_kind = :kind]`, order by `entity_id` for keyset paging, served by `idx_entities_parent_kind (entity_parent_slug, entity_kind, entity_id)`. The parent scope is the **expandable parent node id** (e.g. `business/corporate-news/what-to-track/earnings`), and `entity_id = parent + '/' + <set-label-slug> + '/' + <entity-label-slug>` (uniform across all 248 rows). `nextCursor` = the last row's `entity_id`.

**`searchEntities({q})` contract** — query `entity_search_query` via `ILIKE '%' || q || '%'` (or `word_similarity(q, entity_search_query)`), served by the GIN trigram index `idx_entities_search_trgm`. The haystack is `label` (+ ` ticker` for companies). The SP2 `search_entities` RPC is to be **added to this same `0007` file** (per the phase plan SP2 file list) — it is NOT in 0007 yet; SP2 appends it.

**Slug scheme (load-bearing for SP3)** — `entity_id == entity_slug ==` the prototype's path-derived chip id (`idBase + '/' + slug(label)`, `slug = lowercase, [^a-z0-9]+ → '-', trim '-'`). This is identical to what `interest_picker.html` computes at runtime, so SP3's engine ids will match the registry ids exactly.

**Cross-path dedupe (SP3/SP4 must own this)** — the registry deliberately holds **multiple rows for the same real-world entity reached via different paths** (Nvidia = 3 rows). SP3's "dedupe to one underlying id" and SP4's persistence must dedupe on a **canonical identity** (e.g. `entity_label` + `entity_ticker` + `entity_kind`), NOT on `entity_id`, and record the multiple paths in `follow_path`. If the orchestrator prefers a single-row-per-entity registry instead, that is a different SP1 design (identity-derived id) and would change the `listEntities` paging model — flag if SP3/SP4 find the multi-row model awkward.

- **Migration 0007 is NOT yet applied** to any DB. SP2/SP3/SP4 mock the Supabase client (per CLAUDE.md mocking rule); the live apply is a one-time owner op before TestFlight.
