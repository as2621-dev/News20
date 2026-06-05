# Phase 5 — Sub-phase 2 execution report

**Sub-phase:** Entity registry data layer (list + search)
**Status:** SUCCESS
**DoD:** PASS
**Date:** 2026-06-05

---

## What I implemented

A typed, client-side data layer over the SP1 `entities` registry, plus the
`search_entities` Postgres RPC it depends on.

### `src/lib/entities.ts` (new)
- **`listEntities({ parent, kind?, cursor?, limit=20 }, client?)` → `{ results, nextCursor }`** —
  keyset pagination over `entities`. Filters `entity_parent_slug = parent`
  (+ `entity_kind = kind` when given), orders by `entity_id` ascending, seeks past
  the cursor with `.gt('entity_id', cursor)`. Served by SP1's
  `idx_entities_parent_kind (entity_parent_slug, entity_kind, entity_id)`.
  `nextCursor` = last row's `entity_id` only when a FULL page (`rows.length === limit`)
  returned, else `null`.
- **`searchEntities({ q, kind?, parent?, limit=20 }, client?)` → `EntityResult[]`** —
  calls the `search_entities` RPC via `supabase.rpc('search_entities', { q, k, p, lim })`.
  Trims `q`; an empty/whitespace query short-circuits to `[]` without hitting the RPC.
  A no-match returns `[]` (first-class — caller stores free text per spec §6).
- **Mapping** `entity_id→id`, `entity_label→label`, `entity_ticker→ticker` (OMITTED
  when null/empty), `entity_kind→kind`, in `mapEntityRow`.
- **Types:** exported `EntityResult { id; label; ticker?; kind }`, `EntityKind`
  (12-value union matching the `entity_kind` enum), `EntityPage`, and typed
  `ListEntitiesParams` / `SearchEntitiesParams`.
- **Logging + client:** structured `logger` (info on both read boundaries, error
  with `fix_suggestion`) mirroring `onboardingProfile.ts`; reuses
  `getSupabaseBrowserClient()` with an injectable `client` default for tests. No new
  deps, no hardcoded URLs/keys.

### `supabase/migrations/0007_entity_registry.sql` (appended only)
- Appended `create or replace function search_entities(q text, k entity_kind default
  null, p text default null, lim int default 20) returns table (entity_id text,
  entity_label text, entity_ticker text, entity_kind entity_kind) language sql stable
  security invoker` at the END of the file. Body:
  `where entity_search_query ilike '%' || q || '%' and (k is null or entity_kind = k)
  and (p is null or entity_parent_slug = p) order by word_similarity(q, ...) desc,
  entity_label asc limit lim`, aliasing the table as `e`.
- **Did NOT alter SP1's DDL** — only appended the function + its comment block (SP1's
  style). Verified via grep that SP1's CREATE TABLE / RLS statements are untouched.

### `tests/lib/entities.test.ts` (new)
- 10 tests, Supabase client mocked at the chain boundary (mirrors
  `tests/lib/feed/supabaseFeed.test.ts`). Covers happy/failure/edge + the three DoD
  cases, each with a Rule-9 WHY comment.

---

## Files touched
- `src/lib/entities.ts` (new)
- `supabase/migrations/0007_entity_registry.sql` (appended `search_entities` RPC only)
- `tests/lib/entities.test.ts` (new)

No other files touched.

---

## Decisions / divergences
1. **`searchEntities` uses an RPC, not a PostgREST `.ilike()` chain.** The phase plan
   prescribed the `search_entities` RPC; chosen because trigram `word_similarity`
   ranking can't be expressed cleanly through PostgREST's filter grammar, and SP1's
   "Concerns for SP2" explicitly says the RPC is to be added to 0007. No existing
   `.rpc(` call site existed in `src/lib` (grep confirmed) — so the RPC invocation
   pattern (`client.rpc(name, args).returns<T>()`) is established here for SP3+.
2. **RPC params named `q / k / p / lim`** (single-letter, never `entity_*`), columns
   qualified with alias `e.` — deliberately avoids the Postgres "column reference is
   ambiguous" bug (SP1 flagged this).
3. **`security invoker`** (the SQL default, stated explicitly for clarity) so the
   public-read RLS on `entities` still governs the search — the function does not
   escalate privileges.
4. **`ticker` is omitted, not `null`,** in the result when absent (spec §6 writes it
   `ticker?`), so SP3 can branch on presence rather than a sentinel.
5. **Empty-query short-circuit** in `searchEntities` — a blank Add-your-own input can
   never resolve to an entity, so it returns `[]` without an RPC round-trip. Minor,
   matches the free-text fallback intent.
6. **Registry multi-row reality (carried from SP1):** `listEntities` pages a parent's
   children 1:1 with the prototype; cross-path dedupe of the same real-world entity
   (Nvidia = 3 rows) remains an SP3/SP4 follow-layer concern, NOT this data layer's.

---

## Self-review findings + fixes
- **Column names vs SP1 DDL** — `entity_id/label/ticker/kind/parent_slug/search_query`
  all exact. ✓
