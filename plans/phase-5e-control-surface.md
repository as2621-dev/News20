> **⚠ SUPERSEDED by phase-5a (2026-06-05) — see `plans/phase-5a-build-your-30-and-entity-ranker.md`.**
> This phase's master-dial + draggable 30-cell-ribbon design is **retired**. The owner replaced it with the **"Build your 30, in order"** screen (one ordered list, explicit per-category slot counts, manual sequence). Phase-5a shipped that screen's **backend** — migration 0008 (`user_feed_allocation` + the 8 `feed_category` keys), the category-budget + manual-sequence allocator, and the entity-aware ranking (`reference/ranking-spec.md` §3a). Only the frontend screen remains; it writes the `user_feed_allocation` contract phase-5a defined. Kept for planning history.

# Phase 5e: Control surface (allocation settings)

**Milestone:** M5 — Two-axis personalization (sources + control surface)
**Status:** Not started
**Estimated effort:** L

## Goal
A settings screen where the user balances the two axes across the fixed **30-story** window — a **My Sources ↔ Discovery** master dial, per-source priority, a draggable 30-cell topic ribbon, and presets — with a **live preview** that re-renders on every drag, all resolved by the **pinned-sources-fill-first** rule and driving the real per-user `daily_feeds`.

## Why this phase exists
This is the heart of spec §5 and Decision #11's conflict rule. It's the only place the user reconciles topics vs sources, and the live preview is the key UX detail that makes allocation feel concrete. No donor exists — built fresh, re-skinned to News20 tokens.

## Context the sub-agents need
- **Spec:** `reference/control-surface-spec.md` (the allocation algorithm §3, controls §2, live preview §4). Pinned-first: `sourceBudget = round(30 * (1 - dial/100))`; sources fill first by priority+freshness; topics fill the remainder by ribbon weights; sparse sources **soft-bias** roll into topics (feed stays 30); no double-counting.
- **Existing feed build:** `agents/pipeline/feed_assembly.py` produces `daily_feeds` per `reference/ranking-spec.md`. This phase makes the control-surface prefs an **input** to that build, and shares the allocation logic with the client preview.
- **Per-source priority** already exists as `user_content_sources.source_priority` (enum `off|big_stuff|everything`, Phase 5b). The UI extends TL;DW `source-item-row.tsx`'s active/paused `Switch` to this 3-state.
- **Static-export:** prefs read/write = client-side Supabase under RLS; the live preview runs the allocation **client-side** against a cached content snapshot; the authoritative allocation runs in `feed_assembly.py`.
- **Latest migration after 5b is `0008`** → this phase adds **`0009`**.
- **"Existing" ribbon design (spec §5.3):** locate the referenced artifact before building (open Q); the 8 categories are the colors (C1).

## Sub-phases

### Sub-phase 1: Migration 0009 — feed prefs + prefs data layer
- **Files touched:** `supabase/migrations/0009_feed_prefs.sql`, `src/lib/feedPrefs.ts`.
- **What ships:** `user_feed_prefs` (`user_id`→`users` PK, `master_dial int default 50` [0=all sources, 100=all discovery], `ribbon_allocation jsonb` [per-category weights over the 8], `active_preset text null`, `updated_at`) with owner-only RLS; plus `src/lib/feedPrefs.ts` client-side `getFeedPrefs()` / `upsertFeedPrefs(prefs)`.
- **Definition of done:** migration applies on a DB holding 0001–0008; a prefs row upserts scoped to `auth.uid()`; an anon/other-user `SELECT` returns zero rows (RLS); `getFeedPrefs` returns sane defaults (dial 50, even ribbon) when no row exists. Mock + SQL assertion. ⚠ irreversible (forward-only).
- **Dependencies:** Phase 5b SP1.

### Sub-phase 2: Allocation function (pinned-first) — shared client + pipeline
- **Files touched:** `src/lib/feedAllocation.ts`, `agents/pipeline/feed_assembly.py` (extend), `reference/control-surface-spec.md` (lock the algorithm).
- **What ships:** the pinned-sources-fill-first allocator implementing control-surface-spec §3 — compute `sourceBudget` from the dial; fill source slots by `source_priority` + freshness (respecting `big_stuff` vs `everything`); fill the remainder across categories by `ribbon_allocation` weights (reusing the `ranking-spec` per-story score within a category); guarantee **no story double-counts** across buckets; **soft-bias** unfilled source slots into topics so the feed stays 30. Implemented once as a spec and mirrored in TS (`feedAllocation.ts`, for preview) and Python (`feed_assembly.py`, for the real `daily_feeds`).
- **Definition of done:** unit tests cover dial extremes (dial=0 → all 30 from sources when available; dial=100 → all topics), the sparse case (few source items → unfilled source slots roll into topics, feed still 30), and the **no-double-count** invariant (a story claimed by a source never also appears under its topic). Rule 9: the tests fail if pinned-first or no-double-count is violated, not just on a crash. Both the TS and Python implementations pass the same fixtures.
- **Dependencies:** Sub-phase 1.

