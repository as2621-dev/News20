# Phase 5c — Sub-phase 4a (LOGIC ONLY) execution report

**Scope:** the palette-agnostic LOGIC half of SP4. NO `.tsx`, NO flow wiring
(`onboarding/page.tsx` / `OnboardingFlow.tsx`). The UI screens + flow routing are
DEFERRED to the later UI pass (user supplies the source-screen HTML/CSS).

**Status:** SUCCESS.

---

## 1. What I implemented (the logic half)

Four hand-off helpers the UI pass + the recommendation read depend on:

1. **`getArchetypes()`** (`src/lib/sources.ts`) — public-read client-side Supabase
   read of the `archetypes` table (anon-public via `archetypes_public_read`,
   migration 0009), returning typed `Archetype[]` (`archetype_id/slug/label +
   archetype_vector`) for the PURE `mapToArchetype` (SP1) to score against.

2. **`rollUpInterestVector()`** (NEW `src/lib/interestVector.ts`) — reads the
   user's `user_interest_profile` (joined to `interests` for the slug via a
   PostgREST to-one embed) + `user_entity_follows`, maps each row's slug/entity-
   root to one of the 8 PINNED archetype keys, and SUMS the per-row weights
   (`profile_weight` / `follow_weight`) into one (un-normalized) `InterestVector`
   — the exact shape `mapToArchetype` consumes. Empty/anon → `{}` → fallback.

3. **`upsertUserAddedSource()`** (`src/lib/sources.ts`) — the SP3a search-add gap:
   search results carry only `external_id` but `followSource` needs a catalog
   `source_id`. Upserts a NON-curated (`is_curated=false`) `content_sources` row,
   dedup-keyed on `(content_source_type, external_id)` (idempotent re-add), recovers
   the `source_id` via `.select().single()`, then `followSource`es it.

4. **Pending `x_account` marker** — wired INTO `upsertUserAddedSource`: an
   unresolved `@handle` (`is_pending: true`) persists `platform_metadata.is_pending
   = true` so Phase 5d can find + enrich it. Non-pending rows carry `null` metadata.

5. **Source-onboarding-complete marker** (`src/lib/onboardingProfile.ts`) —
   `markSourceOnboardingComplete()` / `isSourceOnboardingComplete()`, a
   localStorage round-trip the future returning-user-skip reads.

---

## 2. Files created / modified

| File | Change | Tests |
|---|---|---|
| `src/lib/interestVector.ts` | **NEW** — roll-up + the two mapping tables | `tests/lib/interestVector.test.ts` (NEW, 9 tests) |
| `src/lib/sources.ts` | ADD `getArchetypes`, `upsertUserAddedSource` (+ pending marker), types/consts | `tests/lib/sources.test.ts` (+8 tests) |
| `src/lib/onboardingProfile.ts` | ADD `markSourceOnboardingComplete` / `isSourceOnboardingComplete` | `tests/lib/onboardingProfile.test.ts` (+3 tests) |

No `.tsx`, no `onboarding/page.tsx`, no `OnboardingFlow.tsx` touched (Rule 3).
(`agents/worker/main.py` + `plans/phase-5d-*.md` in the working tree are from
OTHER sub-phases, NOT this one.)

---

## 3. Divergences (+ why)

- **Roll-up lives in a NEW `src/lib/interestVector.ts`, not `onboardingProfile.ts`.**
  `onboardingProfile.ts` is the WRITE path (495 lines, near cohesion limit); the
  roll-up is a distinct READ responsibility (two tables → vector → matcher input).
  Splitting matches the codebase's one-file-per-responsibility pattern (e.g.
  `sourceRecommendations.ts` separate from `sources.ts`). Justified.

