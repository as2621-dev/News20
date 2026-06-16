# SP4 — Grounding hardening, feature flag, validation

**Status: SUCCESS**

## What I implemented

### 1. Feature flag `NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT` (default OFF) — `AskSheetVoice.tsx`
- Added a tiny local helper `isVoiceCorpusInContextEnabled()` (no existing boolean-env
  helper in the repo; the codebase reads `process.env.NEXT_PUBLIC_*` inline). It reads
  the env string, lowercases it, and returns `true` only for `"1"` or `"true"` —
  anything else/undefined is OFF. Matches the `OnboardingFlow.tsx` `=== "true"` pattern,
  extended to also accept `"1"` per the mission.
- The flag is read once per mount via `useMemo(() => isVoiceCorpusInContextEnabled(), [])`.
- **Flag ON** → current SP3 behavior: the corpus `useEffect` runs `fetchStoryCorpus`,
  and `systemInstruction = buildInNewsSystemInstructionWithCorpus(headline, id, storyCorpus, STORY_QA_TOOL_GROUNDING_CLAUSE)`.
- **Flag OFF** → true legacy path: the corpus `useEffect` early-returns (so **no corpus
  GET fires** — verified by test), `storyCorpus` stays `""`, and
  `systemInstruction = buildInNewsSystemInstruction(headline, id, LEGACY_TOOL_FORCED_CLAUSE)`.
  The `onToolCall` handler is still wired, so the tool answers every factual question —
  net behavior == pre-phase.
- The corpus effect's dep array now includes `corpusInContextEnabled`.

