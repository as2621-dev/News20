# Phase 2: Story Detail + Trust Layer

**Milestone:** M2 — Detail View + trust + interrogation
**Status:** Not started
**Estimated effort:** L

## Goal
Swipe-right from any reel story opens a readable, trust-laden Story Detail — chunked Playfair body, key-figure card, the bias/coverage/blindspot/opposing-view trust strip, and an expandable "how it developed" timeline — all from Supabase-direct reads, with drag-to-open/close.

> **⚠ Prerequisite (Rule 12 — read before running):** This phase assumes **M1** has delivered: the Next.js 15 static-export scaffold, the Supabase client (`src/lib/supabase`), the **migrated + seeded** content tables (`stories`, `detail_chunks`, `story_trust`, `story_timeline`, `story_sources`, `suggested_questions`, `segments`), the Tailwind token config + fonts (`reference/prototype-port-map.md` §4), and the `LayerStack` shell + reel layer to swipe *from* (`prototype-port-map.md` §1, build-order item 1). **M1 is NOT YET PLANNED.** Run `/plan-phases M1` then `/run-phase` for M1 first, or satisfy these prereqs manually before this phase. All sub-phase DoDs below assume the prototype's `data.js` content for stories `s1`–`s5` is seeded into Supabase.

## Sub-phases

### Sub-phase 1: Detail data layer + types
- **Files touched:** `src/types/detail.ts`, `src/lib/detail/fetchStoryDetail.ts`
- **What ships:** TS interfaces matching `reference/api-contracts.md` + `reference/supabase-schema.md` (`StoryDetail`, `BiasBreakdown`/`TrustSummary`, `StorySource`, `TimelineEvent`, `KeyFigure`) and a typed `fetchStoryDetail(story_id)` that issues Supabase-direct reads against `detail_chunks` (ordered by `chunk_index`), `story_trust` (coverage L/C/R + `coverage_outlet_count` + `blindspot_lean` + `opposing_view_text`), `story_timeline` (ordered by `timeline_event_index`), `story_sources`, `suggested_questions`, plus the key-figure fields on `stories` — returning one populated, validated object.
- **What ships (observable):** one function call returns the full Detail payload for a story.
- **Definition of done:** `fetchStoryDetail('s1')` against the seeded project returns an object with ≥1 ordered detail chunk, populated coverage counts + outlet_count, ≥1 ordered timeline event, and the key-figure value/label. A unit test mocks the Supabase client and asserts each column maps to the right field **and that chunks/timeline come back in index order** (Rule 9: the test fails if a column is mis-mapped or ordering is dropped). `next lint` / Biome passes.
- **Dependencies:** none (within phase). Prereq: M1 Supabase client + seeded content tables.

### Sub-phase 2: Detail layer shell — drag-to-open panel, staggered reveal, body + key figure, mount slots
- **Files touched:** `src/components/detail/StoryDetail.tsx`, `src/components/detail/KeyFigureCard.tsx`, `src/components/detail/TrustStrip.tsx` (stub slot), `src/components/detail/StoryTimelineDrawer.tsx` (stub slot), `src/components/shell/LayerStack.tsx` (wire `.layer-detail`), `src/app/(reel)/page.tsx` (mount)
- **What ships:** The swipe-right `.layer-detail` panel (framer-motion `drag="x"`, `translateX(100%)→0`, drag-to-close gated on `scrollTop < 10` per `prototype-port-map.md` §3.2), the reel dim/scale-back (`scale(0.94) brightness(0.45)`) driven by drag progress, the staggered `.reveal` entrance (stagger container, §3.3), the chunked **Playfair** reading body from `detail_chunks`, the `KeyFigureCard` reading `var(--accent)`. **Critically, `StoryDetail.tsx` renders the layout with `<TrustStrip/>` and `<StoryTimelineDrawer/>` already imported as minimal stubs** so SP3/SP4 fill only their own files (no shared-file edits — see Risk lens).
- **Definition of done:** Drag-right from a seeded reel story opens Detail showing the story's ordered chunked body + key figure; drag-from-top closes it; reel dims/scales behind; `useReducedMotion()` path snaps instantly with no stagger. Manual UI smoke + a component test asserting chunks render in `chunk_index` order.
- **Dependencies:** Sub-phase 1. Prereq: M1 `LayerStack`/reel.