- **Complete-marker mechanism = localStorage (Rule 7 conflict, surfaced).** The
  existing step-state mechanisms are (a) the `users.user_onboarded_at` Supabase
  column and (b) localStorage (`src/lib/signals.ts`, documented there as "no DB
  table, no migration"). `user_onboarded_at` is ALREADY stamped by the PRIOR
  picker step, and adding a `users.user_sources_onboarded_at` column needs a
  MIGRATION (no migration file is in this SP's scope). The source-step marker is a
  UX skip-gate only — the FOLLOWS themselves persist RLS-scoped via
  `followSource`/`upsertUserAddedSource` — so localStorage (mirroring `signals.ts`:
  SSR-safe, try/catch, best-effort) is the faithful in-scope choice. **Flagged for
  cleanup:** promote to a `users` column when a migration is next added; the
  getter/setter contract stays identical.

- **`was_inserted` is always `false`.** PostgREST upsert does not report insert-vs-
  update without a pre-read; documented honestly (not faked — Rule 12). The caller
  doesn't branch on it (follow idempotency is the contract that matters).

---

## 4. The category-slug → 8-pinned-key mapping (the load-bearing decision)

The picker/screen taxonomy and the 8 PINNED archetype keys are DIFFERENT axes.
`agents/pipeline/categories.py` `SLUG_TO_CATEGORY` maps interest roots into the 8
SCREEN categories (`world_politics|tech_science|markets|sport|culture|youtube|x|
breaking`) — NOT the pinned keys. No interest→pinned mapping was documented
anywhere (reference/archetypes.md only defines the pinned 8 + archetype vectors),
so I defined it. Pinned keys: `ai|geopolitics|business|environment|politics|tech|
sport|arts`.

### Interest ROOT → pinned key (`INTEREST_ROOT_TO_PINNED_KEY` + the `tech.ai*` exception)

| Interest root (seeded) | → Pinned key | Reason |
|---|---|---|
| `world` | `geopolitics` | "World & Politics" carries the `geopolitics` segment accent (interests.sql) |
| `geopolitics` | `geopolitics` | direct (alias) |
| `climate` | `environment` | "Climate & Environment" → the environment key |
| `business` | `business` | direct |
| `markets` | `business` | markets folds into business (no markets pinned key) |
| `crypto` | `business` | "Crypto & Web3" segment `markets` → business |
| `tech` | `tech` | direct |
| `science` | `tech` | "Space & Hard Science" segment `tech` |
| `health` | `tech` | no health key; health/biotech closest to tech/science axis |
| `sport` | `sport` | direct |
| `entertainment` | `arts` | "Entertainment & Culture" → arts |
| `lifestyle` | `arts` | culture long-tail → arts |
| `wildcard` | `arts` | segment catch-all → arts |
| **`tech.ai*`** (exception) | **`ai`** | AI is a pinned key but only a sub-node of the `tech` interest root; routing `tech.ai`/`tech.ai.llms` to `ai` aligns the topic axis with the `ai/...` ENTITY axis so BOTH feed the `ai` dimension |

### Entity ROOT → pinned key (`ENTITY_ROOT_TO_PINNED_KEY`)

Entity ids are path-derived (`ai/.../openai`); the seeded registry's top-level
segments ARE the pinned keys with coverage. Mostly identity, declared explicitly:

| Entity root | → Pinned key |
|---|---|
| `ai` | `ai` |
| `arts` | `arts` |
| `business` | `business` |
| `geopolitics` | `geopolitics` |
| `sport` | `sport` |
| `tech` | `tech` |
| (`environment`, `politics` declared for forward-compat) | identity |

An UNMAPPED root (interest or entity) is DROPPED + logged — never mis-bucketed (a
wrong bucket is a silent miscategorization, Rule 12). The `politics` pinned key has
no seeded interest/entity root today (geopolitics absorbs the world bucket); it
remains reachable via the entity map for forward-compat. Documented gap, not a bug.

---

## 5. Code-review findings + fixes (Step B/C)

| # | Finding | Severity | Resolution |
|---|---|---|---|
| 1 | Roll-up vector must EXACTLY match `mapToArchetype`'s `InterestVector` | high | CONFIRMED — typed as `InterestVector`, asserted via integration test (roll-up → matcher → `ai-frontier-tech`) |
| 2 | All 8 pinned keys reachable / all seeded roots covered | high | Completeness tests assert every seeded interest + entity root maps; unmapped → dropped+logged |
| 3 | Upsert idempotency (no dupe on re-add) | high | `onConflict: content_source_type,external_id` (the unique constraint); asserted |
| 4 | RLS scoping on new writes | high | `upsertUserAddedSource` requires auth first; inner `followSource` is owner-scoped; roll-up reads owner-scoped, anon→{} |
| 5 | Pending marker actually persisted | high | `platform_metadata.is_pending=true` only when pending; asserted (+ null-when-not) |
| 6 | Complete-marker uses an EXISTING mechanism | med | localStorage (signals.ts precedent); conflict surfaced (§3) |
| 7 | `was_inserted` could mislead | low | Documented as always-false (no fake guarantee) |
| 8 | TS strict / no `any` / no secret logged | high | tsc clean; no `any`; logs ids/counts/slugs only |
| 9 | biome import-sort | low | autofixed (`biome check --write`) |

---

## 6. Validation results (exact commands)

| Command | Result |
|---|---|
| `npx tsc --noEmit` | **PASS** (exit 0) |
| `npx biome check src/lib/interestVector.ts src/lib/sources.ts src/lib/onboardingProfile.ts tests/lib/{interestVector,sources,onboardingProfile}.test.ts` | **PASS** (no fixes; import-sort autofixed earlier with `--write`) |
| `npx vitest run tests/lib/interestVector.test.ts tests/lib/sources.test.ts tests/lib/onboardingProfile.test.ts` | **PASS** (44 tests) |
| `npx vitest run` (FULL suite, regression) | **PASS** — 36 files, **339 tests**, 0 fail |

Mocks are at the Supabase client boundary (archetypes read, interest_profile+entity_
follows read, content_sources upsert, follow insert, localStorage stub) per CLAUDE.md.
Test coverage per the DoD's named cases: ai+tech profile → `ai-frontier-tech`
(integration across SP1 + roll-up); empty profile → `balanced-generalist`; re-adding
the same `external_id` doesn't duplicate (conflict-key asserted); a pending `@handle`
gets `is_pending=true`; plus happy/failure/edge per function.

---

## 7. Definition of done — PASS (logic) vs DEFERRED (UI)

**PASS now (the LOGIC-testable parts of SP4's DoD):**
- The roll-up + `getArchetypes` let `mapToArchetype` produce a CORRECT archetype
  for a real-shaped profile — asserted end-to-end (`ai-frontier-tech`; empty →
  `balanced-generalist`).
- Follows PERSIST RLS-scoped: curated via `followSource` (existing), non-curated
  via `upsertUserAddedSource` (new) — asserted against mocked Supabase (owner-
  scoped writes; anon throws).
- The source-onboarding-complete marker can be SET and READ — asserted (round-
  trip + garbage-value edge), so the returning-user-skip works once wired.

**DEFERRED to the UI pass (NOT faked — Rule 12):**
- The 3 recommendation screens render (`SourceRecScreen` — user supplies HTML/CSS).
- The sequenced flow picker → YouTube → X/personalities → podcasts → reel, each
  skippable, and the actual routing (`onboarding/page.tsx` / `OnboardingFlow.tsx`).
- Reading `isSourceOnboardingComplete()` in the route to skip a returning user.

---

## 8. Concerns / hand-offs — the contract the UI pass will wire to

The UI pass (when the source-screen HTML lands) wires picker → 3 screens → reel
against these exact signatures:

```ts
// src/lib/sources.ts
getArchetypes(client?): Promise<Archetype[]>
upsertUserAddedSource(
  input: UserAddedSourceInput,           // { content_source_type, external_id, source_name,
                                         //   source_description?, thumbnail_url?, subscriber_count?, is_pending? }
  priority?: SourcePriority,             // default "everything"
  client?,
): Promise<UserAddedSourceResult>        // { source_id, was_inserted }
followSource(sourceId, priority?, client?): Promise<void>   // existing — for CATALOG recs

// src/lib/interestVector.ts
rollUpInterestVector(client?): Promise<InterestVector>      // → feed to mapToArchetype

// src/lib/archetypeMatch.ts (SP1)        mapToArchetype(vector, archetypes) → ArchetypeMatch
// src/lib/sourceRecommendations.ts (SP1) getRecommendedSources(kind, { archetypes, subNiches?, limit? })

// src/lib/onboardingProfile.ts
markSourceOnboardingComplete(): void
isSourceOnboardingComplete(): boolean
```

**Recommended screen wiring (per axis):**
1. `const archetypes = await getArchetypes()` (once)
2. `const vector = await rollUpInterestVector()`
3. `const { archetype_id } = mapToArchetype(vector, archetypes)` — top-1 (or
   pass top-2 slugs as the `archetypes` array for the round-robin balance)
4. `getRecommendedSources("youtube_channel" | "x_account"/"personality" | "podcast",
   { archetypes: [archetype_id], limit })` → render the grid (`is_already_added` pre-set)
5. Catalog follow toggle → `followSource(source_id)` / `unfollowSource(source_id)`
6. Search-add result → `upsertUserAddedSource(searchResult)` (passes the SP3a
   `WorkerSourceSearchResult` straight through; `is_pending` flows to the marker)
7. On finishing/skipping the LAST source screen → `markSourceOnboardingComplete()`
   then route to the reel
8. Returning-user skip (in `onboarding/page.tsx`): after the existing
   `user_onboarded_at` check, also `if (isSourceOnboardingComplete()) router.replace("/")`

**Open flags for the UI pass:**
- **`politics` pinned key** has no seeded interest/entity root today (geopolitics
  absorbs the world bucket) → no user currently rolls up a `politics` signal, so
  `us-politics-policy` is effectively unreachable until a politics root is seeded.
  Mapping is correct; this is a SEED gap, not a logic bug. Surface to /cmo.
- **Marker is device-local** (localStorage). A user on a new device re-sees the
  (skippable) source screens. Promote to a `users` column when a migration lands.
- **`personality` axis** is a client-side catalog read (no live search), so the
  search-add modal on that screen has no worker endpoint — `getRecommendedSources
  ("personality", …)` only. (Matches SP3a's `SearchableSourceType` exclusion.)
