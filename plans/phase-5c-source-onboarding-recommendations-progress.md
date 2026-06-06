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

## UI pass (part 2) — TODO when user provides HTML/CSS
- Re-skin SP2 SourceArtwork/SourceCard to the supplied HTML (keep onError fallback, kind→shape, aria-pressed).
- Build SP3b SourceSearchModal + FollowButton (300ms debounce, optimistic follow + toast rollback, Add/Adding/Added) against sourceSearch.ts contract; render search_ok===false as "unavailable".
- Build SP4b SourceRecScreen + wire OnboardingFlow/onboarding/page.tsx: picker→YouTube→X/personalities→podcasts→reel, each skippable; returning user skips via isSourceOnboardingComplete().
- Then final phase-level DoD + commit (part 2).
