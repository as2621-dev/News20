# Progress: phase-2-m2-detail-and-trust

**Phase file:** plans/phase-2-m2-detail-and-trust.md
**Started:** 2026-05-31
**Mode:** Sequential (no irreversible sub-phases; user chose sequential). Read-only phase — Supabase-direct reads, no migrations.
**Base commit:** 1ab2546

## Decisions (this run)
- **Shell pre-step (SP0) added.** Phase 2 assumed M1 shipped `src/components/shell/LayerStack.tsx` + a `(reel)` route to swipe from. Reality: reel is `src/app/page.tsx` → `PhoneShell` → `Reel`; no LayerStack, no lateral-layer plumbing, and `Reel` owns active-story state internally. SP0 builds the structural shell (LayerStack container + lift active story + mount in existing `page.tsx`) so SP1–SP4 run clean. `(reel)/page.tsx` is NOT created (would collide with `page.tsx` → build error; same trap Phase 1e documented).
- **Sequential** SP0 → SP1 → SP2 → SP3 → SP4. SP3/SP4 are parallel-safe by design (SP2 creates stubs) but user chose sequential.

## Prereqs verified
- SP1 data layer unblocked: 1b seed populated `detail_chunks`, `story_trust`, `story_timeline` (ordered), `story_sources`, `suggested_questions`, and `story_key_figure_value/label` on `stories`.
- `DetailVisual[]` conflict (phase OQ#2): `api-contracts.md:40-47` still models the stale `detail_visuals` gallery — SP1 follows `supabase-schema.md` (no visuals gallery), not that.
- framer-motion ^12.36.0 installed.

## Sub-phase progress
- [x] 0: Shell pre-step — **COMPLETE**. LayerStack.tsx + LayerStackContext.tsx (new), page.tsx + Reel.tsx (minimal). `openDetail`/`closeDetail` via `useLayerStack()`; reel dim/scale-back + reduced-motion; stub detail `<aside>` slot (translateX 100%→0) for SP2; exports `LATERAL_TRANSITION`/`REEL_SCALEBACK_*`. lint 0 / tsc 0 / vitest 95 / build OK. Report: sub-0.md.
  - **Naming quirk (flag, not fixed — Rule 3):** reel `Story.digest_id` field carries the `stories.story_id` value (`s1`..`s5`), per its own JSDoc. SP2 passes `activeStory.digest_id` → `fetchStoryDetail` → matches by value.
  - **Quote rule:** global CLAUDE.md says single quotes but repo Biome enforces `quoteStyle: double`; double wins (Rule 11).
  - **SP2 contract:** fill the `<aside aria-label="Story detail">` body (guard story != null); call `openDetail(activeStory)` for the swipe-right trigger; reuse exported transition/scaleback constants.
- [x] 1: Detail data layer + types — **COMPLETE**. `src/types/detail.ts` (StoryDetail/DetailChunk/TrustSummary/StorySource/TimelineEvent/SuggestedQuestion/KeyFigure/BiasLean; no DetailVisual gallery per OQ#2) + `src/lib/detail/fetchStoryDetail.ts` (6 concurrent PostgREST reads, fix_suggestion idiom) + `tests/lib/detail/fetchStoryDetail.test.ts` (7 tests, mocked client, asserts column-map + chunk_index/timeline order). tsc 0 / Biome clean / vitest 102 (12 files). **Live smoke** `fetchStoryDetail("s1")` PASS (3 chunks, cov 9/7/3, 19 outlets, blindspot=right, keyfig ~20%, 3 timeline, 2 sources, 3 Qs). Report: sub-1.md.
  - **SP2 consumes:** `fetchStoryDetail(activeStory.digest_id)` → StoryDetail. Nullable: key_figure.*, blindspot_lean, opposing_view_text, source_*. chunks/timeline pre-ordered — don't re-sort.
- [x] 2: Detail layer shell — **COMPLETE** (PENDING human drag-feel smoke). StoryDetail.tsx (fetch + staggered reveal + Playfair body in chunk_index order + KeyFigureCard + stub slots + scrollTop seam), KeyFigureCard.tsx (`var(--accent)`, null-safe), TrustStrip.tsx + StoryTimelineDrawer.tsx STUBS, LayerStack.tsx edited (motion.aside + drag-to-open via 28px edge region → openDetail, drag-to-close gated scrollTop<10 → closeDetail). tests/lib/detail/storyDetail.test.tsx (chunk-order, mutation-checked). Biome 0 / tsc 0 / vitest 105 (13 files) / build OK. Report: sub-2.md.
  - **Cleanup:** SP0's `LATERAL_TRANSITION` was a CSS string (unusable by framer) → replaced with structured `LATERAL_PANEL_TRANSITION`, dead const removed. Validation green.
  - **STUB contracts SP3/SP4 MUST preserve:** `TrustStrip({ trustSummary: TrustSummary })`, `StoryTimelineDrawer({ timeline: TimelineEvent[] })`. Data is pre-fetched + pre-ordered — no extra fetch, no re-sort. Do NOT modify StoryDetail.tsx or LayerStack.tsx.
- [x] 3: Trust strip — **COMPLETE**. BiasBar.tsx (+ pure `computeBiasSegmentProportions`, all-zero guard), OpposingViewCard.tsx (null-safe), TrustStrip.tsx fleshed out (chip → BiasBar → COVERED BY N OUTLETS mono → OpposingViewCard). tests/lib/detail/trustStrip.test.tsx (proportion math + blindspot present/absent + opposing-view branches, mutation-verified). Biome 0 / tsc 0 / vitest 117 (14 files) / build OK (after `rm -rf .next` — stale webpack cache, not code). `TrustStripProps` unchanged. Report: sub-3.md.
  - **Divergence RESOLVED (owner chose trim):** the BALANCED chip was removed to match the plan's literal DoD — `blindspot_lean=NULL` now renders NO chip (bias bar + outlet count + opposing view only). Re-validated: Biome 0 / tsc 0 / vitest 122 / clean build; null-blindspot test asserts no-chip (Rule-9 verified). `TrustStripProps` unchanged.
- [x] 4: Timeline drawer — **COMPLETE**. StoryTimelineDrawer.tsx fleshed out (collapsed-default, ≥44px toggle, index-order events, `font-mono` when-label, reduced-motion snap, empty-safe). tests/lib/detail/storyTimelineDrawer.test.tsx (collapsed→expand→collapse + ordering, mutation-verified). Biome 0 / tsc 0 / vitest 122 (15 files) / clean build OK. `StoryTimelineDrawerProps` unchanged. Report: sub-4.md.
  - **Concern (same class as SP2):** collapse-animation exit lingers under jsdom no-op rAF; test asserts on `aria-expanded`/`data-timeline-toggle` state (authoritative). Animated-height feel → human simulator smoke.
- [ ] 2: Detail layer shell — drag-to-open panel, staggered reveal, body + key figure, mount slots — PENDING
- [ ] 3: Trust strip — BiasBar + coverage + blindspot + opposing view — PENDING
- [ ] 4: Timeline drawer — "HOW IT DEVELOPED" — PENDING

## Phase-level passes (all PASS)
- **DoD:** PASS (automated). Clean combined-tree build green: Biome 57-file clean · tsc 0 · vitest 122/122 (15 files) · `next build` static export (`/` prerendered). `fetchStoryDetail('s1')` returns a complete payload (SP1 live-smoke). StoryDetail composes Playfair body → KeyFigureCard → TrustStrip (bias bar + outlet count + blindspot + opposing view) → expandable timeline; blindspot + no-blindspot branches tested; drag-open/close + reduced-motion implemented. **PENDING human simulator smoke:** real swipe/drag feel + collapse-animation feel + on-device colour/contrast (same class as Phase 1's pending visual smoke) — not faked.
- **Slop scan:** PASS. No TODO/FIXME/console.log/`any`/`as never`/localhost/dead-code; comments explain *why* (SP0 reel seam carries a `// Reason:`); no swallowed errors (fetchStoryDetail uses fix_suggestion idiom); BiasBar's one pure helper is a deliberate Rule-9 testing seam, not slop.
- **CSO:** PASS — read-only phase (M2 auth-free). No secret literals; no new endpoints/auth/middleware; `fetchStoryDetail(story_id)` uses parameterized PostgREST `.eq()` (no string interpolation; story_id comes from the reel's own feed, not user free-text); no new JS deps (package.json untouched); no token/PII logging.

## Status: COMPLETE — see commit hash in the run summary

## Scope hygiene
Staged ONLY Phase-2 paths. Explicitly EXCLUDED unrelated 1d-in-progress artifacts present in the tree: `agents/shared/exceptions.py`, `requirements.txt`, `agents/ingestion/`, `plans/phase-1d-daily-content-pipeline-progress.md`, and the stray `news_digest_app_report.docx`.
