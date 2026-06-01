# Phase 3b — Sub-phase 3 execution report: Grounded answer round-trip + refusal contract

**Status:** SUCCESS
**Date:** 2026-05-31

## What was implemented

The `ask_about_story` Gemini Live function-call round-trip, wiring the Live
transport to M2's shipped in-context grounded-Q&A path (`askQuestion` →
`POST /api/story/{story_id}/question`). No retriever built (Pinecone/RAG dropped
2026-05-31); this only routes the tool call to the existing endpoint.

1. **`src/lib/voice/storyQaTool.ts` (NEW):**
   - `askAboutStoryDeclaration: GeminiToolDeclaration` — function name
     `ask_about_story`, description ("Answer the user's question about THIS news
     story using only its verified sources…"), JSON-schema params with a single
     required string `question_text`. Name pinned via `ASK_ABOUT_STORY_TOOL_NAME`
     so the declaration + handler guard agree on one spelling.
   - `STORY_QA_TOOL_GROUNDING_CLAUSE: string` — the system-instruction clause that
     FORBIDS answering without the tool: "You MUST NOT answer any factual question…
     call `ask_about_story`… speak ONLY the tool's `answer_text`… when
     `answer_is_grounded` false, say its `answer_text` verbatim… DO NOT guess,
     invent, or add any facts" (Decision #5 / Rule 9).
   - `buildAskAboutStoryHandler(story_id)` — returns an async `onToolCall` handler:
     extracts `question_text` (missing/non-string → `""`, never throws), calls
     `askQuestion(story_id, question_text)`, returns `{ answer_text,
     answer_is_grounded, answer_citations }` (a `Record<string, unknown>` via an
     explicit `AskAboutStoryToolResponse` interface). Relays the server's verdict
     **verbatim** — never substitutes/invents on a refusal. Structured JSON logging
     on call/complete/error paths with `fix_suggestion`. No bare `any` (one
     `extends Record<string, unknown>` interface; `unknown[]` for citations).

2. **`src/components/voice/VoiceMode.tsx` (EDIT):** filled the SP2 seam props on
   `<VoiceConversation>`: `toolsSlot={[askAboutStoryDeclaration]}`,
   `onToolCallSlot={handleAskAboutStory}`, `tool_grounding_clause={STORY_QA_TOOL_GROUNDING_CLAUSE}`.
   The handler is **memoized** via `useMemo(() => buildAskAboutStoryHandler(story.digest_id), [story.digest_id])`
   so `useGeminiLive` isn't handed a new `onToolCall` identity each render. Uses
   `story.digest_id` (same id field SP1/SP2 use).

## Files created / modified

- **NEW**  `src/lib/voice/storyQaTool.ts`
- **EDIT** `src/components/voice/VoiceMode.tsx`
- **NEW**  `tests/lib/voice/storyQaTool.test.ts`
- **EDIT** `tests/lib/voice/voiceMode.test.tsx` (divergence — see below)

`VoiceConversation.tsx` was **NOT** touched — its SP2 seam props threaded straight
through, so the wiring was fully achievable from `VoiceMode`.

## Divergences

- **`tests/lib/voice/voiceMode.test.tsx` edited (one assertion block).** The SP2
  test "on grant: configures useGeminiLive…" asserted `lastGeminiLiveParams?.tools`
  is **undefined** — an explicit guard on the *empty* SP3 seam. SP3 legitimately
  fills that seam, so the assertion went red. Per **Rule 12** (a red test left
  masked = "tests pass" lying) and **Rule 9**, I flipped it to assert the seam is
  now correctly wired: `tools` has length 1 with name `ask_about_story`,
  `onToolCall` is a function, and the assembled `systemInstruction` contains
  `STORY_QA_TOOL_GROUNDING_CLAUSE`. This is the minimal change to that test (one
  block + one import line); no other SP2 assertion touched. Strictly this file was
  outside the "may touch" list, but updating a test invalidated by the very change
  this sub-phase ships is required to keep the suite honest — flagging it here.

- **Biome reordered imports** in `storyQaTool.ts` and `storyQaTool.test.ts`
  (`--write`). In the test, `vi.mock("@/lib/qa/askQuestion")` stays correct because
  vitest hoists `vi.mock` above all imports regardless of source order — the
  mock-before-import guarantee holds.

## Self-review findings + fixes

- **[HIGH — verified safe]** Refusal pass-through. The handler returns the server's
  `answer_text`/`answer_is_grounded`/`answer_citations` unchanged. On
  `answer_is_grounded:false` (from `askQuestion`, which also degrades EVERY failure
  to a safe refusal) the handler emits the refusal copy + `[]` citations and nothing
  else — no fabrication path exists. Locked by test (2) below.
- **[MED — fixed by design]** Handler identity stability. `buildAskAboutStoryHandler`
  is called inside `useMemo` keyed on `story.digest_id`, so `useGeminiLive` gets a
  stable `onToolCall` per story (no effect thrash), matching SP2's ref-stability
  posture.
- **[LOW — noted]** The catch-branch fallback refusal text ("I can't answer that
  from this story's sources right now.") is a distinct string from
  `CLIENT_REFUSAL_ANSWER_TEXT`. It is effectively unreachable (`askQuestion` never
  throws — it returns a safe refusal), so it's pure belt-and-suspenders (Rule 12).
  Acceptable; not worth importing the private constant.

## Validation (exact counts)

- `npx tsc --noEmit` → **PASS** (0 errors)
- `npx biome check` (touched files) → **PASS** (0 findings)
- `npx vitest run` → **PASS** (24 files, **209 tests**; new `storyQaTool.test.ts` =
  **5 tests**; SP2 `voiceMode.test.tsx` updated assertion still green)
- `npm run build` (Next static export) → **PASS** (compiled + exported, 6/6 static
  pages)

### What the tests assert (Rule 9)

`tests/lib/voice/storyQaTool.test.ts` (5 tests), `@/lib/qa/askQuestion` mocked via
`vi.mock`:
1. **On-topic round-trip** — a `toolCall` with `question_text` → `askQuestion`
   called **once** with `(story_id, question_text)`; response carries the grounded
   `answer_text` + **non-empty** citations.
2. **Off-source refusal (zero-hallucination)** — `askQuestion` resolves
   `answer_is_grounded:false` (refusal copy, `[]` citations) → response carries that
   verdict + refusal text verbatim, citations `[]`, and is asserted **not** equal to
   the grounded answer. **This test FAILS if anyone makes the handler invent content
   on a refusal** — the Decision #5 guard.
3. **Declaration contract** — name `ask_about_story`, `type:"object"`,
   `properties.question_text.type === "string"`, `required: ["question_text"]`.
4. **Forbidding clause in the assembled instruction** —
   `buildInNewsSystemInstruction(headline, id, STORY_QA_TOOL_GROUNDING_CLAUSE)`
   contains the clause, `ask_about_story`, `/MUST NOT answer/`, `/answer_is_grounded
   false/`.
5. **Malformed tool call (Rule 12)** — missing arg and non-string `question_text:42`
   → handler calls `askQuestion(story_id, "")` (never throws) and the response is a
   refusal.

`tests/lib/voice/voiceMode.test.tsx` (updated): the on-grant config test now asserts
the SP3 seam is filled (tool wired, `onToolCall` present, clause in the instruction).

**toolResponse shape (Rule 9 note):** the handler returns the `response` *object*;
`useGeminiLive` (unchanged) wraps it into
`{toolResponse:{functionResponses:[{id,name,response}]}}` per the contract. The
handler's `Record<string, unknown>` return matches `onToolCall`'s declared return
type — verified by `tsc` and the hook's own (unchanged) round-trip code. No live
socket asserted (worker undeployed; mock-verified).

## Definition of done (Sub-phase 3)

**PASS:**
- Grounded question → grounded spoken answer citing sources — handler relays
  `answer_text` + non-empty `answer_citations` from the grounded path (test 1).
- `answer_is_grounded === false` → refusal state, **no fabricated answer** — handler
  relays refusal copy + `[]` verbatim; the test fails on any invented content (test 2).
- `toolResponse` shape matches the Gemini Live contract — handler returns the
  `response` object the unchanged hook wraps into the documented frame; return type
  type-checks against `onToolCall`.
- Live E2E is **owner-gated** on worker deploy (`news20-m2-2b-2c-gated-state`); all
  assertions here are mock-verified per the brief (no live HTTP attempted).

## Concerns + the SP4 seam (untouched)

- **SP4 seam — NOT changed by SP3.** The `player_signals` `voice` write + daily
  quota check still slot into the `useEffect(..., [isOpen, story.digest_id])` open
  boundary in `VoiceConversation.tsx` (marked with the SP4 seam comment).
  `story_qa` turn persistence (`qa_source_kind='rag_cached'`) + the conversation
  `ended` state still hang off the hook's turn lifecycle / `orbStateForStatus`.
  `src/lib/signals.ts` is still SP4's to create. SP3 added no signal/persist/quota
  code.
- **Reel-audio overlap** (surfaced by SP2) is unchanged and still open — out of
  scope here.
- **Cross-milestone dependency** on M2's `/api/story/{story_id}/question` endpoint
  is undeployed (`news20-m2-2b-2c-gated-state`); the live grounded round-trip can't
  be E2E-verified until the worker ships. Code + trust contract are mock-verified.