- **Keyset overlap** — strictly `.gt('entity_id', cursor)` over stable
  `order('entity_id', ascending)`; append-only seed data ⇒ no overlap, no skip. The
  no-overlap test asserts `page2 ∩ page1 = ∅`. ✓
- **Termination** — `nextCursor = null` on any partial/empty page; asserted on both
  the 8-of-20 partial page and the empty-parent page. No infinite Show-more. ✓
- **RPC injection** — `q` is a BOUND rpc parameter; the `'%' || q || '%'` lives inside
  the SQL body operating on the bound value, not string-interpolated SQL. Not
  injectable. ✓
- **RPC param/column ambiguity** — `q/k/p/lim` can't collide with `entity_*` columns;
  body qualifies columns with `e.`. ✓
- **No-match → `[]`** — real path (`(data ?? []).map`) + empty-query short-circuit;
  both asserted. ✓
- **Client reused, not re-created** — default `getSupabaseBrowserClient()`, injectable.
  No new client. ✓
- No critical/high issues found; nothing left unfixed.

---

## Validation results
- **`npx vitest run tests/lib/entities.test.ts`** → `Test Files 1 passed (1)` /
  `Tests 10 passed (10)`.
- **Full suite `npx vitest run`** → `Test Files 27 passed (27)` /
  `Tests 230 passed (230)` — no regressions from the new module.
- **`npx biome check src/lib/entities.ts tests/lib/entities.test.ts`** →
  `Checked 2 files in 63ms. No fixes applied.` (clean). SQL is out of biome scope, as
  expected.
- Max-2-attempts rule: passed on the first attempt; no fix-and-rerun needed.

---

## Definition of done — PASS
- `listEntities` paginates a seeded parent and returns a working `nextCursor` whose
  next call yields the next page with **no overlap** (asserted disjoint), and a
  partial final page returns `nextCursor=null`. ✓
- `searchEntities('Nvidia')` resolves to the entity carrying **ticker NVDA** (mapping
  surfaces `ticker:'NVDA'`). ✓
- A no-match query returns `[]`. ✓
- All asserted against a **mocked Supabase client** (no real service hit). ✓
No test is skipped or shallow; the no-overlap assertion is a real set-disjointness
check, not a length check.

---

## Concerns for SP3 (the API surface SP3's FollowSet/FollowChip consume)

**Exact signatures + shape (import from `@/lib/entities`):**

```ts
type EntityKind =
  | "company" | "team" | "person" | "league" | "org" | "asset"
  | "event" | "brand" | "franchise" | "conflict" | "genre" | "product";

interface EntityResult { id: string; label: string; ticker?: string; kind: EntityKind }
interface EntityPage   { results: EntityResult[]; nextCursor: string | null }

listEntities(
  params: { parent: string; kind?: EntityKind; cursor?: string; limit?: number },
  client?: SupabaseClient,
): Promise<EntityPage>;

searchEntities(
  params: { q: string; kind?: EntityKind; parent?: string; limit?: number },
  client?: SupabaseClient,
): Promise<EntityResult[]>;
```

**Wiring Show-more (spec §4/§9):**
- A `FollowSet` with a `registry` pointer (`{ parent, kind }` from the seed) calls
  `listEntities({ parent: set.registry.parent, kind: set.registry.kind })` on first
  Show-more, then re-calls with `cursor: page.nextCursor` to append each next page.
- **Stop condition:** when `nextCursor === null`, hide/disable the Show-more control —
  the set is exhausted. Do NOT loop while non-null without appending, or you'll
  re-fetch.
- **Dedupe against seed items:** Show-more rows may include `id`s already shown as seed
  bubbles (the seed is the curated top-N of the same parent). SP3 should dedupe the
  appended page against already-mounted chip `id`s before rendering (set-membership on
  `EntityResult.id`).

**Wiring Add-your-own (spec §6):**
- Debounce the input, call `searchEntities({ q, kind: set.registry.kind, parent:
  set.registry.parent })`; render `results` as suggestions.
- On pick → store the resolved `EntityResult` (`id` is the canonical entity id; SP4
  persists it as a `more`/`custom` follow).
- On `results.length === 0` (or the user submits with no pick) → store the typed value
  as a **free-text** follow (`{ kind: 'freetext' }` per spec §6/§7) — `searchEntities`
  returns `[]` (never throws) precisely so this fallback is clean. An empty/whitespace
  query also returns `[]` (short-circuited, no RPC).

**Cross-path dedupe is SP3/SP4's job, not this layer's** (carried from SP1): the
registry holds multiple rows for one real-world entity reached via different paths
(Nvidia = 3 `entity_id`s, all `ticker NVDA`). `listEntities`/`searchEntities` return
them as-is. SP3's selection store must dedupe on a **canonical identity**
(`label`+`ticker`+`kind`), NOT on `EntityResult.id`, and SP4 records the multiple paths
in `user_entity_follows.follow_path`.

**Note for SP1's live apply:** migration 0007 (now including `search_entities`) is NOT
yet applied to any DB — `searchEntities` will only resolve once the owner runs the
one-time apply + seed. SP3/SP4 mock the client per the CLAUDE.md rule.
