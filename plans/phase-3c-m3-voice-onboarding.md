# Phase 3c: Voice-agent interest onboarding

**Milestone:** M3 — Voice + personalization + follow + onboarding
**Status:** Not started
**Estimated effort:** L

## Goal
A new user is interviewed by a Gemini Live agent (the same brain/orb as Voice mode) that adapts to what they say, niches down through the hierarchical interest taxonomy (Sport → which team?), and extracts a weighted interest profile via function-calling — replacing the prototype's canned `VP_TURNS` script.

## Sub-phases

### Sub-phase 1: Interest chips + live profile panel (hierarchical)
- **Files touched:** `src/components/onboarding/InterestChips.tsx`, `src/components/onboarding/InterestProfilePanel.tsx`, `src/lib/interests.ts`
- **What ships:** `InterestChips` rendering the `interests` tree (Phase 3 seed) as tappable chips with **lazy child expansion** (tap a depth-0 chip → load its children via the recursive read) — a scaffold, not a checkbox grid; `InterestProfilePanel` with the lighting-up tags (`.interest-tag → .in → .lit`) and the header "YOUR PROFILE · TOP WORLD STORIES ALWAYS IN" (the guaranteed-world-tier promise). `src/lib/interests.ts` reads the tree (recursive CTE / nested fetch).
- **Definition of done:** Chips render the seeded depth-0 segments; tapping one reveals its depth-1 children (asserted against mocked `interests` data); a tag transitions `in→lit` when marked detected; the world-tier header is present. Tapping a chip emits the same "interest detected" event a spoken mention would (shared handler).
- **Dependencies:** Phase 3 SP1 (seeded `interests`).

### Sub-phase 2: VoiceOnboarding screen + adaptive Live conversation
- **Files touched:** `src/components/onboarding/VoiceOnboarding.tsx`, `agents/onboarding/prompts.py` (new onboarding system prompt), function-declaration config
- **What ships:** Mounts the shared `VoiceOrb`/`Waveform` (Phase 3 SP4) + `useGeminiLive` (Phase 3 SP3) with an **onboarding system prompt** and the `record_interest(category_path, weight)` + `record_trait(trait, value)` function-declarations; a genuinely two-way ~30–60s interview that issues niche-down follow-ups when a top-level interest is detected (walks the `interests` hierarchy). Mic folded into the orb (tap = pause/resume). Not 3 fixed turns.
- **Definition of done:** On mount, `useGeminiLive` sends the onboarding `setup` + greeting nudge; a simulated transcript mentioning "Sport" triggers a model `record_interest` call and a niche-down follow-up question (verified against a scripted mock WS); the conversation is bounded to ~30–60s (a max-duration guard ends it gracefully).
- **Dependencies:** Phase 3 SP3+SP4; Sub-phase 1.

### Sub-phase 3: Profile extraction + persistence
- **Files touched:** `src/lib/onboardingProfile.ts`, `src/components/onboarding/VoiceOnboarding.tsx` (handlers)
- **What ships:** The function-call handlers: `record_interest(category_path, weight)` resolves `category_path` → the matching `interests` row(s) and upserts `user_interest_profile` (`profile_source='voice'`); `record_trait` writes `user_interest_traits` (`prefers_world_first`, `context_vs_facts_ratio`); the full transcript + `extracted_profile` are saved to `onboarding_conversations`. Chip taps (SP1) feed the **same** upsert path.
- **Definition of done:** A `record_interest('sport.soccer.epl', 0.8)` call inserts/updates the correct `user_interest_profile` row scoped to `auth.uid()` (asserted against mocked Supabase); a `record_trait('world_first', true)` writes `user_interest_traits`; the conversation transcript persists to `onboarding_conversations`; an unknown `category_path` is rejected/logged with `fix_suggestion`, never written as a dangling row (Rule 12).
- **Dependencies:** Sub-phase 2; Phase 3 SP1.

### Sub-phase 4: Onboarding flow order + typed fallback
- **Files touched:** `src/components/onboarding/OnboardingFlow.tsx`, `src/components/onboarding/OnboardingSplash.tsx`, `src/app/(onboarding)/page.tsx`
- **What ships:** The sequence splash (1) → **voice onboarding** (2) → email sign-in (Phase 3 SP2) → loading skeleton → reel (per `prototype-port-map.md` §5); a **typed fallback** (no mic / denied / "type instead") that drives the identical profile via tapped chips, writing through the SP3 path; `users.user_onboarded_at` set on completion.
- **Definition of done:** Completing voice onboarding advances to email sign-in then the reel; choosing the typed fallback produces an equivalent `user_interest_profile` without ever opening the WSS; `user_onboarded_at` is set; a returning onboarded user skips straight to the reel.
- **Dependencies:** Sub-phases 1–3; Phase 3 SP2.

## Phase-level definition of done
A first-run user is interviewed by voice (or types as a fallback), the agent niches down through the interest hierarchy, and a weighted `user_interest_profile` + traits + transcript are persisted scoped to their `auth.uid()`; the flow ends signed-in at the reel with `user_onboarded_at` set. The same Gemini Live transport and orb as Voice mode are reused (no second brain).

## Out of scope
- The feed actually *adapting* to the profile (that's Phase 3d ranking — this phase only writes the profile).
- The prototype's canned `VP_TURNS`/`extract()` — explicitly superseded (`prototype-port-map.md` §5).
- Re-onboarding / profile editing UI beyond replay (defer).

## Open questions
- **Function-calling reliability:** if the model under-extracts during a 30–60s window, do chip taps alone produce a usable profile? (Typed fallback covers the floor; confirm the minimum viable profile size for ranking in 3d.)
- **Taxonomy depth at launch:** confirm how deep the seeded niche-down chains go beyond the two example chains in `supabase-schema.md` §3 (affects how rich the niche-down feels).

## Self-critique

**Product lens:** PASS — delivers the brief's onboarding + simple personalization input, and the "TOP WORLD STORIES ALWAYS IN" world-tier promise resolves the brief's "world vs my field" tension (Open Q3) at the profile layer. Builds the user's explicit superseding target (§5), not the prototype shortcut. No creep.
**Engineering lens:** PASS — reuses the Phase 3 transport/orb (one brain, two mounts) per Rule 2; chip-tap and voice extraction share one upsert path (SP1↔SP3) so they aren't secretly two implementations. DoDs are mock-verifiable in fresh context. SP4 cements flow order last, after the pieces exist — correct ordering.
**Risk lens:** No DB migrations (additive writes only). Within-phase file overlap: `VoiceOnboarding.tsx` is touched by SP2 and SP3 — handled by ordering (SP3 depends on SP2), not parallel edits; flag for `/run-phase` to run SP2→SP3 serially. Each DoD carries a test; the dangling-row guard (SP3) encodes *why* (no orphan interest weights).
**Irreversible sub-phases:** none.
