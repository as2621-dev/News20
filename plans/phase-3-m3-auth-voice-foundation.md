# Phase 3: Auth + Gemini Live foundation

**Milestone:** M3 — Voice mode + follow
**Status:** Not started
**Estimated effort:** L

## Goal
The shared rails M3 needs before any feature: the user-side Supabase schema (+RLS+seed taxonomy), email-only passwordless sign-in, and a parameterized Gemini Live transport (ephemeral-token mint + raw-WSS hook) with the shared orb/waveform UI — so in-news Voice mode (3b) mounts one brain and one component. *(Voice onboarding (3c) was dropped 2026-05-30 — onboarding is chip-only; Voice mode is the sole consumer of this transport.)*

## Sub-phases

### Sub-phase 1: Migrate user-side schema + RLS + seed interest taxonomy ⚠ irreversible
- **Files touched:** `supabase/migrations/<ts>_m3_user_personalization.sql`, `supabase/seed/interests.sql`
- **What ships:** The M3 user/personalization tables exactly per `reference/supabase-schema.md` §3: `users` (+ `handle_new_user()` trigger on `auth.users` insert), `interests` (self-FK tree), `user_interest_profile`, `user_interest_traits` (deprecated — voice onboarding dropped), `follows`, `player_signals` — each with the §6 RLS policies (content/`interests` public-read; user tables `auth.uid()`-scoped). Seed the 5 depth-0 segment interests + the representative niche-down chains from §3 (Sport→Soccer→Premier League, Markets→Equities→Semiconductors).
- **Definition of done:** Migration applies cleanly **on a DB that already has the M1 content tables** (FKs: `interests.interest_segment_slug`→`segments`, `follows.follow_story_id`/`player_signals.signal_story_id`→`stories`); the recursive CTE in §3 returns the seeded 3-level chains; an anon client can `SELECT` `interests` but **cannot** read another user's `follows`/`player_signals` (RLS test); inserting a row into `auth.users` creates the matching `users` row via trigger.
- **Dependencies:** none within phase. **Cross-milestone:** requires M1's content migration (`segments`, `stories`, `segment_slug` enum) to exist first — see Open questions.

### Sub-phase 2: Email-only passwordless magic-link auth
- **Files touched:** `src/components/onboarding/EmailSignIn.tsx`, `src/lib/supabase/auth.ts`, `src/lib/supabase/client.ts` (reuse/adapt TLDW), `src/app/(auth)/callback/page.tsx`
- **What ships:** The `EmailSignIn` surface (prototype `onbStep` step 2): email field → `supabase.auth.signInWithOtp({ email })` → "check your inbox" state; magic-link callback that establishes the Supabase session and resolves the `users` row. States: empty · invalid-email (blush inline error) · sending · sent. No password, no Sign-in-with-Apple.
- **Definition of done:** Submitting a valid email calls `signInWithOtp` (asserted against a mocked Supabase client) and renders the "sent" state; an invalid email renders the inline error and never calls the API; completing the magic-link callback yields an authenticated session whose `auth.uid()` matches a `users` row.
- **Dependencies:** Sub-phase 1 (`users` table + trigger).

### Sub-phase 3: Gemini Live transport — token mint + parameterized WSS hook
- **Files touched:** `agents/voice/live_token.py` (new) + route in `agents/worker/main.py`, `src/lib/voice/useGeminiLive.ts`, `src/lib/voice/audio.ts`
- **What ships:** (a) A worker endpoint that mints an ephemeral token (`POST v1alpha/auth_tokens`, header `x-goog-api-key`, body `{uses:1, expireTime, newSessionExpireTime}` only — key stays off-device); (b) `useGeminiLive({ systemInstruction, tools, onTranscript, onToolCall })` raw-WebSocket hook implementing the 7 gotchas from memory `news20-gemini-live-tts-contract.md`: `…BidiGenerateContentConstrained` endpoint via `?access_token=`, the `setup` frame + wait for `setupComplete`, greeting nudge, input 16kHz / output 24kHz PCM16 (resample + ring-buffer in `audio.ts`), frame normalization, function round-trip, double-connect guard (React 19 StrictMode). The hook is **parameterized** by `systemInstruction` + `tools` so 3b and 3c configure it differently.
- **Definition of done:** Token endpoint returns 200 with a `.name` starting `auth_tokens/` (mocked Gemini call); the hook, against a mock WS server, sends a well-formed `setup` frame, waits for `setupComplete` before any audio, downsamples mic input to 16kHz, and replies to a `toolCall` with a correctly shaped `{toolResponse:{functionResponses:[{id,name,response}]}}`; mounting twice (StrictMode) opens exactly one socket.
- **Dependencies:** none within phase (needs `GEMINI_API_KEY` in `.env`). Can run in parallel with SP1/SP2 (worktree).

