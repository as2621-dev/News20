# Phase FSR-M6a: Source/cluster onboarding UI (filter ∩ category, opt-out clusters, no-dup)

**Milestone:** M6 — Source/cluster onboarding UI + priority feed mix (feed-source revamp, `plans/prd.md`)
**Status:** Shipped (2026-06-30) — see `plans/phase-fsr-m6a-source-cluster-onboarding-ui-progress.md`
**Estimated effort:** L

## Goal
After categories, the user sees YouTube channels / X accounts / Personalities **filtered by `topic_tags ∩ chosen categories`**, ordered by `popularity_score`, with **recommended clusters pre-selected** that they **deselect (opt-out)**; selecting a cluster follows all its members; the rendered grid honors the **no-dup rule** (a personality's own handles hidden); the resulting follow set persists to `user_content_sources` / `user_personalities`.

## Why this phase exists
This is the user-facing half of M6 (PRD §"Onboarding selection" module contract, User Stories 8–15). Phase 5c already shipped the source-onboarding *plumbing* (catalog reads, recommendation merge, `SourceArtwork`/`SourceCard`, follow writes, the onboarding flow). M6a does NOT recreate those — it adds the **category-keyed filter** (Phase 5c keyed on `personas`/archetypes; M6 keys on `topic_tags ∩ chosen categories`), **cluster bulk-select**, **opt-out pre-selection**, the **no-dup grid**, and the **`user_personalities` write path** that `src/lib/sources.ts` lacks today.

## Cross-milestone dependency (assumed — surface if unmet)
- **M1 (catalog clusters + no-dup)** — owns the net-new `source_clusters` / `source_cluster_members` schema, the seed, and the cluster/no-dup **resolver** (per `reference/source-catalog-taxonomy.md` §clusters). M6a **consumes** that resolver's output: for a category, its clusters with ordered members, with a personality's own `content_sources` rows excluded when the personality card is present. **If M1 has not landed a TS-callable cluster read by the time M6a runs, SP1 builds the thin `src/lib` read over M1's tables and STOPS if the tables/seed are absent (Rule 12 — do not stub a fake catalog).**
- **M5 (top-level-category onboarding)** — supplies the user's chosen root categories (`user_interest_profile` roots) that M6a filters by. If M5's roots aren't available at run time, SP4 reads the existing chip profile (`phase-1e`) rolled to roots, as Phase 5c did.

## Reuse (do NOT recreate — `reference/sources-reuse-map.md`, `reference/control-surface-spec.md`)
- `src/lib/sources.ts` — `listSourcesByArchetype` pattern, `followSource`/`unfollowSource`, `getUserSources`, the RLS/anon-degrade posture. **Extend, don't fork.**
- `src/lib/sourceRecommendations.ts` — popularity ordering, `is_already_added` annotation. The **category filter replaces the `personas` overlap**; keep the popularity sort + follow annotation.
- `src/components/sources/SourceArtwork.tsx`, `SourceCard.tsx` — avatar + selectable card (re-skinned, `aria-pressed`). Reuse as-is for cluster member tiles.
- `src/components/blip/library/SourcesScreen.tsx` / `SourcesAddControls.tsx` — the `AXIS_DISPLAY` map, glyphs, `FollowedSourceWithPriority` shape.
- Onboarding wiring: `src/components/onboarding/OnboardingFlow.tsx`, `src/app/(onboarding)/onboarding/page.tsx`.

## Sub-phases

### Sub-phase 1: Category-keyed catalog + cluster read (`src/lib`)
- **Files touched:** `src/lib/sourceClusters.ts` (new), `src/lib/sources.ts` (add `listSourcesByCategory(categories, kind, limit)` — `topic_tags && chosen` overlap, `popularity_score desc`).
- **What ships:** a pure-over-injected-client read layer: (a) `listSourcesByCategory` — catalog rows whose `topic_tags ∩ chosen categories` is non-empty, popularity-ordered, per axis; (b) `getClustersForCategories(categories)` — consumes M1's resolver/tables to return, per chosen category, its clusters with **ordered members**, **no-dup applied** (personality's individual YouTube/X rows excluded when the personality card is present), empty clusters omitted. Both annotate `is_already_added` against the user's follows (degrade to false when anon, per Phase 5c posture).
- **Definition of done:** mock-asserted against fixture catalog/cluster rows: a chosen-category set returns only sources whose `topic_tags` overlap it (a non-overlapping source is excluded — the "no randoms" rule); results are `popularity_score`-descending; a personality present hides its bundled `youtube_channel_ids` rows from both the grid AND its cluster's members; an empty cluster is not returned; a source tagged to two chosen categories appears once per category (deduped within a category); a member in two clusters of one category resolves without duplication. **The no-dup test must fail if the personality's handles leak back into the grid** (Rule 9 — encodes the load-bearing rule).
- **Dependencies:** M1 (cluster schema + resolver + seed). INDEPENDENT of SP2/SP3 within this phase.

