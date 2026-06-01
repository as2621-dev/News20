# Progress: phase-3-m3-auth-voice-foundation

**Phase file:** plans/phase-3-m3-auth-voice-foundation.md
**Started:** 2026-05-31
**Phase-diff baseline commit:** 9566b38
**Execution mode:** PARALLEL via worktrees (SP3 ∥ SP4 — disjoint file sets, neither irreversible).

## Scope reduction (verified against repo 2026-05-31)
Phase 3 was written before M1's personalization pull-forward. Two of its four sub-phases already shipped:
- **SP1 (user schema + RLS + seed)** — ✅ DONE in `0003_personalization_schema.sql` (phase-1e SP1): `users`+`handle_new_user()` trigger, `interests`, `user_interest_profile`, `user_interest_traits`, `player_signals`, RLS, + `seed/interests.sql`. Only `follows` is missing — owned by phase-3d per its re-scope note (CONFLICT flagged: phase-3-SP1 also lists `follows`; 3d wins as more recent).
- **SP2 (email magic-link auth)** — ✅ DONE in phase-1e SP2: `src/lib/supabase/auth.ts` (`signInWithOtp` + invalid-email guard), `src/components/onboarding/EmailSignIn.tsx`, `src/app/(auth)/callback/page.tsx`.

**Only SP3 + SP4 are executed in this run.**

## Worktrees
- Sub-phase 3: ../News20-sub-3
- Sub-phase 4: ../News20-sub-4

## Sub-phase progress
- [x] 1: Migrate user-side schema + RLS + seed taxonomy — DONE-IN-M1 (0003 / phase-1e SP1). Not executed here.
- [x] 2: Email-only passwordless magic-link auth — DONE-IN-M1 (phase-1e SP2). Not executed here.
- [x] 3: Gemini Live transport — token mint + parameterized WSS hook — **READY-TO-MERGE** (../News20-sub-3). SUCCESS + DoD PASS (5/5). Files: agents/voice/live_token.py (new), agents/worker/main.py (additive: 1 import + POST /api/voice/live-token route), src/lib/voice/{audio,useGeminiLive}.ts(x) (new), tests/agents/voice/test_live_token.py, tests/lib/voice/{audio.test.ts,useGeminiLive.test.tsx}, report sub-3.md. Validation: pytest voice 9 / worker 5 / agents 226, vitest voice 18 / full 154, tsc 0, ruff+biome clean. Hook signature for 3b: `useGeminiLive({systemInstruction, tools?, onTranscript?, onToolCall?, voiceName="Charon", model?, greetingNudge?}) → {status, isSetupComplete, inputAmplitude, connect(), disconnect()}`; `connect()` must run inside a user gesture.
- [x] 4: Shared voice UI — orb + waveform + transcript — **READY-TO-MERGE** (../News20-sub-4). SUCCESS + DoD PASS (3/3). Files (all new): src/components/voice/{VoiceOrb,Waveform,TranscriptLine}.tsx, tests/lib/voice/voiceOrb.test.tsx, report sub-4.md. Validation: vitest 24/24 (full 160/160), tsc 0 (src/tests), biome clean. Used double-quotes per repo biome.json (Rule 11). prefers_reduced_motion is an explicit prop. **Follow-up at phase assembly:** add `.orb`/`.orb-ring`/`.wave-bar` base rules + `pulse-ring`/`orb-throb` keyframes to src/app/globals.css (out of SP4 file scope; components render standalone, animation needs the keyframes).

## Phase-level passes (Step 3) — all on the MERGED tree
- **Merged-tree validation:** PASS — Python `tests/agents` 226 · `tsc --noEmit` exit 0 · biome clean (8 voice files) · vitest 178/178 (20 files = base 136 + SP3 18 + SP4 24, no regression).
- **DoD (3a):** PASS (isolated rails). SP1/SP2 done-in-M1; SP3 token-mint + setup→setupComplete + single-socket and SP4 orb states + tap + reduced-motion all verified by the 42 voice tests. Full live-browser e2e (real mic + Gemini WSS + in-app orb animation) is NOT automatable here and depends on the deferred globals.css keyframes + 3b integration — stated, not faked.
- **Slop scan (3b):** PASS — no TODO/console.log/`any`/empty-catch/marketing/dead-code. Soft note: `useGeminiLive.ts` 505 LoC (5 over soft-500, one cohesive 6-gotcha hook, under hard 1000 — accepted, per phase-2c precedent).
- **CSO (3c):** PASS — no critical/high; secret hygiene exemplary (key never logged/returned, client never holds it). One MEDIUM logged → `.agents/cso-findings/phase-3-m3-auth-voice-foundation.md` (unauthenticated mint route, matches existing worker convention, gated by 3b SP4 quota guard).

## Follow-ups (not in this commit)
- **globals.css keyframes:** add base `.orb`/`.orb-ring`/`.wave-bar` + `pulse-ring`/`orb-throb` to `src/app/globals.css` so the orb animates in-app (out of SP4 file scope; components render standalone). Owner or 3b.
- **phase-3d concurrent edit:** `plans/phase-3d-m3-personalization-follow.md` was rewritten mid-run (Follow-as-ranking-signal). Out of phase-3 scope → left UNSTAGED. Belongs to a 3d commit.

## Status: COMPLETE (SP3 + SP4) — committed. SP1/SP2 done-in-M1.

## Gates / flags
- `GEMINI_API_KEY` is present in `.env` with a real value (confirmed 2026-05-31, value not printed). SP3 unit tests mock Gemini; the *live* token-mint smoke is NOT gated — the key exists. (Earlier "missing key" flag was a false alarm from a broken multi-file grep.)
- **DECISION (owner: "take your call"):** `follows` table belongs to **phase-3d** (its re-scope header explicitly claims it). phase-3-SP1's stale mention is superseded; SP1 is done-in-M1 regardless. Not built in this run.
