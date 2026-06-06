# Phase 5c — Sub-phase 1 execution report: Archetype mapping + recommendation read

**Status:** SUCCESS
**Date:** 2026-06-05
**Scope:** Sub-phase 1 only (archetype mapping + client-side recommendation read).

## What I implemented

### 1. `src/lib/archetypeMatch.ts` — `mapToArchetype(interestVector, archetypes)`
A pure, deterministic function (no I/O) that maps a user's 8-category interest
vector to the nearest seeded archetype by **cosine similarity** against each
`archetypes.archetype_vector`.

- `ARCHETYPE_CATEGORY_KEYS` — the 8 pinned category keys in canonical lowercase
  order (`ai, geopolitics, business, environment, politics, tech, sport, arts`),
  matching `supabase/seed/archetypes.sql` and `reference/archetypes.md` §2. Cosine
  is computed over exactly these 8 keys (insertion-order-independent).
- `InterestVector` type = `Partial<Record<ArchetypeCategoryKey, number>>` (missing
  key → 0; values need not be normalized — cosine is magnitude-invariant).
- Returns `{ archetype_id, archetype_label, archetype_score, is_fallback }`
  (verbose names per CLAUDE.md). `archetype_id` is the archetype **slug** (the
  stable key the catalog read filters by).
- **Fallback:** when the top cosine is `< ARCHETYPE_MATCH_THRESHOLD` (0.5) OR there
  are no candidates / zero-magnitude vector, returns `balanced-generalist`
  (resolved from the same candidate list for the real label/id; degrades to the
  bare slug if absent). Threshold choice documented with a `// Reason:` comment.
- Zero-magnitude guard in `cosineSimilarity` returns 0 (never `NaN`).
- Full TS strict, no `any`. Structured logging via the existing `@/lib/logger`.

### 2. `src/lib/sourceRecommendations.ts` — `getRecommendedSources(kind, options)`
A client-side Supabase read that turns the user's matched archetype(s) into a
balanced, popularity-ranked, follow-annotated grid for ONE axis.