### Sub-phase 2: Opt-out cluster-selection state model (pure)
- **Files touched:** `src/lib/clusterSelection.ts` (new).
- **What ships:** a pure selection reducer/model over (clusters, members, pre-selected cluster ids, individual toggles): `buildInitialSelection(clusters, recommendedClusterIds)` pre-selects recommended clusters; `toggleCluster(state, clusterId)` selects/deselects all its members; `toggleMember(state, memberRef)` flips one; `resolveFollowSet(state)` → the deduped `{ sources: source_id[], personalities: personality_id[] }` to persist. Encodes opt-out semantics + edge rules.
- **Definition of done:** unit-tested: pre-selected clusters yield their members as the initial follow set (opt-out, not opt-in — User Story 12); **deselecting a cluster after individually keeping one member leaves that member followed** (PRD edge case — must fail if deselect nukes the kept member); deselecting a pre-selected cluster removes exactly its non-individually-kept members; picking **zero clusters** yields an empty follow set (allowed — User Story 21, feed still works via news); a member shared by two selected clusters appears once in `resolveFollowSet`; personalities and content-sources are partitioned correctly into the two output lists.
- **Dependencies:** SP1 (consumes its cluster/member shapes). INDEPENDENT of SP3.

### Sub-phase 3: No-dup grid + cluster cards UI (render against fixtures)
- **Files touched:** `src/components/sources/SourceClusterGrid.tsx` (new), `src/components/sources/ClusterCard.tsx` (new). Reuse `SourceArtwork`/`SourceCard`.
- **What ships:** the per-axis selection surface: cluster cards (name + member-avatar stack + one-tap select/deselect with `aria-pressed`, pre-selected rendered as selected) above the popularity-ordered individual grid; a personality renders **once** as a personality card (its handle rows absent); selecting a cluster visibly flips all its members; deselect flips them back. Re-skinned to News20 tokens (no TL;DW palette).
- **Definition of done:** React Testing Library tests against SP1/SP2 fixtures: recommended clusters render **pre-selected**; tapping a selected cluster deselects it and its member tiles reflect deselection; the grid never renders a personality's bundled handle as a separate tile (no-dup, visible); the zero-category-catalog cell renders a graceful fallback (never randoms — PRD edge case); `aria-pressed` reflects selection for cluster + member controls. Pure-visual polish may be manual-smoke; selection/no-dup behavior is test-covered.
- **Dependencies:** SP1, SP2.

### Sub-phase 4: Persist follow set + onboarding flow wiring
- **Files touched:** `src/lib/sources.ts` (add `followPersonality`/`unfollowPersonality` writing `user_personalities`, mirroring `followSource`), `src/lib/onboardingProfile.ts` (mark source step complete), `src/components/onboarding/OnboardingFlow.tsx`, `src/app/(onboarding)/onboarding/page.tsx`.
- **What ships:** on continue, `resolveFollowSet` (SP2) is committed — content-source members → `user_content_sources` (existing `followSource`), personality members → `user_personalities` (new `followPersonality`); the source/cluster screen is sequenced **after** the category picker; flow advances picker → source/cluster screen → reel; returning users skip it.
- **Definition of done:** mock-asserted (Supabase mocked at the client boundary): committing a selection writes exactly the resolved source ids to `user_content_sources` and personality ids to `user_personalities` (RLS-scoped, idempotent re-commit is a no-op); deselecting then committing does NOT write the deselected members; an empty selection commits zero follows without error and the flow still advances; the step-complete flag is set and a returning user bypasses the screen. **The write test must assert the follow set equals the resolved opt-out set, not "some rows written."**
- **Dependencies:** SP2 (the resolved set), SP3 (the surface that produces it). Touches shared onboarding wiring LAST.