### Sub-phase 4: Shared voice UI — orb + waveform + transcript
- **Files touched:** `src/components/voice/VoiceOrb.tsx`, `src/components/voice/Waveform.tsx`, `src/components/voice/TranscriptLine.tsx`
- **What ships:** The one-component-two-mounts visual template (prototype `.orb`/`.orb-brand`/`.orb.listening`/`.orb.responding` + waveform): `VoiceOrb` with states `idle · listening (pulse-ring) · responding (throb) · paused`, **mic folded into the orb** (tap orb = pause/resume; animating = listening, still = paused — no separate mic button), `Waveform` reacting to audio amplitude, `TranscriptLine` for input/output transcription. Honors `prefers-reduced-motion` (no pulse/throb).
- **Definition of done:** Storybook/headless render of each `VoiceOrb` state matches the prototype class contract; tapping the orb toggles a `paused` callback; reduced-motion render emits no animation classes. Visual smoke against the prototype is acceptable (UI per Rule 9 note).
- **Dependencies:** none within phase. Can run in parallel with SP1–SP3 (worktree).

## Phase-level definition of done
A signed-out user can request a magic link and land authenticated with a `users` row; the M3 user-side schema is live with RLS enforced and the interest taxonomy seeded; a throwaway test page can mint a Live token, open one constrained WSS, exchange `setup`/`setupComplete`, and render the shared `VoiceOrb`/`Waveform` reacting to mic input — i.e. every shared rail in-news Voice mode (3b) depends on is verified in isolation.

## Out of scope
- Any in-news Voice mode behavior (3b) — this phase only builds the reusable transport + UI, not the prompts/tools that drive it. *(Voice onboarding (3c) was dropped 2026-05-30.)*
- `saves` / `play_sessions` tables and the full profile sheet (deferred; `saves`+streak are not in M3's "true when"; `play_sessions` is M4 metric instrumentation).
- RAG retriever/verification (built in M2; reused by 3b).

## Open questions
- **M1/M2 prerequisite:** this migration has hard FKs to M1's `stories`/`segments`. M1 and M2 are not yet planned. Confirm M1 (content schema + reel) and M2 (RAG endpoint) ship before M3 executes, or that at minimum M1's content migration runs first.
- **Token-mint host:** placed in the FastAPI worker (key off-device, RAG already lives there). Confirm vs. a dedicated Vercel serverless function if worker latency at session-start is a concern.

## Self-critique

**Product lens:** PASS — voice is the brief's most novel/expensive feature (Open Q6); this phase front-loads its riskiest plumbing (Live WSS) into M3's first phase so it's de-risked before 3b/3c build on it. No scope creep (saves/profile deferred). Email-only auth matches the brief's commuter-friction goal.
**Engineering lens:** PASS — the WSS hook is parameterized by `systemInstruction`+`tools` so SP4-of-later-phases doesn't cement one use; token mint stays in-stack (worker). SP1 and SP3/SP4 are independent tracks → safe to parallelize. Each DoD is fresh-context-verifiable against mocks.
**Risk lens:** SP1 is an ⚠ irreversible DB migration **and** has a cross-milestone FK dependency on M1 — flagged in Open questions; `/run-phase` must confirm M1 content tables exist before applying. No within-phase file overlaps (4 disjoint file regions). Each DoD carries a test (mocked Supabase/Gemini/WS), not just "compiles".
**Irreversible sub-phases:** Sub-phase 1 (schema migration).