### Sub-phase 3: Trust strip — BiasBar + coverage + blindspot + opposing view
- **Files touched:** `src/components/detail/TrustStrip.tsx` (flesh out the SP2 stub), `src/components/detail/BiasBar.tsx`, `src/components/detail/OpposingViewCard.tsx`
- **What ships:** The "COVERAGE" authority strip — `BiasBar` rendering L/C/R proportions from `story_trust.coverage_{left,center,right}_count` using the `bias-left|center|right` tokens, the "COVERED BY N OUTLETS" count (mono), a blindspot chip shown only when `blindspot_lean` is set (the >70%-one-side rule is already applied at write time), and `OpposingViewCard` rendering `opposing_view_text`.
- **Definition of done:** For a seeded story with a blindspot, the bias-bar segment widths equal the normalized counts (test asserts the proportion math), the blindspot chip shows the under-covered lean, and the opposing-view card renders its quote; a story with `blindspot_lean = NULL` shows **no** chip. The test encodes the proportion math + the blindspot-present/absent branch (Rule 9 — fails if a fabricated proportion or a wrong blindspot state slips through).
- **Dependencies:** Sub-phase 2 (consumes the SP2 stub slot; touches only its own files).

### Sub-phase 4: Timeline drawer — "HOW IT DEVELOPED"
- **Files touched:** `src/components/detail/StoryTimelineDrawer.tsx` (flesh out the SP2 stub)
- **What ships:** The collapsed/expandable timeline drawer rendering `story_timeline` events (`timeline_when_label` mono + `timeline_what_text`) in `timeline_event_index` order, with an expand/collapse toggle.
- **Definition of done:** Drawer starts collapsed; tapping expands it to show all events in index order; tapping again collapses. A component test asserts event ordering + the collapsed↔expanded toggle state (Rule 9 — fails if events render out of order or the toggle is a no-op).
- **Dependencies:** Sub-phase 2 (consumes the SP2 stub slot; touches only its own file — **no overlap with SP3's files**).

## Phase-level definition of done
On a simulator/device (or browser preview), swiping right from a seeded reel story opens a Story Detail that reads top-to-bottom: Playfair chunked body → key-figure card → trust strip (bias bar + outlet count + blindspot + opposing view) → expandable timeline — all populated from Supabase-direct reads, with drag-to-open/close and a reduced-motion fallback. No Q&A yet (that is Phase 2b). `/run-phase` validates: `fetchStoryDetail('s1')` returns a complete payload **and** the rendered Detail shows every section for a blindspot story and a no-blindspot story.

## Out of scope
- Q&A composer / thread / RAG grounding / citation chips — **Phase 2b**.
- A generic "supporting visuals" gallery (graph / chart / image carousel). The prototype's Detail has only the **key-figure card + ambient poster**; `supabase-schema.md` has no `detail_visuals` table. See Open question #2.
- `player_signals` emission on `open_detail` — defers to **M3** (signals are `auth.uid()`-scoped and auth is M3; M2 stays auth-free).
- Voice mode, follows/saves surfaced from Detail, supporting-visual generation.

## Open questions
1. **⚠ M1 not planned (prerequisite).** See the prerequisite banner above. Decide: run M1 first, or stand up the minimal scaffold (Supabase client + content-table migration + seed + `LayerStack`/reel stub) as a pre-step. This phase does **not** include schema migration (that belongs to M1 — Rule 3/7, don't duplicate).
2. **`DetailVisual[]` conflict (Rule 7).** `api-contracts.md` models `detail_visuals: DetailVisual[]` (graph/timeline/image/chart), but the newer, prototype-derived `supabase-schema.md` has no such table. Resolved toward the schema (more recent, more tested): **no generic visuals gallery in M2**; the Detail's only visuals are the key-figure card + ambient poster. Action: mark `DetailVisual[]` in `api-contracts.md` as stale and reconcile the doc.

## Self-critique

**Product lens:** PASS with note. Traces to M2's "True when": *read* (chunked body, SP2) + *see who's covering* (trust strip, SP3) ship here; *ask a question* ships in Phase 2b. No scope creep — cut the `DetailVisual[]` gallery (OQ#2) and deferred `player_signals` to M3. The brief's "trust layer is a power-user feature for a casual persona" caveat (brief Open Q4) is acknowledged: it's cheap, schema-backed, and not expected to drive the habit — kept because it's one static-table read, not a build.

**Engineering lens:** PASS. No stack escape — all four sub-phases are Supabase-direct reads + React components (within the static-SPA stack; no migrations, no worker). Every DoD is fresh-context-checkable (column-mapping test, proportion-math test, ordering/toggle test) rather than "works end-to-end." SP4 (timeline) does not cement any API shape. SP3 vs SP4 are genuinely distinct surfaces (trust strip vs developmental timeline), not the same thing.

**Risk lens:** PASS (mitigated). **File-boundary risk:** SP3 and SP4 both render *inside* `StoryDetail.tsx`. Mitigation: **SP2 creates `StoryDetail.tsx` with `<TrustStrip/>` and `<StoryTimelineDrawer/>` already imported as stubs**, so SP3 and SP4 edit only their own component files — no shared-file write, safe to run in parallel worktrees. Test coverage: each DoD carries a test (SP2/SP4 also flagged manual UI smoke). Reversibility: read-only phase — no migrations, no external writes.

**Irreversible sub-phases:** none.
