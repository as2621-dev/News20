# Progress: phase-3b-m3-in-news-voice-mode

**Phase file:** plans/phase-3b-m3-in-news-voice-mode.md
**Started:** 2026-05-31
**Phase-diff baseline commit:** 7cc5239 — ⚠ HEAD advanced to **85fedc4** mid-run (phase-3d `follow-as-ranking-signal` committed between SP1 spawn + return). 3d's files (`src/lib/follows.ts`, `0005_m3_follows.sql`, `Reel.tsx`, `agents/memory/{player_signals,session_processor}.py`) are **disjoint** from 3b's remaining sub-phases — no conflict. 3b commit stages only 3b files explicitly.
**Execution mode:** SEQUENTIAL (SP1→SP2→SP3→SP4 — dependency chain + shared files VoiceMode.tsx/VoiceConversation.tsx; no independent pair).

## Pre-flight (verified 2026-05-31)
- Phase 3 SP3/SP4 **merged** at `7cc5239`: `src/lib/voice/{useGeminiLive.ts,audio.ts}`, `src/components/voice/{VoiceOrb,Waveform,TranscriptLine}.tsx`, `agents/voice/live_token.py` all present.
  - Hook signature: `useGeminiLive({systemInstruction, tools?, onTranscript?, onToolCall?, voiceName="Charon", model?, greetingNudge?}) → {status, isSetupComplete, inputAmplitude, connect(), disconnect()}`; `connect()` must run inside a user gesture.
- M2 grounded-Q&A shipped (2b, `46b88fa`): worker route `POST /api/story/{story_id}/question` → `QuestionAnswer{answer_text, answer_citations[], answer_is_grounded}`; client `src/lib/qa/askQuestion.ts`; server `agents/qa/{corpus,agent,verification,prompts}.py`. **RAG/Pinecone dropped** — SP3 wires to this in-context endpoint, NOT `agents/rag/*` (gone).
- **Plan rewrite (this run):** SP3 + goal/out-of-scope/open-Qs/self-critique re-scoped from the deleted RAG path to the in-context endpoint (doc-only; mirrors 2b/2c rewrites).
- `src/components/shell/LayerStack.tsx` exists (SP2 edits it). `src/lib/signals.ts`, `ios/App/App/Info.plist` mic string, VoiceMode/VoiceConversation/VoicePermissionGate/micPermission.ts all absent → 3b creates them.

## Pre-existing uncommitted (NOT 3b — left untouched, not staged at commit)
- `plans/phase-3d-m3-personalization-follow.md` (M) — belongs to a future 3d commit (phase-3 follow-up note).
- `news_digest_app_report.docx` (untracked).

## Decisions (this run)
- **D-3b.1 (owner GO 2026-05-31):** phase-1c (Capacitor iOS shell) never ran → no `ios/`, no Capacitor. **Build all of 3b on web now.** `micPermission.ts` uses web-standard `navigator.mediaDevices.getUserMedia`/Permissions API (portable to the Capacitor WebView later). **Defer** the iOS `Info.plist` `NSMicrophoneUsageDescription` to phase-1c / a cloud Mac (MacinCloud for interactive Simulator smoke; Codemagic/GitHub Actions for builds → TestFlight at M4). No native platform generated this phase.

## Sub-phase progress
- [x] 1: Mic permission gate + denied fallback — **COMPLETE** (web-std getUserMedia; Info.plist deferred to 1c). tsc 0 · biome 85 · vitest 195/195 (10 new). Files: `src/lib/voice/micPermission.ts`, `src/components/voice/VoicePermissionGate.tsx`, `tests/lib/voice/voicePermissionGate.test.tsx`. Report sub-1.md.
  - **SP2 mount seam (from SP1):** mount `<VoicePermissionGate story_id={activeStory.digest_id} onGranted={…open WSS via useGeminiLive…} onOpenTextFallback={() => openDetail(activeStory)} prefers_reduced_motion={useReducedMotion()}>`. Detail-open seam = `useLayerStack().openDetail(story)` from `src/components/shell/LayerStackContext.tsx` (no router route). Gate is props-in/callbacks-out; the WSS opens ONLY in `onGranted`. Added an `unsupported` state (SSR/old WebView) → same text fallback.
