# Phase 3b: In-news Voice mode (swipe-left)

**Milestone:** M3 — Voice + personalization + follow + onboarding
**Status:** Not started
**Estimated effort:** M

## Goal
Swipe-left from any reel story opens a hands-free Gemini Live conversation about *that* story, grounded on its source set via the M2 RAG brain, refusing cleanly when the source can't support an answer — so a commuter can interrogate the current story without touching the screen.

## Sub-phases

### Sub-phase 1: Mic permission gate + denied fallback
- **Files touched:** `src/components/voice/VoicePermissionGate.tsx`, `ios/App/App/Info.plist`, `src/lib/voice/micPermission.ts`
- **What ships:** A permission gate that declares `NSMicrophoneUsageDescription` and requests mic via Capacitor **before** opening the WSS (prototype `voicePermission`); the `voiceMicDenied` calm fallback ("read & ask by text instead") routing to Detail Q&A. Replaces the prototype's `localStorage("n20-mic")` with the real Capacitor permission result.
- **Definition of done:** Granting permission resolves the gate to "ready" and never opens the socket before grant; denying renders the text-fallback CTA that deep-links to Detail Q&A; `Info.plist` contains the usage string. Verifiable against a mocked Capacitor permissions API.
- **Dependencies:** Phase 3 SP4 (shell the gate sits in front of).

### Sub-phase 2: VoiceMode lateral layer + Live wiring
- **Files touched:** `src/components/voice/VoiceMode.tsx`, `src/components/voice/VoiceConversation.tsx`, `src/components/shell/LayerStack.tsx`
- **What ships:** The left lateral layer (`translateX(-100%)→0`) as a framer-motion `drag="x"` drag-to-follow panel (per `prototype-port-map.md` §3.2), mounting the shared `VoiceOrb`/`Waveform`/`TranscriptLine` (Phase 3 SP4) and `useGeminiLive` (Phase 3 SP3) configured with the **in-news system prompt** (voice `Charon`) scoped to the active `story_id`, plus the greeting nudge. The reel dims/scales behind it off the drag progress.
- **Definition of done:** Swiping left past the offset/velocity threshold opens VoiceMode for the current `story_id`; the layer tracks the finger and the reel applies `scale(0.94) brightness(0.45)`; on open, `useGeminiLive` sends `setup` (responseModalities AUDIO, voice Charon, the story-scoped system instruction) and a `clientContent` greeting nudge; closing returns to the reel without unmounting reel audio.
- **Dependencies:** Phase 3 SP3+SP4; Sub-phase 1.

### Sub-phase 3: RAG grounding round-trip (reuse M2 brain) + refusal contract
- **Files touched:** `agents/worker/main.py` (RAG-tool route, reuse `agents/rag/*`), `agents/chat/prompts.py` (ADAPT in-news voice prompt), function-declaration config in `VoiceMode.tsx`
- **What ships:** The `search_briefing_content(query)` function-declaration wired to the **existing M2 RAG retriever**, scoped to the active story's `story_sources` set (the `{$in}` story-source scope, not the whole briefing); `toolCall` → worker retrieval → `toolResponse` round-trip; system prompt **forbids answering without tool context**. Answers map to `api-contracts.md` `QuestionAnswer` — when `answer_is_grounded === false`, the spoken/visible response is the graceful refusal, never a guess (Decision #5).
- **Definition of done:** Given a mocked RAG endpoint returning source chunks, an on-topic question produces a grounded answer citing those sources; given an empty/off-source retrieval, the response is the refusal state and **no fabricated answer** is emitted; the function round-trip frame shape matches the Gemini Live contract. (Tests the trust contract, not just wiring — Rule 9.)
- **Dependencies:** Sub-phase 2. **Cross-milestone:** requires M2's RAG retriever + verification stage to exist.

### Sub-phase 4: Voice signals + transcript persistence + quota guard
- **Files touched:** `src/lib/signals.ts` (new; `voice` event), `agents/worker/main.py` (persist turns), `src/components/voice/VoiceConversation.tsx`
- **What ships:** On entering Voice mode, write a `player_signals` row `event_type='voice'` (the deep-engagement signal); persist conversation turns to `story_qa` (`qa_source_kind='rag_cached'`); conversation `ended` state on `turnComplete`/`goAway`; reuse the TLDW 600s/day heartbeat + hard-cap quota pattern to bound cost.
- **Definition of done:** Opening Voice mode inserts exactly one `player_signals` `voice` row for the `story_id` (asserted against mocked Supabase); a completed turn persists a `story_qa` row with citations; exceeding the daily quota blocks a new session with a calm message rather than silently failing (Rule 12).
- **Dependencies:** Sub-phase 2/3.

## Phase-level definition of done
From a reel story, swipe-left opens a hands-free conversation scoped to that story; an on-topic spoken question returns a source-grounded answer and an off-source question returns the refusal (never a fabrication); the session logs a `voice` engagement signal and persists its turns; the daily quota is enforced. End-to-end run requires the M2 RAG endpoint live.

## Out of scope
- The RAG retriever/verification implementation itself (built in M2; this phase only wires the Live function-call to it).
- Voice-agent onboarding (3c) — different prompt/tool, built next, but reuses this phase's transport.
- Bridge-clip cross-fades between turns (memory note: likely unneeded for swipe-left UX).

## Open questions
- **M2 dependency:** the grounded-answer DoD depends on M2's RAG endpoint. If M2 isn't shipped, this phase can build/verify against a mock but cannot complete its E2E DoD — confirm sequencing.
- **Quota tuning:** confirm the per-user daily Live budget (default: port TLDW's 600s/day) given commuter session length.

## Self-critique

**Product lens:** PASS — delivers the brief's hands-free interrogation moat (Open Q5 grounding/refusal preserved exactly). Pulls the "differentiator validated last" risk (Open Q6) into a verifiable phase rather than leaving voice untested. No features beyond the brief.
**Engineering lens:** PASS — SP3 explicitly **reuses** M2's RAG rather than re-porting it (Rule 2/reuse-map), and scopes to a single story's source set (not the whole briefing) per the memory delta. DoDs are mock-verifiable in a fresh context; the refusal DoD fails if business logic (grounding gate) regresses.
**Risk lens:** SP3 has a cross-milestone dependency on M2 — flagged. SP4 touches a new `src/lib/signals.ts` shared with Phase 3d (3d extends it) — sequencing, not conflict, since 3b creates it first. No within-phase file collisions. Refusal + quota DoDs both carry tests.
**Irreversible sub-phases:** none (no migrations or public-API changes; `story_qa`/`player_signals` writes are additive).
