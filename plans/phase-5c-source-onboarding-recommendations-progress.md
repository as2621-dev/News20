# Progress: phase-5c-source-onboarding-recommendations

**Phase file:** plans/phase-5c-source-onboarding-recommendations.md
**Started:** 2026-06-05
**Mode:** Sequential. **RE-SCOPED 2026-06-05:** user supplies the source-screen UI as HTML/CSS; sub-agents build LOGIC now and port UI later (user decision). Phase splits into a logic-foundation pass (now) + a UI-integration pass (when HTML lands).
**Pre-phase base:** bc420d3

## Palette note (Rule 7/11 conflict, resolved by user: "I will provide this UI")
- Onboarding flow = cream/forest system (#f4f1ea / #1b1a17 / #3a5a40) across OnboardingPicker, FollowChip, FollowSet, SelectionTray.
- reference/design-language.md = dark-editorial (#020617), scoped to "reel + detail views".
- SP2 was built dark (brief over-read the phase file). User will provide HTML/CSS for the source screens → SP2/SP3/SP4 UI re-skinned to that. Do NOT auto-pick a palette.

## Sub-phase progress
- [x] 1: Archetype mapping + recommendation read — COMPLETED (19 tests; full suite 302 pass; report sub-1.md). Hand-offs to SP4: (a) InterestVector roll-up helper from user_interest_profile/user_entity_follows → 8 pinned category keys; (b) archetypes-table read helper in src/lib/sources.ts; getRecommendedSources takes resolved slugs not the table.
- [~] 2: Avatar + selectable card — BUILT (dark, 9 tests, suite 311 pass; report sub-2.md). portraitBg.ts (logic) keep. SourceArtwork/SourceCard = AWAIT UI re-skin to user HTML.
- [x] 3a (logic, NOW): worker source-search endpoint + sourceSearch.ts + X resolver — COMPLETED (36 py + 8 ts tests; suites 313 py / 319 ts; report sub-3a.md). Contract: POST /api/sources/search {query,kind} → 200 {results[],search_ok}; searchSources({query,kind}) annotates is_already_added client-side. Hand-offs to SP4a: (1) upsert-then-follow helper in sources.ts for non-curated added sources (search results carry external_id not source_id); (2) pending x_account marker (platform_metadata.is_pending) for 5d. X API = open Q#3 (seam ready, none wired).
- [ ] 3b (UI, LATER): SourceSearchModal + FollowButton from user HTML — DEFERRED (await UI; consumes sourceSearch.ts contract above)
- [x] 4a (logic, NOW): interestVector roll-up + getArchetypes + upsertUserAddedSource (+pending x_account marker) + onboarding-complete markers — COMPLETED (44 tests; full suite 339 pass; report sub-4a.md). UI contract: getArchetypes(), rollUpInterestVector(), upsertUserAddedSource(), markSourceOnboardingComplete()/isSourceOnboardingComplete(). Flags: complete-marker=localStorage (mirrors signals.ts, no migration in scope); NEW slug→pinned-key map (report §4); `politics` pinned key unreachable = SEED gap for /cmo.
- [ ] 4b (UI, LATER): SourceRecScreen + flow wiring (onboarding/page.tsx, OnboardingFlow.tsx) — DEFERRED (await UI)

## Phase-end gates — LOGIC FOUNDATION (part 1 of 2)
- Combined-tree validation: TS tsc clean + 36 files/339 tests; Python 313 tests; ruff clean. PASS.
- Slop scan: PASS (no TODO/FIXME/console.log/any/hardcoded secrets; boundary excepts log+convert, not swallow).
- CSO lite: PASS (worker input Pydantic-validated; youtube_api_key from Settings, never logged; httpx timeouts; Supabase writes ride existing 5b RLS; no new TS deps).
- ⚠ Scope-violation caught + reverted: SP3a edited plans/phase-5d-source-ingestion.md (out of scope) baking an unverified "probe confirmed" X-API/Grok + Gemini decision into a future plan → REVERTED. X API stays open Q3 for the user.
- **Phase-level DoD: PARTIAL** — logic PASS; the user-facing flow (3 screens, picker→sources→reel, skippable) is DEFERRED to the UI pass (user supplies HTML/CSS).

## UI pass (part 2) — IN PROGRESS (user supplied design: Blip "Source Swipe")
**Design supersedes the planned grid+search UI.** User handoff bundle = a dark "Blip" flow; the locked source-onboarding direction is a **Tinder-style swipe deck** (`Source Swipe - Final.html` / `blip-sources.js`), confirmed in chat transcripts. Scope = **source-swipe stage only** (picker/build-30/reel are other phases). Built by fresh sub-agent.
- Design source files (extracted): `/tmp/blip_design/blip/project/News20 Prototype/{blip-sources.js, blip-flow.css, Blip Flow.html}` (tarball `/tmp/blip_design_root.out`).
- Palette: dark `blip-flow.css` (`--bg:#020617`, `--accent:#EF4444`, `--hi:#FACC15`; Inter/Playfair/JetBrains Mono). SUPERSEDES the cream onboarding palette for this screen.
- 4 passes: YouTube → Podcasts → X → People (personalities). Curtain intro (~6s, skippable) → swipe deck → per-set auto-advance handoff (~1.7s) → final "You're all set" → onDone → reel.
- Wires to logic: rollUpInterestVector→mapToArchetype→getArchetypes→getRecommendedSources (cards); archetype score→% match; followSource/upsertUserAddedSource (swipe-right persist + undo); markSourceOnboardingComplete→route reel.
- DROPPED from onboarding: in-flow search-add modal (no search in the swipe design). sourceSearch.ts + worker endpoint retained for a later 5e "add a source" surface (not wasted).
- [x] UI-A: SourceSwipe deck + card + curtain + styles + data hook — SHIPPED (8 tests; tsc clean). Files: src/components/sources/{SourceSwipe,SourceSwipeCard,ProfileCurtain,SignalOrb,sourceSwipeGlyphs}.tsx, src/lib/sourceSwipeData.ts, globals.css swipe/orb styles, 2 tests. Wires rollUpInterestVector→mapToArchetype→getArchetypes→getRecommendedSources; archetype score→%match (60–99); optimistic followSource + undo-unfollow; portraitBg fallback; markSourceOnboardingComplete→reel.
- [ ] UI-B: flow wiring (OnboardingFlow.tsx) — **DEFERRED / ENTANGLED.** ⚠ A CONCURRENT process (separate session) ran phase-5d ingestion + a topic-tree/blip redesign in this same tree. OnboardingFlow.tsx is co-edited: foreign OnboardingPicker→TopicTree swap + my sources-step wiring share hunks, and it imports the still-UNTRACKED TopicTree. My wiring (sources step + handleSourcesDone + isSourceOnboardingComplete gate) is correct and sits in the working tree, but is NOT independently committable — it must land WITH the topic-tree commit. Not committed by me to avoid dragging in / breaking foreign work.

## CONCURRENCY INCIDENT (2026-06-06)
During the UI sub-agent run, other agent sessions wrote phase-5d (agents/ingestion/*, trigger/*, requirements.txt) and a topic-tree/blip redesign (TopicTree.tsx, src/components/blip/, src/lib/treeSelection.ts, src/styles/, archived the cream picker) into THIS working tree. HEAD stayed 614aa6d (no foreign commit). I committed ONLY the self-contained phase-5c UI; all foreign work + the entangled OnboardingFlow.tsx left uncommitted for their owners. Hand-off: whoever commits the topic-tree work should include OnboardingFlow.tsx (it carries my sources wiring).

## Open hand-off (personalities)
The "People" pass reads content_sources rows of kind 'personality' + followSource (uniform with the 3 axes), NOT the separate personalities/user_personalities tables. If the seed only fills personalities (not content_sources personality rows), the People deck renders empty (graceful). Decide: seed personality rows into content_sources, or add a personalities-specific recommend+follow path.