### 2. Legacy clause constant — `storyQaTool.ts`
- Added `export const LEGACY_TOOL_FORCED_CLAUSE`, recovered **byte-for-byte** from
  `git show HEAD:src/lib/voice/storyQaTool.ts` (verified programmatically: the const
  body string-equals HEAD's original `STORY_QA_TOOL_GROUNDING_CLAUSE` value).
- The SP3 corpus-first `STORY_QA_TOOL_GROUNDING_CLAUSE` is left untouched. Clean A/B:
  flag-ON uses the corpus-first clause, flag-OFF uses `LEGACY_TOOL_FORCED_CLAUSE`.

### 3. Low temperature on the voice session — `useGeminiLive.ts`
- Added `temperature: 0.2` inside `buildSetupFrame`'s `generationConfig` (mirrors the
  server's `ANSWER_TEMPERATURE = 0.2` in `agents/qa/agent.py`), with a `// Reason:`
  comment. Updated `buildSetupFrame`'s return type to include `temperature: number` and
  added a JSDoc note.

### 4. README — `README.md`
- Added an "Environment variables" subsection in the blip section documenting the flag:
  what it does, default OFF, and the latency-vs-grounding-strictness trade-off, in plain
  prose (no marketing voice).

## Files modified
- `src/components/blip/reel/AskSheetVoice.tsx`
- `src/lib/voice/storyQaTool.ts`
- `src/lib/voice/useGeminiLive.ts`
- `README.md`
- `tests/lib/voice/useGeminiLive.test.tsx` (extended)
- `tests/lib/voice/storyQaTool.test.ts` (extended)
- `tests/lib/reel/askSheetVoiceCorpus.test.tsx` (extended)

Touched ONLY the four source/doc files in the mission (plus their mirrored tests).
**Did NOT touch `.env.example`.** Did NOT `git add` / commit.

## `.env.example` deferral note
`.env.example` already carries foreign uncommitted edits from another concurrent session,
so I did **not** add the new var there (would entangle foreign changes). **The owner of
`.env.example` should add `NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT=false` (with a one-line
comment) to it later.** The flag is documented in README in the meantime.

## temperature-in-generationConfig: NEEDS LIVE VERIFICATION
I could **not** verify from existing code/comments that the v1alpha constrained Live
endpoint accepts `temperature` inside `generationConfig`. The only documented constraint
in this file is that `speechConfig` MUST sit inside `generationConfig` (top-level → WS
close 1007). `temperature` is a standard `generationConfig` field on the GenAI API, so it
is very likely accepted, but the manual voice eval is the confirmation gate: **if the
socket 1007-rejects `temperature` at `generationConfig`, it must be moved or removed.**
Comment in the code flags this. Nothing else depends on temperature.

## Self code-review findings + fixes
- **[Verified] LEGACY_TOOL_FORCED_CLAUSE byte-for-byte == HEAD original.** Confirmed with
  a node string-equality check against the `git show HEAD` value. PASS.
- **[Verified] Flag-OFF is a genuine no-corpus-fetch path.** New test asserts
  `fetchStoryCorpus` call count is 0 when the flag is off/unset.
- **[Verified] temperature placement.** Inside `generationConfig` (alongside
  `speechConfig`/`responseModalities`), not at `setup` top level; type + JSDoc updated.
- **[Verified] README is plain.** Single table row, no marketing language.
- **[Verified] Type safety.** `tsc --noEmit` clean; no `any` introduced; helper has an
  explicit `boolean` return type.
- **[Note, not changed — Rule 3] SP3's JSDoc above `STORY_QA_TOOL_GROUNDING_CLAUSE`**
  still calls it the "web-only path" clause (SP3's wording, not my edit). Left as-is to
  stay surgical.
- **[Low, fixed by linter] Formatting.** Biome reflowed the ternary in AskSheetVoice;
  applied `biome check --write` to that file only; re-check clean.

## Validation output (tails)
- `npx tsc --noEmit` → `TSC_EXIT:0` (clean, no output).
- `npx biome check` (6 touched files, after autofix) → `Checked 6 files. No fixes applied.` (exit 0)
- `npx vitest run` (touched files) → `Test Files 3 passed (3) | Tests 26 passed (26)`.
- `npx vitest run` (FULL suite) → `Test Files 42 passed (42) | Tests 381 passed (381)` (was 377 in SP3; +4).

Tests added/updated:
- `useGeminiLive.test.tsx`: buildSetupFrame asserts `generationConfig.temperature === 0.2`
  (WHY: a low temp keeps the corpus-in-context voice faithful; mirrors server 0.2).
- `storyQaTool.test.ts`: `LEGACY_TOOL_FORCED_CLAUSE` exists, contains "You MUST NOT answer
  any factual question" + "For every such question, call ask_about_story" + tool-forced
  refusal handling, contains NO "STORY CONTEXT", and is distinct from the corpus-first
  clause. Existing corpus-first assertions on `STORY_QA_TOOL_GROUNDING_CLAUSE` unchanged.
- `askSheetVoiceCorpus.test.tsx`: suite default now sets the flag ON for the original
  corpus-injection tests (corpus fetched + injected). New "flag OFF" describe block:
  (a) corpus NOT fetched (call count 0), (b) legacy tool-forced instruction used (no
  STORY CONTEXT block, carries the MUST-NOT clause), (c) unset flag == OFF. Env var
  saved/restored per test in beforeEach/afterEach. `fetchStoryCorpus`, `useGeminiLive`,
  `micPermission` mocked at the boundary — no network/socket/LLM.

## Definition of done: PASS
- Flag defaults OFF → pre-phase behavior (no corpus GET, legacy tool-forced clause, tool
  answers all questions). PASS
- Flag ON → hybrid corpus-in-context path. PASS
- Voice session sends temperature 0.2 (placement done; live-accept is the one documented
  open verification). PASS (with noted live check)
- README documents the flag. PASS
- Typecheck + lint + tests pass. PASS

## MANUAL VOICE EVAL CHECKLIST (the real grounding gate — Rule 9/12)
**Requires a live Gemini Live session — CANNOT be run in this sandbox.** Run with the
worker deployed + `GEMINI_API_KEY` set + the token endpoint reachable.

1. **temperature accepted** — open a voice session; confirm `setupComplete` arrives and
   the socket does NOT close 1007 (`Unknown name "temperature"`). If it 1007s, move/remove
   `temperature` from `generationConfig` in `buildSetupFrame`.
2. **Flag ON, in-corpus question** → answered + spoken back with **NO** `ask_about_story`
   tool call in logs (grep for absence of `ask_about_story_tool_called`), fast first audio.
3. **Flag ON, out-of-corpus but related question** → model says a short filler ("let me
   check that") → `ask_about_story_tool_called` fires → web-searched answer + web citations.
4. **Flag ON, unrelated question** → off-topic pushback ("let's keep it to this story").
5. **Flag ON, corpus-fetch failure** (e.g. point at a story with no corpus / 200-empty) →
   graceful tool-only voice, still grounded (no STORY CONTEXT, falls back to the tool).
6. **Flag OFF (default)** → legacy behavior: no corpus GET in the network log, every
   factual question routes through `ask_about_story` (tool-forced), still grounded.

## Concerns
- **temperature live-verification (above) is the one open item** — placement is correct
  and tested, but the constrained endpoint's acceptance is unconfirmed without a socket.
- **The manual voice eval is the real grounding gate** and cannot be run here; the corpus
  path drops the server's second `verify_answer_against_corpus` guardrail (the accepted
  phase trade-off), so faithfulness rests on constrained-context + strict prompt + temp 0.2.
- **`.env.example`** still needs `NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT=false` added by its
  owner (not done here to avoid entangling foreign uncommitted edits).
