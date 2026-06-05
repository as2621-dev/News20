# Phase 5b · Sub-phase 4 — Typed source data layer + types

**Status:** SUCCESS · **No commit** (orchestrator commits at phase end).

## What shipped
A News20 TypeScript type module mirroring the `0009_content_sources.sql` columns, plus a client-side
Supabase data layer (under RLS) for browsing the public source catalog and managing a user's follows,
plus a mock-asserted vitest suite.

## Files touched
- `src/types/source.ts` (created) — `ContentSourceType`, `SourcePriority`, `ContentSource`,
  `UserContentSource`, `Personality`, `Archetype`, and the `SOURCE_TYPE_CONFIGS` map (4 axes incl.
  `x_account`).
- `src/lib/sources.ts` (created) — `listSourcesByArchetype`, `getUserSources`, `followSource`,
  `unfollowSource`, `setSourcePriority`.
- `tests/lib/sources.test.ts` (created) — 14 tests, Supabase mocked at the boundary.
- `.agents/execution-reports/phase-5b-source-data-model-catalog-sub-4.md` (this report).

## Key decisions / divergences
1. **Migration number 0009, not 0008.** SP4's brief referenced "0008 columns"; the actual schema landed
   as `0009_content_sources.sql` (0007/0008 were taken by sibling phases). Types match **0009** exactly.
   Error messages / fix_suggestions reference 0009.
2. **User scoping via `auth.uid()`, owner column `user_id` → `auth.users(id)`** (per the 0009 DDL design
   note), NOT a `public.users` join. `requireAuthedUserId()` resolves `auth.getUser()` app-side; reads
   additionally pin `.eq("user_id", authedUserId)` (defense-in-depth mirroring `follows.ts`, redundant
   with the `user_content_sources_owner_all` RLS policy).
3. **Unauth path THROWS, does not no-op.** `follows.ts` degrades a signed-out follow to a silent no-op
   (the reel is usable signed-out). A *source* follow is an explicit authed action with no graceful
   no-op, so `requireAuthedUserId` throws a loud, actionable error (Rule 12) rather than returning null.
   This is a deliberate divergence from `follows.ts`, documented in the module docstring.
4. **`SOURCE_TYPE_CONFIGS` synthesized (donor file not local).** The TL;DW donor
   `src/types/source.ts:100-119` was unavailable locally, so the config was synthesized to the shape the
   reuse-map §5 describes (label, icon, search placeholder, pill/badge), **News20-neutral** (no TL;DW
   amber palette). Shape shipped per axis: `{ label, label_plural, icon_key, search_placeholder,
   pill_label, tile_shape }`. `icon_key` is a stable string handle (e.g. `"youtube"`, `"x"`) the UI
   resolves to a glyph later — no color/SVG embedded. `tile_shape` (`circle` for person/x_account,
   `square` for channel/podcast) is carried because reuse-map §5's source-artwork note ties tile geometry
   to axis. Added the **4th `x_account`** entry as required. ⚠ When the donor file becomes available, the
   labels/placeholders should be diffed against it for copy parity (cosmetic only; no API impact).
5. **Test path = `tests/lib/sources.test.ts`.** The repo's lib tests live in `tests/lib/` (not colocated;
   no `src/**/*.test.ts*` exist). Matches `tests/lib/follows.test.ts` + `tests/lib/entities.test.ts`. This
   path was orchestrator-authorized.
6. **`subscriber_count bigint` → `number | null`.** Matches the donor's mapping and supabase-js's default
   bigint→number coercion. (For counts beyond `Number.MAX_SAFE_INTEGER` this would lose precision, but
   subscriber counts are nowhere near 2^53; not a concern.)
7. **`platform_metadata jsonb` → `Record<string, unknown> | null`** (not `any` — a typed object boundary).
8. **`followSource` uses `upsert(..., { onConflict: "user_id,source_id" })`** for idempotent re-follow
   against the PK, rather than the read-then-insert toggle in `follows.ts` — simpler and matches the
   "writes a row" DoD wording. `added_via` is set to `"data_layer"` (free-text per the DDL).

## Self-review findings + fixes
- **Formatting:** initial files failed `biome check` (multiline-import + object-property wrapping). Fixed
  via `biome check --write`. Re-checked clean.
- **No `any`** in either source file (verified by grep; only match is the word "anyway" in a comment).
- **No empty catch blocks** — there are no `catch` blocks at all; errors flow through the PostgREST
  `{ data, error }` pattern and are thrown explicitly with a `fix_suggestion` (Rule 12).
- **Shared client reused** — both files import `getSupabaseBrowserClient` from `@/lib/supabase/client`; no
  new client is instantiated.
- No critical/high issues found that required a logic change.

## Validation
| Gate | Command | Result |
|---|---|---|
| Lint | `biome check src/types/source.ts src/lib/sources.ts tests/lib/sources.test.ts` | **PASS** — "Checked 3 files. No fixes applied." |
| Test (new) | `vitest run tests/lib/sources.test.ts` | **PASS** — 14/14 |
| Test (full) | `vitest run` | **PASS** — 31 files, 283/283 (no regressions) |
| Build | `npm run build` (next build, full type-check) | **PASS** — compiled + type-checked + static-exported clean |

## Definition of done (SP4)
| DoD clause | Status | Proof |
|---|---|---|
| Types match the 0009 columns | **PASS** | `src/types/source.ts` mirrors every column name + nullability of `content_sources`/`user_content_sources`/`personalities`/`archetypes`; `next build` type-check passes against the data layer. |
| `followSource` writes a `user_content_sources` row scoped to `auth.uid()` with default `priority='everything'` | **PASS** | Test "upserts a row with default priority 'everything' for a fresh follow" asserts the upsert payload `{ user_id: AUTHED_USER_ID, source_id, source_priority: "everything" }` + `onConflict: "user_id,source_id"`. |
| `getUserSources` returns only the caller's rows (RLS) | **PASS** | Test "returns ONLY the caller's rows, pinned to the authed user_id" asserts the read filters `["user_id", AUTHED_USER_ID]` and every returned row's `user_id === AUTHED_USER_ID`. |
| `setSourcePriority` updates the enum | **PASS** | Test "issues the enum UPDATE on the caller's (user_id, source_id) row" asserts `update({ source_priority: "off" })` scoped to user + source. |
| Mock-asserted | **PASS** | All tests mock the Supabase client at the boundary (no real network); error-surfacing covered for every fn (Rule 12). |

## Concerns / follow-ups for later sub-phases / phases
- **`SOURCE_TYPE_CONFIGS` copy parity:** synthesized without the donor file. A later UI sub-phase (5c/5e)
  should diff labels/placeholders against TL;DW `src/types/source.ts:100-119` for copy parity and resolve
  the `icon_key`s to real News20 glyphs. No API/schema impact.
- **`personalities`/`archetypes` are typed but have no data-layer reads yet** — SP4's brief scoped the data
  layer to `content_sources` + `user_content_sources` only. `Personality`/`Archetype` types exist for 5c's
  matcher + personality-spotlight reads; those reads are out of SP4's scope (correctly).
- **`added_via: "data_layer"`** is a placeholder origin tag; 5c/5e should pass the real origin
  (onboarding / manual / youtube import) when they call `followSource` — consider adding an `addedVia`
  param then (deferred; not needed by SP4's DoD).
