# Voice latency fix — hybrid corpus-in-context + tool-as-web-fallback

**Problem.** In live voice mode, every factual question is silent-blocked while a full
server pipeline runs. Today the Live model is a *mouthpiece*: it is forbidden to answer
and MUST call `ask_about_story` for every question → HTTPS hop to the Railway worker →
`answer_question()` runs **2 serial `gemini-2.5-flash` calls** (grounded answer +
`verify_answer_against_corpus`), or **3** on a corpus miss (+ web-search call) → answer
returns → Live model TTS-es it. The native-audio model's low latency is wasted; the
floor is "speak → wait for a remote multi-LLM pipeline → hear it," with dead silence
throughout.

**Goal.** Cut perceived question→answer latency while keeping (a) grounded answers and
(b) the web-search fallback.

**Strategy (the hybrid).** Inject the story's whole grounding corpus (~6k tokens, well
under any context window) into the Live session's `systemInstruction` at setup. The
native-audio model then answers **corpus-answerable questions directly** — no Railway
hop, no extra LLM call, grounded *by construction* because it only has the corpus in
context. Re-scope `ask_about_story` to fire **only when the corpus can't answer**, which
routes to the existing web-search fallback. The common case collapses to sub-second; only
genuine out-of-story questions pay a round-trip — and those need the web call anyway.

