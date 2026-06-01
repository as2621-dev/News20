# Phase 3b: In-news Voice mode (swipe-left)

**Milestone:** M3 — Voice mode + follow
**Status:** Not started
**Estimated effort:** M

## Goal
Swipe-left from any reel story opens a hands-free Gemini Live conversation about *that* story, grounded on its source set via M2's in-context grounded-Q&A endpoint (`POST /api/story/{story_id}/question`), refusing cleanly when the source can't support an answer — so a commuter can interrogate the current story without touching the screen.

## Sub-phases

### Sub-phase 1: Mic permission gate + denied fallback
- **Files touched:** `src/components/voice/VoicePermissionGate.tsx`, `src/lib/voice/micPermission.ts`. *(iOS `Info.plist` `NSMicrophoneUsageDescription` deferred to phase-1c — the native platform isn't generated yet; see Deferred below.)*
- **What ships:** A permission gate that requests mic via the **web-standard** `navigator.mediaDevices.getUserMedia({audio:true})` / Permissions API **before** opening the WSS (prototype `voicePermission`) — portable to the Capacitor iOS WebView once 1c adds the platform; the `voiceMicDenied` calm fallback ("read & ask by text instead") routing to Detail Q&A. Replaces the prototype's `localStorage("n20-mic")` with the real permission result.
- **Definition of done:** Granting permission resolves the gate to "ready" and never opens the socket before grant; denying renders the text-fallback CTA that deep-links to Detail Q&A. Verifiable against a mocked `getUserMedia`/permissions API.
- **Dependencies:** Phase 3 SP4 (shared voice UI, merged).
- **Deferred to phase-1c (cloud-Mac/M4):** the iOS `Info.plist` `NSMicrophoneUsageDescription` string — needed for `getUserMedia` inside the native WebView, but the `ios/` project doesn't exist yet (1c never ran). Logged as a follow-up; no native platform generated in this phase.

### Sub-phase 2: VoiceMode lateral layer + Live wiring
- **Files touched:** `src/components/voice/VoiceMode.tsx`, `src/components/voice/VoiceConversation.tsx`, `src/components/shell/LayerStack.tsx`
- **What ships:** The left lateral layer (`translateX(-100%)→0`) as a framer-motion `drag="x"` drag-to-follow panel (per `prototype-port-map.md` §3.2), mounting the shared `VoiceOrb`/`Waveform`/`TranscriptLine` (Phase 3 SP4) and `useGeminiLive` (Phase 3 SP3) configured with the **in-news system prompt** (voice `Charon`) scoped to the active `story_id`, plus the greeting nudge. The reel dims/scales behind it off the drag progress.
- **Definition of done:** Swiping left past the offset/velocity threshold opens VoiceMode for the current `story_id`; the layer tracks the finger and the reel applies `scale(0.94) brightness(0.45)`; on open, `useGeminiLive` sends `setup` (responseModalities AUDIO, voice Charon, the story-scoped system instruction) and a `clientContent` greeting nudge; closing returns to the reel without unmounting reel audio.
- **Dependencies:** Phase 3 SP3+SP4; Sub-phase 1.

### Sub-phase 3: Grounded answer round-trip (reuse M2 in-context Q&A) + refusal contract
- **Files touched:** `src/lib/voice/storyQaTool.ts` (new — `ask_about_story` function-declaration + `toolCall` handler), `src/components/voice/VoiceMode.tsx` (wire `tools` + the tool-forcing system-instruction clause into `useGeminiLive`). **No worker change** — reuses the shipped `POST /api/story/{story_id}/question` endpoint (2b, `46b88fa`) and `src/lib/qa/askQuestion.ts`.
- **What ships:** An `ask_about_story(question_text)` Gemini Live function-declaration (replaces the obsolete `search_briefing_content`) whose `toolCall` handler reuses M2's shipped **in-context** grounded-Q&A path — `src/lib/qa/askQuestion.ts` → `POST /api/story/{story_id}/question` (the per-story corpus loaded into LLM context + server-side `verification`, **not** a vector retriever; Pinecone/RAG was dropped 2026-05-31), scoped to the active `story_id`; `toolCall` → grounded-Q&A round-trip → `toolResponse`. The Live system instruction **forbids answering without calling the tool**. The handler returns the shipped `QuestionAnswer` (`reference/api-contracts.md`) — when `answer_is_grounded === false`, the spoken/visible response is the graceful refusal, never a guess (Decision #5).
- **Definition of done:** Given a mocked `askQuestion`/`/api/story/{story_id}/question` returning a grounded `QuestionAnswer`, an on-topic question produces a grounded spoken answer citing those sources; given `answer_is_grounded === false` (off-source), the response is the refusal state and **no fabricated answer** is emitted; the `toolResponse` frame shape matches the Gemini Live contract (`news20-gemini-live-tts-contract`). (Tests the trust contract, not just wiring — Rule 9.)
- **Dependencies:** Sub-phase 2. **Cross-milestone:** reuses M2's shipped grounded-Q&A endpoint (`agents/qa/{corpus,agent,verification}.py` + worker `/api/story/{story_id}/question`); per `news20-m2-2b-2c-gated-state` the worker is currently **local-only/undeployed** → build + verify against a mock, live E2E gated on worker deploy.

### Sub-phase 4: Voice signals + transcript persistence + quota guard
- **Files touched:** `src/lib/signals.ts` (new; `voice` event), `agents/worker/main.py` (persist turns), `src/components/voice/VoiceConversation.tsx`
- **What ships:** On entering Voice mode, write a `player_signals` row `event_type='voice'` (the deep-engagement signal); persist conversation turns to `story_qa` (`qa_source_kind='rag_cached'`); conversation `ended` state on `turnComplete`/`goAway`; reuse the TLDW 600s/day heartbeat + hard-cap quota pattern to bound cost.
- **Definition of done:** Opening Voice mode inserts exactly one `player_signals` `voice` row for the `story_id` (asserted against mocked Supabase); a completed turn persists a `story_qa` row with citations; exceeding the daily quota blocks a new session with a calm message rather than silently failing (Rule 12).
- **Dependencies:** Sub-phase 2/3.

## Phase-level definition of done
From a reel story, swipe-left opens a hands-free conversation scoped to that story; an on-topic spoken question returns a source-grounded answer and an off-source question returns the refusal (never a fabrication); the session logs a `voice` engagement signal and persists its turns; the daily quota is enforced. End-to-end run requires the M2 grounded-Q&A endpoint (`/api/story/{story_id}/question`) deployed — currently local-only per `news20-m2-2b-2c-gated-state`, so until deploy SP3/SP4 verify against mocks and the live E2E is owner-gated.

## Out of scope
- The grounded-Q&A retriever/verification itself — the in-context corpus loader + verifier built in M2 under `agents/qa/*` (Pinecone/RAG dropped 2026-05-31); this phase only wires the Live function-call to the existing `/api/story/{story_id}/question` endpoint.
- Voice-agent onboarding (3c) — **dropped 2026-05-30** (onboarding is chip-only, `plans/phase-1e-auth-onboarding-interest-profile.md`); this phase's transport is now used only by Voice mode.
- Bridge-clip cross-fades between turns (memory note: likely unneeded for swipe-left UX).

## Open questions
- **M2 dependency:** the grounded-answer DoD depends on M2's grounded-Q&A endpoint (`/api/story/{story_id}/question`). It is shipped as code (2b, commit `46b88fa`) but **not yet deployed** (`news20-m2-2b-2c-gated-state`: worker local-only, CORS/rate-limit pending) — this phase builds/verifies against a mock; the live E2E DoD is gated on worker deploy.
- **Quota tuning:** confirm the per-user daily Live budget (default: port TLDW's 600s/day) given commuter session length.

## Self-critique

**Product lens:** PASS — delivers the brief's hands-free interrogation moat (Open Q5 grounding/refusal preserved exactly). Pulls the "differentiator validated last" risk (Open Q6) into a verifiable phase rather than leaving voice untested. No features beyond the brief.
**Engineering lens:** PASS — SP3 explicitly **reuses** M2's shipped in-context grounded-Q&A endpoint rather than re-porting it (Rule 2/reuse-map), scoped to a single story (the per-story corpus the endpoint already loads) per the memory delta. DoDs are mock-verifiable in a fresh context; the refusal DoD fails if business logic (grounding gate) regresses.
**Risk lens:** SP3 has a cross-milestone dependency on M2 — flagged. SP4 touches a new `src/lib/signals.ts` shared with Phase 3d (3d extends it) — sequencing, not conflict, since 3b creates it first. No within-phase file collisions. Refusal + quota DoDs both carry tests.
**Irreversible sub-phases:** none (no migrations or public-API changes; `story_qa`/`player_signals` writes are additive).
