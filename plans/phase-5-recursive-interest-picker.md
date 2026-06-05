# Phase 5: Recursive interest picker (topics axis)

**Milestone:** M5 — Two-axis personalization (sources + control surface)
**Status:** Not started
**Estimated effort:** L

## Goal
A signed-in user works a **recursive follow-set picker** across the 8 categories — tapping bubbles that unfold nested child sets to arbitrary depth (Earnings→Companies, NFL→Teams+People, a genre→Artists), each set with **Select all / Show more / Add your own** — and lands with persisted **topic follows + entity follows** (RLS-scoped) that the ranker consumes. Replaces M1's `InterestChips` onboarding with the richer engine from `interest_picker.html`.

## Why this phase exists
Master plan Decision #11 (added 2026-06-04) makes topics a first axis backed by a recursive engine, not a fixed 3-level chip tree. The owner chose to keep the picker in M5 (not pull it into M1). It supersedes `phase-1e`'s `InterestChips` but **keeps phase-1e's auth, `users`/`user_interest_profile` schema, and RLS** — this phase swaps the onboarding UI and adds an entity registry, nothing more. Source of truth: `onboarding_interest_picker_spec.md` (the §-numbered contract) + `interest_picker.html` (the approved prototype + full seed dataset). Output contract feeds `reference/ranking-spec.md`.

## Context the sub-agents need
- **Static-export reality (carry from `phase-1e`):** the Capacitor app has **no Next.js server runtime on device**. The spec §6 describes REST endpoints (`/api/entities/...`); on News20 these are implemented as **client-side Supabase reads** (RPC + `ilike`/full-text) under RLS, not Next API routes. *(Conflict flagged Rule 7: spec says REST; News20 ships client-side Supabase, matching the most recent shipped pattern in `phase-1e`.)*
- **Naming:** `sources`/`story_sources`/`outlets` already exist (news outlets + per-story article sources). Entity follows use **new** tables — do **not** reuse those names.
- **Existing onboarding** lives in `src/components/onboarding/` (`InterestChips.tsx`, `CustomInterestChip.tsx`, `OnboardingFlow.tsx`, `OnboardingSplash.tsx`) + `src/lib/onboardingProfile.ts` + `src/lib/interests.ts` + `src/app/(onboarding)/onboarding/page.tsx`. Auth (magic-link) + `user_interest_profile` writes already work client-side under RLS.
- **Topics vs Entities (spec §2):** topics are stable/finite (ship inline); entities are dynamic/unbounded (live in a registry, seeded for first paint). Tag each node `type` + (entities) `kind`.
- **Latest migration is `0006`** → new migration is `0007`.
- **Lift the seed dataset** from `interest_picker.html` (the big `DATA` const) rather than re-authoring.

## Sub-phases

### Sub-phase 1: Migration 0007 — entity registry + entity follows schema
- **Files touched:** `supabase/migrations/0007_entity_registry.sql`, `supabase/seed/entities.sql`.
- **What ships:** an additive forward-only migration adding — `entity_kind` enum (`company|team|person|league|org|asset|event|brand|franchise|conflict|genre|product`); `entities` table (`entity_id`, `entity_slug` path-derived unique, `entity_label`, `entity_kind`, `entity_ticker text null`, `entity_parent_slug text null`, `entity_search_query text`, `entity_is_curated bool default true`, plus a `tsvector`/trigram index for search); `user_entity_follows` (`user_id`→`users`, `entity_id`→`entities`, `follow_path text[]`, `follow_source` enum `seed|more|custom`, `follow_weight numeric`, PK `(user_id, entity_id)`); one ALTER on `interests` adding `interest_node_type text default 'topic'` (so the picker can render topic nodes from the existing tree). RLS: public-read `entities`; owner-all `user_entity_follows`. Plus `entities.sql` seeding the entity nodes lifted from `interest_picker.html` (companies w/ tickers, teams, people, etc.).
- **Definition of done:** migration applies cleanly on a DB holding 0001–0006 (all FKs resolve); a trigram/full-text search over seeded `entities` returns "Nvidia" with `entity_ticker='NVDA'`; an **anon** `SELECT` on `entities` succeeds while an anon `SELECT` on another user's `user_entity_follows` returns **zero rows** (RLS allow/deny assertion). ⚠ irreversible (forward-only, additive, no drops).
- **Dependencies:** none (extends existing migrations).

### Sub-phase 2: Entity registry data layer (list + search)
- **Files touched:** `src/lib/entities.ts`, `supabase/migrations/0007_entity_registry.sql` (add the `search_entities` SQL RPC here).
- **What ships:** typed client-side reads matching the spec §6 result shape — `listEntities({ parent, kind, cursor, limit=20 })` (keyset pagination over `entities` by `entity_parent_slug`+`entity_kind`, returns `{ results, nextCursor }`) and `searchEntities({ q, kind?, parent?, limit=20 })` (a Postgres `search_entities` RPC over the trigram/tsvector index). Both run client-side against Supabase under RLS.
- **Definition of done:** `listEntities` paginates seeded entities for a `parent` and returns a working `nextCursor` that fetches the next page with no overlap; `searchEntities('Nvidia')` resolves to the entity with its ticker; a no-match query returns `[]` (so the caller can store free-text). Asserted against a mocked Supabase client (CLAUDE.md mocking rule).
- **Dependencies:** Sub-phase 1.