**The one accepted trade-off.** The corpus path loses the server-side
`verify_answer_against_corpus` re-check (the text-Q&A path's *second* guardrail). For voice
you can't unspeak an answer, so we rely on constrained-context + strict prompt + low temp.
Mitigations in SP4. This is a conscious downgrade from today's two-guardrail text path.

---

## Success criteria
1. A question answerable from the story is spoken back **without** any `ask_about_story`
   tool call (verify in logs: no `ask_about_story_tool_called`). Latency: native-audio
   round-trip only (~sub-second to first audio).
2. A question NOT in the corpus triggers `ask_about_story` → web-search answer with web
   citations; an unrelated question still gets the off-topic pushback.
3. Web-path latency is reduced (server skips the corpus answer+verify calls) and **masked**
   by a short spoken acknowledgement before the tool call.
4. If the corpus can't be fetched, voice degrades gracefully to today's tool-only behavior.
5. Existing typed Q&A (Detail view, `askQuestion`) is unchanged.
6. A feature flag toggles old vs new behavior for A/B on latency + grounding quality.

---

## SP1 — Server: expose the corpus + a web-only answer path

**Files:** `agents/worker/main.py`, `agents/qa/agent.py`, `agents/qa/models.py`

1. **New endpoint `GET /api/story/{story_id}/corpus`** in `main.py`.
   - Reuse `get_or_load_corpus(story_id, supabase_client, loader=load_grounding_corpus)`
     (already cached per story — corpus assembly stays in ONE place, server-side).
   - Return `{ "context_block": corpus.render_context_block(), "approx_token_count": corpus.approx_token_count }`.
   - Graceful (Rule 12 boundary, mirror the question endpoint): on `GroundingCorpusError`
     or any failure, return HTTP 200 with `{ "context_block": "", "approx_token_count": 0 }`
     so the client falls back to tool-only voice. Add to the rate-limit middleware allowlist
     if relevant.
   - Confirm CORS already covers it via the existing `QA_API_ALLOWED_ORIGINS` middleware.

2. **Web-only mode on the answer path.** Add `web_only: bool = False` to `QuestionRequest`
   (`agents/qa/models.py`). In `post_story_question` (`main.py:319`), when `web_only`:
   skip the SP4 answer cache read/write and call `_answer_from_web(...)` directly instead
   of `answer_question(...)`. This is the path the re-scoped tool uses — the corpus already
   failed *at the model*, so re-running the corpus answer+verify server-side is wasted work.
   Web grounding (relatedness gate, web citations, off-topic pushback) is unchanged.
   - Prefer the flag over a new endpoint: reuses the route, the corpus load (needed for the
     relatedness context block), and the graceful-200 contract.

3. **Tests** (`tests/agents/...`): corpus endpoint happy / missing-story / oversized-corpus
   → empty block; `web_only=True` skips corpus answer+verify and calls `_answer_from_web`
   (mock `LLMClient`); `web_only=False` path unchanged.

## SP2 — Client: fetch corpus + new corpus-aware system instruction

**Files:** `src/lib/voice/fetchStoryCorpus.ts` (new), `src/lib/voice/storyVoicePrompts.ts`

1. **`fetchStoryCorpus.ts`** — thin HTTP client mirroring `askQuestion.ts` (same
   `getQaApiBaseUrl()`, same graceful posture). `GET ${base}/api/story/{id}/corpus` →
   returns `context_block` string; any failure → `""` (caller degrades to tool-only).

2. **`storyVoicePrompts.ts`** — new pure builder
   `buildInNewsSystemInstructionWithCorpus(story_headline, story_id, corpus_context_block, tool_grounding_clause)`:
   - Keeps the Jordan persona + scope lines.
   - Embeds the corpus as a labeled `STORY CONTEXT` block (the `[passage_id] text` lines
     from `render_context_block()`).
   - Instruction: *answer ONLY from the STORY CONTEXT below; keep it short and spoken; if
     the answer is not in the context, say a brief "let me check that" and call
     `ask_about_story`; never use outside knowledge; never read citations aloud.*
   - When `corpus_context_block` is empty, fall back to the existing
     `buildInNewsSystemInstruction` (tool-only) — single graceful-degradation seam.
   - Keep it PURE/exported so it stays unit-assertable without a socket (Rule 9).

3. **Tests:** builder embeds the corpus + the corpus-first/tool-on-miss clause; empty
   corpus falls back to the tool-only instruction; `fetchStoryCorpus` happy/non-200/throw.

## SP3 — Re-scope the tool to web-only + wire it up

**Files:** `src/lib/voice/storyQaTool.ts`, `src/components/blip/reel/AskSheetVoice.tsx`

1. **`storyQaTool.ts`:**
   - Rewrite `STORY_QA_TOOL_GROUNDING_CLAUSE` → *"You have the full story in your STORY
     CONTEXT. Answer from it directly. Call `ask_about_story` ONLY when the answer is not in
     that context (e.g. a related fact the story doesn't state); before calling it, say one
     short filler line like 'let me check that.' Speak the tool's `answer_text` verbatim;
     when `answer_is_grounded` is false, deliver it as a brief refusal/pushback and add
     nothing."*
   - Update `askAboutStoryDeclaration.description` to "for questions the STORY CONTEXT does
     not cover — fetches a web-searched answer with citations."
   - `buildAskAboutStoryHandler` calls `askQuestion(story_id, question_text, turns, fetch)`
     with the new `web_only: true` flag (extend `askQuestion` signature/body in
     `src/lib/qa/askQuestion.ts` to pass it through; default `false` keeps Detail Q&A intact).

2. **`AskSheetVoice.tsx`:**
   - Before connecting, `fetchStoryCorpus(story.digest_id)` — run it **in parallel** with
     the existing token mint (don't serialize). Gate `connect()` on both.
   - Pass the corpus into `buildInNewsSystemInstructionWithCorpus(...)` for the
     `useGeminiLive({ systemInstruction })` arg; keep `tools: [askAboutStoryDeclaration]`
     and the (now web-only) `onToolCall` handler wired.
   - `useGeminiLive.ts` needs **no structural change** — it already takes
     `systemInstruction` + `tools`; the corpus just enlarges the instruction (trivial for
     ~6k tokens; verify setupComplete time stays acceptable).

## SP4 — Grounding hardening, flag, validation

1. **Determinism:** set a low `temperature` in `buildSetupFrame`'s `generationConfig`
   (`useGeminiLive.ts:170`) for the voice session, to keep corpus answers faithful (mirrors
   the server's `ANSWER_TEMPERATURE = 0.2`). Confirm the constrained endpoint accepts
   `temperature` inside `generationConfig`.
2. **Feature flag:** `NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT` (default off). Off → today's
   tool-only path; on → hybrid. Lets you A/B latency vs grounding quality. Document in
   `.env.example` + README.
3. **Optional non-blocking telemetry:** log the model's spoken answer + transcript so you
   can post-hoc sample for grounding faithfulness without blocking speech.
4. **Manual voice eval (the real gate, Rule 9/12):**
   - In-corpus question → answered, **no** tool call in logs, fast.
   - Out-of-corpus but related → filler line, tool call, web answer + web citations.
   - Unrelated → off-topic pushback.
   - Corpus-fetch failure → graceful tool-only behavior, still grounded.

---

## Risks
- **Native-audio adherence.** Getting a chatty native-audio model to (a) stay strictly
  inside injected text and (b) call the tool *only* on a miss needs prompt tuning + the SP4
  eval. Budget iteration here; the flag lets you fall back instantly.
- **Lost server verification on the corpus path.** Accepted trade-off; mitigated by
  constrained context + strict prompt + low temp (SP4).
- **Larger setup frame** slightly raises setupComplete time — negligible at ~6k tokens, but
  measure.

## Out of scope (separate, additive latency wins)
- Pre-connecting the session on mic-grant (cuts toggle→greeting time).
- Shortening/streaming the greeting nudge.