### Sub-phase 3: Control-surface UI — dial, priority list, ribbon, presets
- **Files touched:** `src/components/settings/MasterDial.tsx`, `src/components/settings/SourcePriorityList.tsx`, `src/components/settings/TopicRibbon.tsx`, `src/components/settings/FeedPresets.tsx`.
- **What ships:** the master dial (0–100, "My Sources ↔ Discovery"); the followed-sources list with a **3-state priority** control (`off|big_stuff|everything`, persisting to `user_content_sources` via `src/lib/sources.ts`); the 30-cell color-coded `TopicRibbon` (drag category boundaries, colors = the 8 categories); and the three `FeedPresets` (Power Feed / Balanced / Wide Lens) that set the dial + ribbon defaults.
- **Definition of done:** dial/priority/ribbon/presets render and write to `user_feed_prefs` / `user_content_sources` (mocked Supabase); the 3-state priority persists and round-trips; a preset sets dial + ribbon to its documented defaults. RTL tests per control.
- **Dependencies:** Sub-phases 1, 2.

### Sub-phase 4: Live preview + settings page wiring
- **Files touched:** `src/components/settings/AllocationPreview.tsx`, `src/app/(app)/settings/page.tsx` (new settings route), `src/lib/feedPrefs.ts` (snapshot cache).
- **What ships:** the 30-cell **live preview** that re-renders on every drag (master dial / a priority / a ribbon boundary) by running SP2's `feedAllocation` client-side against a cached per-source/per-category availability snapshot — **source cells show a tiny avatar** (`SourceArtwork`), **topic cells show the category color**; the settings page composing dial + priority list + ribbon + presets + preview; committing persists prefs so the next `feed_assembly` build honors pinned-first.
- **Definition of done:** dragging the dial / a source priority / a ribbon boundary **instantly** re-renders the 30-cell preview with avatars on source cells and colors on topic cells (unit-tested on the preview allocation; the drag interaction is manual smoke — **flagged**); committing persists to `user_feed_prefs`; an integration check shows the next `feed_assembly` run produces `daily_feeds` honoring the saved dial/priority/ribbon (sources first, no double-count).
- **Dependencies:** Sub-phases 1–3.

## Phase-level definition of done
A user sets the My-Sources↔Discovery dial, per-source priority, topic ribbon, and presets; sees a 30-cell live preview update instantly (avatars on source cells, colors on topic cells); and those prefs drive the real per-user `daily_feeds` via pinned-sources-fill-first with soft-bias on sparse days and no double-counting. **Validated by:** migration + RLS; the allocation invariant tests (dial extremes, sparse, no-double-count) passing in **both** TS and Python; the per-control write tests; the preview re-render + commit→feed_assembly integration check.

## Out of scope
- **Learned ordering** of the feed (Phase 6b) — v1 ordering is manual (dial + ribbon + priority).
- **Ingestion** itself (Phase 5d) — this consumes the pool it fills.
- Defining "Only their big stuff" precisely (decided here as a threshold, but the threshold's *learning* is M6).
- The recommendation/onboarding screens (Phase 5c).

## Open questions
1. **Hard split vs soft bias** on sparse-source days — recommend **soft bias** (feed stays full at 30); lock in SP2.
2. **"Only their big stuff" threshold** — engagement? duration? topic match? Pick a v1 heuristic in SP2/5d.
3. **Locate the "existing" 30-cell ribbon design** artifact (spec §5.3) before SP3.
4. **Preview snapshot cache** — where the per-source/per-category availability snapshot comes from (a `daily_feeds` precompute? a cached count query?) for an instant client-side preview.

## Self-critique
**Product lens:** PASS — delivers spec §5 end to end: the master dial (§5.1), per-source priority (§5.2), ribbon (§5.3), presets (§5.4), conflict rule (§5.5), and the live preview (§5.6 — explicitly the "key UX detail"). The pinned-first rule is the load-bearing product decision (#11) and is the central DoD.
**Engineering lens:** PASS — the allocation logic is specified **once** and mirrored in TS (preview) and Python (authoritative `feed_assembly`), with the **same fixtures** proving parity — preventing preview/reality drift, the obvious failure mode. Builds on the existing `feed_assembly.py`/`ranking-spec` rather than a parallel allocator. Static-export reality respected (client preview + server build). SP4 wires the page last.
**Risk lens:** PASS with flags. ⚠ SP1 forward-only migration. The **highest risk is preview ≠ real feed** — mitigated by shared fixtures across the TS/Python implementations (SP2 DoD). Within-phase overlap: `feedPrefs.ts` touched by SP1 (create) and SP4 (snapshot cache) — sequential, dependency marked. Test gap: the drag *interaction* is manual smoke (flagged per Rule 12); the allocation it drives is unit-tested. Painting-into-a-corner: prefs schema → allocator → controls → preview+wire; the allocator existing before the UI means the preview can't diverge from the build.
**Irreversible sub-phases:** SP1 (forward-only migration).
