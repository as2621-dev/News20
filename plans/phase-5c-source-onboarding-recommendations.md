# Phase 5c: Source onboarding + recommendation screens

**Milestone:** M5 — Two-axis personalization (sources + control surface)
**Status:** Not started
**Estimated effort:** L

## Goal
After the topic picker, map the user's interest profile to the nearest **archetype** and walk them through recommendation screens — **YouTube channels → X/personalities → podcasts** — each showing avatar + name + a follow toggle + a **search-and-add** affordance, with follows persisted RLS-scoped and the whole flow skippable.

## Why this phase exists
This is the user-facing half of the sources axis (spec §3): "people like you follow these." It turns the 5b catalog into instant, archetype-matched recommendations and lets users add anyone off-list. It is the riskiest *UX* of M5 (will users curate sources?), so its screens are simple ports of TL;DW's proven components, re-skinned.

## Context the sub-agents need
- **Donor UI:** `reference/sources-reuse-map.md` §5 — lift `source-artwork.tsx`+`portrait-bg.ts` (universal avatar), `source-card.tsx`/`picker-screen.tsx` (selectable grid), `source-search-modal.tsx` (search-add), `personality-grid.tsx:89-209` (optimistic Follow/Following + toast rollback), and the matcher `api/onboarding/analyze/route.ts` + `api/sources/recommended/route.ts`. **Re-skin to News20 tokens** (`reference/design-language.md`) — do NOT carry TL;DW's amber palette.
- **Static-export split (Rule 7):** reads over **our** seeded catalog (recommendations) = **client-side Supabase**. Live external-API search (YouTube/iTunes/X for "add anyone not in the catalog") needs API keys → goes through the **FastAPI worker** (`agents/worker/`, the same surface that already serves QA/voice endpoints), **not** an on-device call. The X resolver is **build-fresh** (no donor).
- **Inputs:** the user profile from Phase 5 (`user_entity_follows` + `user_interest_profile`) or, if Phase 5 hasn't run, the existing `phase-1e` chip profile. Either yields an interest vector over the 8 categories.
- **Writes:** follows go to `user_content_sources` / `user_personalities` (Phase 5b data layer `src/lib/sources.ts`).
- **Flow:** extends `src/app/(onboarding)/onboarding/page.tsx` to run source screens **after** the topic picker; each screen skippable (mirror the picker's skip).

## Sub-phases

### Sub-phase 1: Archetype mapping + recommendation read
- **Files touched:** `src/lib/archetypeMatch.ts`, `src/lib/sourceRecommendations.ts`.
- **What ships:** `mapToArchetype(interestVector)` (cosine similarity of the user's 8-category vector vs `archetypes.archetype_vector`, returns nearest + score; falls back to `balanced-generalist` below a threshold) and `getRecommendedSources(kind, { archetypes, subNiches, limit })` — a client-side Supabase read of `content_sources` filtered by `personas && archetypes`, ordered by `popularity_score`, **round-robin merged** across the top archetypes (port `api/sources/recommended/route.ts:148-189`), with `is_already_added` annotated by joining the user's follows.
- **Definition of done:** a sample profile heavy in `ai`+`tech` maps to `ai-frontier-tech`; a flat profile maps to `balanced-generalist`; `getRecommendedSources('youtube_channel', …)` returns popularity-ordered channels balanced across multiple matched archetypes with `is_already_added` set correctly. Mock-asserted.
- **Dependencies:** Phase 5b (schema + catalog + `src/lib/sources.ts`); Phase 5 or `phase-1e` (profile).

### Sub-phase 2: Universal avatar + selectable source card (port + reskin)
- **Files touched:** `src/components/sources/SourceArtwork.tsx`, `src/lib/portraitBg.ts`, `src/components/sources/SourceCard.tsx`.
- **What ships:** `SourceArtwork` (an `<img>` with `referrerPolicy="no-referrer"` + broken-image→initials-gradient fallback; `kind` drives circle for person, square for channel/podcast) + `portraitBg` (stable hashed gradient + initials) + a selectable `SourceCard` (avatar + name + description + follow toggle, real `<button>` with `aria-pressed`), all re-skinned to News20 tokens.
- **Definition of done:** the avatar renders a thumbnail and **falls back to an initials-gradient** when the URL 404s; a person renders as a circle, a channel/podcast as a square; the card toggles selected state and exposes `aria-pressed`. RTL test incl. the broken-image fallback path.
- **Dependencies:** Phase 5b SP4 (types).

### Sub-phase 3: Search-and-add modal + optimistic follow (incl. X resolver)
- **Files touched:** `src/components/sources/SourceSearchModal.tsx`, `src/components/sources/FollowButton.tsx`, `src/lib/sourceSearch.ts`, `agents/worker/` (new source-search endpoint), `agents/ingestion/adapters/` (X handle resolver helper).
- **What ships:** a debounced (300ms) search modal hitting the worker's source-search endpoint (YouTube 2-step + iTunes ported from `api/sources/search/route.ts`; **X-handle resolver built fresh**) → results with **Add → Adding → Added** states + `is_already_added` badge; `FollowButton` with **optimistic Follow/Following + toast rollback on failure** (port `personality-grid.tsx:89-164`).
- **Definition of done:** searching a channel name returns addable YouTube results; clicking Add optimistically flips to Following and persists to `user_content_sources`; a forced API failure rolls the toggle back and fires a toast; an already-followed source shows "Added"; an `@handle` resolves via the X resolver (mocked) or is stored as a pending `x_account` free-text follow. Mock-asserted (worker endpoint + Supabase mocked).
- **Dependencies:** Phase 5b SP4.

### Sub-phase 4: Three recommendation screens + flow wiring
- **Files touched:** `src/components/sources/SourceRecScreen.tsx`, `src/app/(onboarding)/onboarding/page.tsx`, `src/components/onboarding/OnboardingFlow.tsx`, `src/lib/onboardingProfile.ts` (mark source step complete).
- **What ships:** a reusable `SourceRecScreen` (rec grid of `SourceCard`s from SP1 + the SP3 search-add) instantiated per axis, sequenced **YouTube → X/personalities → podcasts** after the topic picker; each screen **skippable**; follows persist to `user_content_sources`/`user_personalities`; on completion sets a `source onboarding complete` flag and routes to the reel.
- **Definition of done:** a new user post-picker sees archetype-matched channels, then personalities/X, then podcasts (each avatar+name+follow+search-add); follows persist RLS-scoped; any screen can be skipped (no error); the flow advances picker → 3 source screens → reel and a returning user skips straight to the reel. Flow + mock test.
- **Dependencies:** Sub-phases 1–3.

## Phase-level definition of done
A new user, after the topic picker, is mapped to an archetype and shown three skippable source-recommendation screens (YouTube / X+personalities / podcasts), each with avatar + name + follow toggle + working search-and-add (incl. a fresh X resolver), with follows persisted RLS-scoped to `user_content_sources`/`user_personalities`, and the onboarding flow wired picker → sources → reel. **Validated by:** archetype-match test; avatar fallback test; optimistic-follow + rollback test; the per-axis recommendation + skip + flow tests.

## Out of scope
- **Ingestion** of followed sources (Phase 5d) — following here only records intent.
- The **control surface** / per-source priority UI (Phase 5e) — follows default to `everything`.
- The **research agent** (Phase 6).
- Re-ranking sophistication beyond popularity + round-robin (note re-rank strength as open Q).

## Open questions
1. **Re-rank strength** — how hard sub-niche picks re-weight an archetype's defaults (spec §7). Recommend a small linear boost for v1.
2. **Multi-archetype users** — round-robin top-2 (recommended, matches donor) vs single nearest.
3. **X API choice + key** for the resolver/search (worker endpoint) — which API, cost, rate limits.
4. **Onboarding length** — topics + 3 source screens is long; confirm per-screen skip is enough (recommended).

## Self-critique
**Product lens:** PASS — ships spec §3 (the three recommendation screens with avatar+name+follow+search-add) and §2.2 (archetype mapping → instant recs). Skippable per screen keeps onboarding non-blocking. Re-skin requirement prevents importing TL;DW's look.
**Engineering lens:** PASS — correctly splits client-side Supabase reads (our catalog) from worker-side external search (keys) — the static-export constraint would otherwise leak API keys on-device. Ports proven donor components/matcher (Decision #12) and flags the one build-fresh piece (X resolver). DoDs are mock-verifiable. SP4 wires the flow last, after the pieces exist.
**Risk lens:** PASS with flags. No irreversible changes (all writes are reversible user follow rows). Within-phase overlap: `onboarding/page.tsx` + `OnboardingFlow.tsx` touched only in SP4; `onboardingProfile.ts` only in SP4 — no conflict. **Cross-phase note:** `onboarding/page.tsx` is also edited by Phase 5 SP4 — run Phase 5 first (dependency). Test coverage: optimistic-rollback and broken-avatar paths are explicit DoDs (the two likeliest UX failures). New external dependency (X API) is flagged as an open question, not silently assumed.
**Irreversible sub-phases:** none (worker endpoint + UI + reversible follow rows).