## Phase-level definition of done
A new user, after picking root categories, sees YouTube/X/Personalities filtered to `topic_tags ∩ chosen categories` (popularity-ordered, no randoms), with recommended clusters **pre-selected** to deselect; selecting/deselecting clusters bulk-follows/unfollows their members; the grid honors no-dup; and committing persists exactly the resolved opt-out follow set to `user_content_sources` / `user_personalities`. **Validated by:** the SP1 category-filter + no-dup resolver tests, the SP2 opt-out selection-model tests (incl. deselect-keeps-individual-member + zero-cluster), the SP3 render/no-dup tests, and the SP4 persist-exact-set + flow test. LIVE-E2E residual (real onboarding → real follows in live DB) is **deferred**.

## Out of scope
- The cluster **schema, seed, and resolver internals** (M1 owns these; M6a consumes).
- The **feed-mix / `feed_assembly`** changes (Phase FSR-M6b).
- Source **ingestion** (Phase 5d, shipped) — following records intent only.
- The control surface / per-source 3-state priority UI (Phase 5e) — follows default to `everything`.
- Algorithmic cluster generation / catalog discovery (PRD Out of Scope).
- New design tokens (`reference/design-language.md` untouched — reuse).

## Open questions
1. Does M1 expose a **TS-callable** cluster read, or only a Python/SQL resolver? If only SQL, SP1 builds the thin TS read over M1's tables (assumed; flagged).
2. Which clusters are "recommended" (pre-selected)? Assumed: an editorial `is_recommended` flag (or top-N by member popularity) on `source_clusters` from M1. Pin with M1's planner. If absent, SP2 falls back to pre-selecting the top cluster per chosen category and flags it.
3. Confirm target volumes are *guidance*, not hard caps in the UI (~30–40 YT, ~40–50 X, ~4 personalities) — assumed guidance only; no enforcement gate.

## Self-critique

**Product lens:** PASS. Traces M6's onboarding half to sub-phases: filter ∩ category (SP1), popularity order (SP1), opt-out pre-selection (SP2), cluster bulk-select (SP2/SP3), no-dup grid (SP1/SP3), zero-cluster path (SP2/SP3), `user_content_sources`/`user_personalities` writes (SP4) — every PRD M6 offline-check UI bullet maps to a DoD. No scope creep: control surface, ingestion, and cluster authoring are explicitly out of scope and deferred to their owning phases. The riskiest M6 assumption (follows actually shape the feed) is *carried* by FSR-M6b's allocator, not here — correct split; M6a's risk (will users curate?) is mitigated by reusing Phase 5c's proven components.

**Engineering lens:** PASS with one flagged dependency. Every DoD is fresh-context verifiable (mock-asserted reads/writes, RTL renders against fixtures) — no "works end-to-end." Stack-conformant: client-side Supabase reads under RLS (static export, no server runtime), pure selection model, fixture-rendered UI — matches Phase 5c. SP4 (the flow-wiring/lock-in step) correctly comes last, after the read/model/surface exist, so it doesn't cement the selection shape early. SP1 and SP2/SP3 are not secretly the same: SP1 is the catalog/cluster *read* (no-dup resolution), SP2 is the *selection state* (opt-out math) — distinct. **Hard dependency on M1's cluster schema+resolver is surfaced, not assumed silently** (Open Q1, Rule 12) — if M1 is absent, SP1 stops loudly.

**Risk lens:** PASS. File-boundary conflicts: `src/lib/sources.ts` is touched in SP1 (add `listSourcesByCategory`) and SP4 (add `followPersonality`) — **marked SEQUENTIAL / same-file**; the additions are non-overlapping functions but the dependency is explicit so `/run-phase` serializes them. `OnboardingFlow.tsx` / `onboarding/page.tsx` touched only in SP4 (also edited by Phase 5/5c — run those first). Test coverage: every behavioral DoD names a test that fails when the rule breaks (no-dup leak, deselect-keeps-member, exact-follow-set) — Rule 9 satisfied; pure-visual polish is the only manual-smoke. Reversibility: all writes are reversible follow rows — no migration, no public API, no deletion. Painting-into-a-corner: SP1→SP2→SP3→SP4 each consumes the prior's shapes and SP4 still works given SP1–3 state (it just commits the SP2 set the SP3 surface produced) — no reorder needed.

**Irreversible sub-phases:** none (all writes are reversible user follow rows; no schema change — schema is M1's).