- Calls the Phase 5b helper `listSourcesByArchetype([slug], kind, limit, client)`
  **once per archetype** (single-element persona overlap → each list is that
  archetype's own popularity-desc top-K), in parallel.
- **Round-robin merge** (`roundRobinMerge`) across the per-archetype lists, deduped
  by `source_id` (ported from `api/sources/recommended/route.ts:148-189`). Single
  archetype skips the merge; empty `archetypes` short-circuits to `[]`.
- **Sub-niche boost** (`applySubNicheBoost`): `+0.1` per matched `topic_tag`
  (`SUB_NICHE_BOOST_PER_MATCH`), stable-sorted by boosted score. Tiny vs the 0–100
  popularity scale so it only re-orders near-equals (Open Q#1: modest linear v1;
  documented with a `// Reason:` comment).
- **`is_already_added`** annotated via the Phase 5b helper `getUserSources` (no raw
  SQL). The auth state is probed gracefully first (`client.auth.getUser()`); an
  **anon** browse degrades to "no follows" (all-false) without throwing, so
  onboarding can browse the catalog before sign-in. A real follow-read error still
  surfaces (Rule 12).
- Over-fetches `2× limit` per archetype and merges a `2× limit` candidate pool so
  the sub-niche re-rank has headroom to promote a slightly-lower-ranked match into
  the final `limit` (not just reorder a pre-capped head). Trims to `limit` last.
- Full TS strict, no `any`. Mirrors `sources.ts` client pattern + structured logging.

## Files created
- `src/lib/archetypeMatch.ts` (implementation)
- `src/lib/sourceRecommendations.ts` (implementation)
- `tests/lib/archetypeMatch.test.ts` (8 tests)
- `tests/lib/sourceRecommendations.test.ts` (11 tests)

No other files touched. `src/lib/sources.ts` was **not** edited.

## Divergences from the plan
- **None functional.** One deliberate clarification: the plan's DoD says "a flat
  profile maps to `balanced-generalist`". With the FULL 12-row seed present, a flat
  vector reaches `balanced-generalist` by **direction match** (cosine = 1.0,
  `is_fallback: false`), not via the threshold fallback — because
  `balanced-generalist` is the only uniform archetype, so a flat vector points
  exactly at it and wins. The threshold fallback (`is_fallback: true`) is exercised
  by the **zero/empty vector** (cosine 0) and by genuinely-diffuse vectors when the
  uniform catch-all is absent from the candidate list. The DoD outcome (flat →
  `balanced-generalist`) holds either way; both mechanisms are asserted. See
  "Concerns" below — this is a real property of the seeded vectors worth knowing.

## Code-review findings + fixes
| Sev | Finding | Resolution |
|-----|---------|------------|
| Medium | Round-robin originally capped the merge at `limit` BEFORE the sub-niche boost, so a sub-niche-matching source ranked just outside `limit` could never be promoted (over-fetch headroom wasted). | Fixed: merge a `2× limit` candidate pool, boost, then slice to `limit`. |
| Low | `resolveAlreadyAddedSourceIds` calls `auth.getUser()` and then `getUserSources` (which calls `auth.getUser()` again) → one redundant auth round-trip when authed. | Accepted: keeps reuse of the 5b helper (no raw SQL) and the graceful anon guard; the extra call is cheap and supabase-js caches the session. Noted, not changed. |
| — | Cosine math, zero-magnitude guard, round-robin index/off-by-one, array-overlap filter (single-element persona per archetype), `is_already_added` set membership — all reviewed correct. | No change. |

## Validation results (exact commands + pass/fail)
- `npx tsc --noEmit` → **PASS** (exit 0, no errors).
- `npx biome check src/lib/archetypeMatch.ts src/lib/sourceRecommendations.ts tests/lib/archetypeMatch.test.ts tests/lib/sourceRecommendations.test.ts` → **PASS** (auto-formatted once via `--write` for line-length 120; re-check clean, "No fixes applied").
- `npx vitest run tests/lib/archetypeMatch.test.ts tests/lib/sourceRecommendations.test.ts` → **PASS** (19 tests).
- `npx vitest run` (full suite, regression check) → **PASS** (302 tests, 33 files).

(One fix-and-rerun cycle was used: 3 initial test-expectation errors — my own incorrect fixtures for the round-robin dedup order and the sub-threshold fallback vector — were corrected against the real seeded cosine values. The implementation was not the cause.)

## Definition of done — PASS (per criterion)
- **A heavy ai+tech profile maps to `ai-frontier-tech`** — PASS (`{ ai: 0.6, tech: 0.4 }` → `ai-frontier-tech`, score 0.954, `is_fallback: false`; asserted).
- **A flat profile maps to `balanced-generalist`** — PASS (uniform vector → `balanced-generalist`, score ≈ 1.0; asserted). Threshold fallback additionally asserted for the zero/empty vector and a sub-0.5 diffuse vector.
- **`getRecommendedSources('youtube_channel', …)` returns popularity-ordered channels balanced across multiple matched archetypes (round-robin) with `is_already_added` correct** — PASS (asserts exact interleaving `[A1, B1, A2, B2, A3]`, cross-archetype dedup, per-archetype popularity order, limit trim, and `is_already_added` true/false for authed + all-false-no-throw for anon).

All assertions encode WHY (Rule 9): balance/interleaving, dedup, the modest-boost ceiling, and the anon degrade path — not just non-empty lists. No assertions skipped (Rule 12).

## Concerns / hand-offs for the orchestrator
1. **`mapToArchetype` consumes a typed `InterestVector`, but no module currently
   PRODUCES an 8-category vector.** `src/lib/onboardingProfile.ts` writes
   `user_interest_profile` rows (interest-slug → weight), not a rolled-up 8-category
   map. **Hand-off:** a later SP (likely SP4 flow wiring, or Phase 5) needs a
   "roll up `user_interest_profile` / `user_entity_follows` into an
   `InterestVector` over the 8 categories" helper before `mapToArchetype` can be
   called with real user data. The `SLUG_TO_CATEGORY` mapping in
   `agents/pipeline/categories.py` (ranking-spec §3a.2) is the reference for which
   interest slugs roll up into which top-level category — but note that mapping is
   to the 8 SCREEN categories, which differ from these 8 PINNED interest categories
   (e.g. screen "Markets" vs pinned `business`). The roll-up author must align to
   the pinned 8 keys here, not the screen categories.

2. **Threshold rarely fires with the full seed (documented property, not a bug).**
   Because `balanced-generalist` is a uniform catch-all, any non-zero diffuse user
   vector scores high cosine to it and is "rescued" above 0.5; concentrated vectors
   hit their themed archetype high. So `is_fallback: true` is, in practice, reached
   only by a zero/empty vector (or a partial archetype read missing the generalist
   row). This is correct and intended, but if product wants a more discriminating
   fallback (e.g. "weak overall signal" detection even when balanced-generalist is
   present), that needs an absolute-magnitude or entropy gate, which is out of scope
   for v1. Flagged for `/cmo` if it matters.

3. **`getRecommendedSources` does NOT read the `archetypes` table itself** — it
   takes already-resolved archetype slugs. The caller (SP4 flow) must read the
   public `archetypes` rows (for `mapToArchetype`) and pass the top-1/top-2 slugs
   in. There is no `archetypes` read helper in `src/lib/sources.ts` today; SP4 (or
   a sources.ts addition) will need one. **No change made to `sources.ts`** per the
   surgical-scope rule — flagged here as the most likely needed addition.

4. **No edit to `src/lib/sources.ts` was required** for SP1. The 5b helpers
   (`listSourcesByArchetype`, `getUserSources`) were sufficient as-is.
