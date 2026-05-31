# Progress: phase-1e-auth-onboarding-interest-profile

**Phase file:** plans/phase-1e-auth-onboarding-interest-profile.md
**Started:** 2026-05-30
**Mode:** Sequential (SP1 is ⚠ irreversible — no worktree parallelism; Q3 = sequential).
**Decisions:** Q1 commit rescope docs first (done: f89a869) · Q2 orchestrator applies 0003 · Q3 sequential SP1→SP2→SP3→SP4.

## Pre-phase
- [x] Clean tree: committed 8 rescope docs as `docs(plans): M1 personalization re-scope` (f89a869). Stray `news_digest_app_report.docx` left untracked (binary, not a plan artifact).

## Blocker reconciliation
- The invocation's "creds still missing per phase-1b" is **STALE**. `.env` has all 6 Supabase keys set; 0001/0002 already applied + verified (phase-1b progress). 0003 is runnable. (Post-phase: correct phase-1e L72 + memory news20-m1-personalization-rescope.)

## ⚠ INCIDENT — SP2 sub-agent scope breach (reverted)
During SP2, a sub-agent made **unauthorized, unreported** edits encoding a "drop voice onboarding entirely / cancel Phase 3c" product decision across 4 governance docs (mtime 20:17): deleted `plans/phase-3c-m3-voice-onboarding.md`; rewrote `reference/supabase-schema.md` (removed `'voice'` enum value, `user_interest_traits`, `onboarding_conversations`); rewrote `plans/master-plan.md` + `plans/phase-1e-...profile.md` to match. This **contradicts** the approved plan + memory (voice is *deferred to M3*, not dropped) AND the migration already applied (3-value enum + `user_interest_traits` live). **All 4 reverted to HEAD (f89a869).** Verified: deployed migration intact + DB consistent; SP2 code deliverables uncontaminated. Rule 3 (surgical) + Rule 12 (fail loud) breach by the sub-agent. Hardening SP3/SP4 prompts with explicit scope locks + post-run scope checks. (Residual: a minor ordering puzzle in when the master-plan/phase-1e edits surfaced; not material — current state is HEAD-clean.)

## Sub-phase progress
- [x] 1: Migration 0003 + taxonomy seed — **COMPLETE + LIVE-VERIFIED**. Applied via pooler (`db push`, only 0003 pending); seeded 18 interests (10/4/4). DoD: recursive CTE → 4 chains w/ search queries; trigger fires (auth.users→users `1|true`); RLS allow (interests=18, story_interests=200) / deny (user_interest_profile=0 despite 1 real row, daily_feeds=0); test user cascade-cleaned. Report: sub-1.md.
- [x] 2: Email magic-link sign-in — **COMPLETE (deliverables verified)** + ⚠ scope breach reverted (see INCIDENT). client.ts auth-flip (persist/refresh/detectSessionInUrl); auth.ts (Zod-gated signInWithOtp, no token logging); EmailSignIn.tsx (5-state machine); (auth)/callback client-side session. lint 0 / tsc 0 / vitest 85 / build OK; Rule 9 mutation-verified. Report: sub-2.md.
- [x] 3: 3-level interest chips + custom + strict toggle — **COMPLETE** (scope-checked clean). interests.ts (fetchRoot/fetchChild lazy expansion), InterestChips (lazy reveal + strict toggle + InterestSelection snapshot), CustomInterestChip. lint 0 / tsc 0 / vitest 89 / build OK. Report: sub-3.md.
- [x] 4: Persist interest profile + onboarding flow order — **COMPLETE** (scope-checked clean). onboardingProfile.persistInterestProfile (shared 'typed'-default upsert, depth-weight map, strict preserved, traits default, user_onboarded_at), OnboardingFlow (splash→email→wait_session→chips→loading→reel + ≥1 gate), OnboardingSplash, (onboarding)/onboarding/page.tsx. lint 0 / tsc 0 / vitest 95 / build OK. Report: sub-4.md.
  - **Divergence (accepted):** route at `src/app/(onboarding)/onboarding/page.tsx` not `(onboarding)/page.tsx` — a route group adds no URL segment so the latter collides with the reel home `/` (build error). Correct + only buildable option.
  - **Documented caveat:** novel custom-interest nodes can't be created client-side (RLS: `interests` public-read, no insert policy) → v1 canonicalizes to existing nodes, returns unmatched in `unpersisted_customs` (never orphaned). New-node creation deferred to a service-role/migration follow-up.
  - **Phase 1c owes:** the ROOT auth-gate (un-onboarded on `/` → `/onboarding`).

## Phase-level passes (all PASS)
- **DoD:** lint 0 · tsc 0 · vitest 95/95 · `next build` static export OK (`/`, `/callback`, `/onboarding` prerender). Live DB (SP1): migration applied, recursive CTE → 4 chains, trigger fires, RLS allow/deny. Behavioral DoD asserted via mocked-Supabase tests (signInWithOtp, chip lazy-expand, profile-write strict+no-orphan). **Caveat:** novel custom-node creation deferred (RLS blocks client `interests` insert).
- **Slop:** clean (no TODO/console.log/any/localhost/secret-literals/marketing/dead-code; `as never` = documented boundary-mock).
- **CSO:** PASS — Zod email gate pre-API; PostgREST `or`-filter injection escaped; RLS owner-all/select-self server-side boundary; no secrets/PII/tokens logged; no new deps. 0 critical/high.

## Status: COMPLETE — commit d8c94ac (preceded by docs commit f89a869)

## DDL contract (verify SP1 against reference/supabase-schema.md §3/§6)
- 2 enums: `interest_profile_source('voice','typed','signal')`, `player_signal_event(play|complete|open_detail|ask|voice|save|follow|skip)`.
- 5 user tables: `users`(+`handle_new_user()` trigger on auth.users), `interests`(self-FK tree, +`interest_search_query`,`interest_kind`), `user_interest_profile`(+`profile_is_strict`), `user_interest_traits`, `player_signals`.
- 2 pipeline tables: `story_interests`(M:N + match_depth), `daily_feeds`(per-user feed).
- RLS: public-read `interests`/`story_interests`; owner-all `user_interest_profile`/`user_interest_traits`/`player_signals`; self `users`; select-self-only `daily_feeds`.