### Sub-phase 3: Recursive follow-set engine (FollowSet + FollowChip)
- **Files touched:** `src/components/onboarding/FollowSet.tsx`, `src/components/onboarding/FollowChip.tsx`, `src/lib/followSets.ts`, `src/types/picker.ts`.
- **What ships:** the recursive unit per spec §4/§9 — `FollowSet` (eyebrow label + **Select all** + **Show more** [paginates via SP2 `listEntities`] + chip grid + **Add your own** [searches via SP2 `searchEntities`, else stores free-text]); `FollowChip` (tap-to-toggle, renders `ticker` in the rust accent, **lazy-mounts** nested child sets on select, collapses on deselect **but preserves child selections** in the store); the `Node`/`FollowSet` types (§5); cross-path **dedupe** (one entity id, multiple `path`s). Selection store keyed by `followId`.
- **Definition of done:** rendering the lifted seed dataset, all four §4 marquee nested cases work — Earnings→*Companies to track* (with tickers, Select all, Show more); Oil&gas→three sets (Majors / Midstream / Equipment); NFL→*Teams* (Show more) **and** *People*, College football independently; a music genre→its Artists with multi-genre multi-select. Selecting a chip lazily mounts its nested sets; deselecting collapses but the store still holds the child selections; an entity reachable via two paths (Nvidia under AI-hardware and Business-earnings) dedupes to one underlying id with both `path`s. RTL + mocked-data tests encode each marquee case (Rule 9).
- **Dependencies:** Sub-phases 1, 2.

### Sub-phase 4: Picker page + selection tray + follows persistence → ranking
- **Files touched:** `src/components/onboarding/OnboardingPicker.tsx`, `src/components/onboarding/SelectionTray.tsx`, `src/lib/onboardingProfile.ts` (extend), `src/app/(onboarding)/onboarding/page.tsx`, `src/components/onboarding/OnboardingFlow.tsx`.
- **What ships:** the page container owning the selection store + the persistent `SelectionTray` (total count, per-category counts, Review panel grouped by category, Export); on completion, persist **topic** follows to `user_interest_profile` and **entity** follows to `user_entity_follows` (with `follow_source` weighting — `custom` > `more`/`seed`, the §7 intent signal) scoped to `auth.uid()`; swap `InterestChips`→`OnboardingPicker` in the flow (splash → email sign-in → **picker** → loading → reel); **skippable** (zero follows → empty profile → breaking-news-only feed, no error); ≥44px targets, chips are real buttons with `aria-pressed`.
- **Definition of done:** completing the picker writes ≥1 `user_interest_profile` + ≥1 `user_entity_follows` row scoped to `auth.uid()`, with a `custom` follow carrying higher `follow_weight` than a `seed` follow; **skipping** writes nothing and routes to the reel with no error state; the old `InterestChips` path is removed from the flow; `reference/ranking-spec.md` consumers (feed_assembly) read both follow sources. Mock-asserted + RLS allow/deny on `user_entity_follows`.
- **Dependencies:** Sub-phases 1–3.

## Phase-level definition of done
A signed-in user completes the recursive picker across all 8 categories — exercising the four §4 nested cases, Show-more pagination, and Add-your-own (resolved + free-text) — and lands with persisted topic + entity follows RLS-scoped to `auth.uid()` (custom-weighted higher), which the ranker consumes; the entity registry (migration 0007 + `listEntities`/`searchEntities`) backs Show-more and Add-your-own; skipping yields an empty profile and a breaking-news-only feed. **Validated by:** migration-applies + entity-search assertion; RLS allow/deny; the four marquee nested-case tests; the dedupe test; the persistence test (custom > seed weight, no orphan free-text).

## Out of scope
- The **sources axis** (Phases 5b–5e) — this is topics/entities only.
- A **live** entity registry (CMS/registry refresh, spec §6) — v1 ships a curated seed; the registry is a static seeded table, paginated/searched, not a live feed.
- Changing auth or the `users`/`user_interest_profile` schema (owned by `phase-1e`).
- Re-skinning beyond the picker's own design system (tokens are in `onboarding_interest_picker_spec.md` §8).

## Open questions
1. **Topic-follow storage:** persist topic nodes to the existing `user_interest_profile` (recommended — the ranker already reads it) vs a unified `user_entity_follows`. Recommend keeping topics in `user_interest_profile`, entities in `user_entity_follows`.
2. **Free-text canonicalization:** an Add-your-own miss stored as `{kind:'freetext'}` follow vs matched to nearest entity. Recommend store-as-freetext for v1 (spec §6), revisit if customs proliferate.
3. **Static-export vs REST (Rule 7):** confirm client-side Supabase + RPC for the registry (recommended) rather than standing up a Vercel/worker REST surface for §6.

## Self-critique
**Product lens:** PASS — delivers Decision #11's topics axis exactly per the owner-approved prototype; the four marquee cases the product owner explicitly cares about (§4) are first-class DoDs. Skippable preserves the "picker is high-value, not a hard gate" principle. No source-axis creep.
**Engineering lens:** PASS — corrects the spec's REST framing to News20's shipped static-export + client-side-Supabase reality (Rule 7 flagged, not blended). Reuses phase-1e's auth/profile/RLS rather than reinventing. DoDs are mock-verifiable in fresh context. SP4 (persistence) lands last, after the engine is proven, so it doesn't cement the follow schema before the UI shape is known.
**Risk lens:** PASS with flags. ⚠ SP1 is a forward-only additive migration (flag a disposable DB first). Within-phase file overlap: `onboardingProfile.ts` and `0007_entity_registry.sql` are each touched by two sub-phases but **sequentially** (SP1→SP2 add RPC to the same migration file before it's applied; SP3→SP4 extend `onboardingProfile.ts`) — no parallel-edit conflict; dependencies marked. RLS allow/deny on `user_entity_follows` is a first-class DoD (a leaked follow set is the worst failure). Painting-into-a-corner check: schema → data layer → engine → persist leaves follows the ranker reads directly.
**Irreversible sub-phases:** SP1 (forward-only migration + seed).
