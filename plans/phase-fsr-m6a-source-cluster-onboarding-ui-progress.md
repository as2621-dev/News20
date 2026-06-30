# Progress: phase-fsr-m6a-source-cluster-onboarding-ui

**Phase file:** plans/phase-fsr-m6a-source-cluster-onboarding-ui.md
**Started / shipped:** 2026-06-30
**Branch:** claude/feed-source-revamp-plan-388edf

## Test-env note
- TS = vitest (jsdom) via `node_modules/.bin/vitest run`. No live DB; GDELT 403.
- Baseline: ONE pre-existing unrelated vitest failure — `tests/lib/app/tabBar.test.tsx`
  (expects no `Thirty` tab). Verified present on the clean tree; NOT this phase's
  regression. After M6a: 1 failed | 527 passed (528) — the only failure is that baseline.
- RTL is NOT a project dependency. The phase file says "RTL tests"; the codebase
  convention (Rule 11) is `react-dom/client` createRoot + `react` act (mirrors
  `tests/lib/sources/sourceCard.test.tsx`). Used that harness — coverage is identical.

## Key decisions (surfaced)
- **TS mirror of M1's Python resolver (not an RPC call).** M1's `resolve_category_clusters`
  is Python; the device is a Capacitor static export with NO server runtime / no ranking
  RPC, so SP1 ships `src/lib/sourceClusters.ts::resolveCategoryClusters` — a PURE TS mirror
  of the Python resolver's exact semantics (filter+is_curated, stable (sort_order, slug)
  order, no-dup suppression set, first-cluster-wins dedup, empty-cluster drop, skip
  missing/un-curated). It is fixture-tested against the SAME no-dup cases as M1.
  `getClustersForCategories` is the live read over the public-read 0009+0022 tables,
  fetching the FULL per-category source/personality POOLS (topic_tags overlap) so the
  no-dup match can see bundled rows — honoring `cluster_query.py`'s "load-bearing subtlety".
- **opt-out selection model:** `toggleCluster` PRESERVES per-member overrides (does NOT
  clear them) — that is what makes "deselect a cluster after individually keeping a member
  leaves the member followed" (PRD edge / User Story 12) actually hold. An override always
  wins over bulk state in `isMemberFollowed`. (First draft cleared overrides on toggle,
  which defeated the edge; corrected during SP2 self-review.)
- **flow wiring:** the `sources` step now renders the new `SourceClusterScreen` (M6 cluster
  grid) instead of `SourceSwipe` (Phase 5c). `SourceSwipe` is NOT deleted (other code +
  5c tests still reference it); only its usage in `OnboardingFlow` was swapped. The grid
  emits its INITIAL pre-selection once on mount so `Continue` commits the opt-out set even
  if the user toggles nothing.
- **recommended-cluster signal (Open Q2):** M1 has not pinned an `is_recommended` flag, so
  `SourceClusterScreen.recommendedSlugsFor` pre-selects EVERY resolved cluster (the opt-out
  default). Swap that one function when M1 exposes the flag.

## Sub-phase progress
- [x] SP1: category-keyed catalog + cluster read — `listSourcesByCategory` (sources.ts) +
  `sourceClusters.ts` (`resolveCategoryClusters` pure mirror + `getClustersForCategories`
  live read). DoD: topic_tags∩category filter, popularity order, no-dup leak test, empty
  cluster omission, multi-cluster dedup, skip missing/uncurated. PASS.
- [x] SP2: opt-out cluster-selection state model — `clusterSelection.ts`
  (`buildInitialSelection`/`toggleCluster`/`toggleMember`/`resolveFollowSet`). DoD:
  pre-select, deselect-keeps-individual-member, zero-cluster empty set, dedup, partition. PASS.
- [x] SP3: no-dup grid + cluster cards UI — `ClusterCard.tsx` + `SourceClusterGrid.tsx`
  (reuse `SourceArtwork`). DoD: pre-selected render, cluster toggle flips members, no-dup
  visible (renders resolver output only), empty-cell fallback, aria-pressed both controls. PASS.
- [x] SP4: persist follow set + flow wiring — `followPersonality`/`unfollowPersonality`
  (sources.ts), `commitClusterFollowSet` (sourceClusters.ts), `SourceClusterScreen.tsx`,
  `OnboardingFlow.tsx` wiring. DoD: writes EXACTLY the resolved opt-out set (sources →
  user_content_sources, personalities → user_personalities), empty commit no-ops, flow
  advances picker→sources→reel, step-complete marker set, returning user bypasses. PASS.

## STATUS: PHASE SHIPPED

### Files added
- `src/lib/sourceClusters.ts` — TS no-dup resolver mirror + live cluster read + commit.
- `src/lib/clusterSelection.ts` — pure opt-out selection model.
- `src/components/sources/ClusterCard.tsx` — selectable cluster card (avatar stack).
- `src/components/sources/SourceClusterGrid.tsx` — per-category cluster+member grid.
- `src/components/sources/SourceClusterScreen.tsx` — the M6 source step container.
- `tests/lib/sourceClusters.test.ts`, `tests/lib/clusterSelection.test.ts`,
  `tests/lib/sources/sourceClusterGrid.test.tsx`, `tests/lib/sources/sourceClusterScreen.test.tsx`.

### Files changed
- `src/lib/sources.ts` — add `listSourcesByCategory` (SP1) + `followPersonality`/
  `unfollowPersonality` (SP4). Non-overlapping additions; existing fns untouched.
- `src/components/onboarding/OnboardingFlow.tsx` — `sources` step → `SourceClusterScreen`.
- `tests/lib/sources.test.ts` — add `listSourcesByCategory` + personality-follow cases.

## LIVE-E2E residuals (deferred — no live DB here)
- Real onboarding → real cluster seed → real follows landing in `user_content_sources` /
  `user_personalities`, with the no-dup rule holding against the LIVE catalog.
- Confirming the cluster seed has real per-category coverage (catalog quality = the #1
  PRD risk; M1's live residual).
- The feed-mix coupling (followed items actually lead the feed) is FSR-M6b's job, not here.

## Concerns for M7 (docs)
- `reference/sources-reuse-map.md` / source-catalog-taxonomy should note the M6 onboarding
  is now CATEGORY-keyed (topic_tags ∩ chosen) + cluster-based, superseding the 5c
  archetype/persona-keyed `SourceSwipe` deck as the onboarding source step.
- The recommended-cluster pre-selection is "all resolved clusters" until M1 adds an
  `is_recommended` flag — doc the Open Q2 follow-up.
- Stale dead mock: `tests/lib/onboarding/onboardingFlowSessionSkip.test.tsx` still
  `vi.mock`s `@/components/sources/SourceSwipe` (no longer imported by OnboardingFlow).
  Harmless (splash branch never renders `sources`); cleanup candidate.