- [x] 2: VoiceMode lateral layer + Live wiring — **COMPLETE**. tsc 0 · biome 88 · vitest 204 (9 new) · build 6/6. Files: `src/components/voice/{VoiceMode,VoiceConversation}.tsx` (new), `src/components/shell/{LayerStack,LayerStackContext}.tsx` (edit — added left Voice layer + `openVoice/closeVoice/openVoiceStory/isVoiceOpen`), `tests/lib/voice/voiceMode.test.tsx`. Report sub-2.md.
  - **SP3 seam (from SP2):** in `VoiceMode.tsx`, pass to `<VoiceConversation>`: `toolsSlot={[askAboutStoryDeclaration]}`, `onToolCallSlot={handler(story.digest_id)}` (memoize), `tool_grounding_clause={…forbid clause…}`. `buildInNewsSystemInstruction(headline, digest_id, clause)` already appends the clause. Reuse `askQuestion(story_id, question_text)` from `src/lib/qa/askQuestion.ts` + `QuestionAnswer` from `src/types/qa.ts`; tool types `GeminiToolDeclaration`/`GeminiToolCall` from `useGeminiLive.ts` (do NOT redefine).
  - **SP4 seam (from SP2):** `player_signals` voice write + quota check at the open-boundary `useEffect([isOpen, story.digest_id])`; `story_qa` persist + `ended` orb state off the hook turn lifecycle (`orbStateForStatus`).
- [x] 3: Grounded answer round-trip (reuse M2 in-context Q&A) + refusal contract — **COMPLETE**. tsc 0 · biome clean · vitest 209/209 (5 new + SP2 test flipped: the intermediate "tools undefined" assertion → "seam wired", Rule 9/12) · build 6/6. Files: `src/lib/voice/storyQaTool.ts` (new — `ask_about_story` decl + forbidding clause + `buildAskAboutStoryHandler`), `src/components/voice/VoiceMode.tsx` (filled seam, memoized handler), `tests/lib/voice/storyQaTool.test.ts` (new), `tests/lib/voice/voiceMode.test.tsx` (updated). `VoiceConversation.tsx` untouched. Report sub-3.md. Live E2E owner-gated (worker undeployed).
- [x] 4: Voice signals + transcript persistence + quota guard — **COMPLETE**. tsc 0 · biome 93 · vitest 220/220 (13 new) · build 6/6. Files: `src/lib/signals.ts` (new — `recordVoiceSignal` `event_type='voice'` + 600s/day localStorage quota), `src/components/voice/VoiceConversation.tsx` (edit — signal once/open, pre-connect quota gate, heartbeat, `ended` text region), `tests/lib/signals.test.ts` + `tests/lib/voice/voiceConversation.test.tsx` (new). **`agents/worker/main.py` NOT touched** — voice Q&A turns already persist via the existing `/question` route (`_write_cached_answer`, `qa_source_kind='rag_cached'`); a second path would be dead code (Rule 2/12). SP4 also fixed a real unhandled-rejection (eager `getSupabaseBrowserClient()` default). Report sub-4.md.

## Phase-level passes (Step 3) — assembled tree
- **DoD (3a):** PASS (mock-verified) — swipe-left opens a story-scoped hands-free conversation (SP2); on-topic → grounded answer, off-source → refusal verbatim, never fabricated (SP3); one `player_signals` `voice` row/open + turns persisted via `/question` + 600s/day quota blocks new sessions (SP4). Live E2E owner-gated on M2 worker deploy (stated, not faked). Combined: tsc 0 · biome 93 · vitest 220/220 · next build OK.
- **Slop scan (3b):** PASS — no TODO/console.log/`any`/localhost/marketing/dead-code; swallows are intentional best-effort telemetry, logged + `// Reason:`-commented. All new src files within size discipline (≤433 LoC).
- **CSO (3c):** PASS — no new server endpoint/auth surface (worker untouched), no new deps, no secrets; input guarded at boundaries (`extractQuestionText`; parameterized `player_signals` insert with `signal_user_id` pinned to `auth.uid()`); logging hygiene clean (logs `question_length` not content; no PII/tokens). Client quota honestly flagged as non-security.

## Status: COMPLETE — committed.

## Follow-ups (NOT in this commit)
- **⚠ Reel-audio competition:** reel narration plays over the Gemini voice on Voice open (reel stays mounted, position preserved — overlap only). Fix is reel-owned: `ReelStory`/`useReelAudio` reads `isVoiceOpen` from `useLayerStack()` and pauses the active story. Out of 3b file scope (3d owns recent `Reel.tsx`).
- **iOS `Info.plist` `NSMicrophoneUsageDescription`** — deferred to phase-1c (no `ios/` platform yet).
- **Live E2E** — gated on deploying the M2 worker (`/api/story/{id}/question` + `/api/voice/live-token`) + CORS/rate-limit (`news20-m2-2b-2c-gated-state`).
- **`src/lib/follows.ts`** has the same eager-default-throw bug SP4 fixed in `signals.ts` (flagged by SP4; 3d-owned, out of scope).
