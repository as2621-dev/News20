# SP3 — Re-scope the tool to web-only + wire corpus into the Live session

**Status: SUCCESS**

## What I implemented

### 1. `src/lib/qa/askQuestion.ts` — web_only passthrough
- Added a 5th positional param `web_only = false` (LAST param, after `fetchImpl`) — the minimal surgical choice; every existing positional caller `(story_id, question_text[, turns[, fetchImpl]])` is untouched.
- When `web_only` is true, the POST body gets `web_only: true`; when false it is OMITTED entirely (`...(web_only ? { web_only: true } : {})`), so the Detail-view body stays byte-identical to before (a first question with defaults serializes to exactly `{ question_text }`).
- Updated the JSDoc + added a second `@example` for the voice/web-only path.

### 2. `src/lib/voice/storyQaTool.ts` — corpus-first / tool-on-miss / web-only semantics
- **`STORY_QA_TOOL_GROUNDING_CLAUSE` rewritten** (still one exported const string): the model has the full story in STORY CONTEXT and answers from it directly (fast path); calls `ask_about_story` ONLY when the answer is NOT in that context; before calling, says one short filler line like "let me check that"; speaks `answer_text` verbatim; on `answer_is_grounded` false delivers it as a brief refusal/pushback, adding nothing.
- **`askAboutStoryDeclaration.description` updated** to: "Use ONLY for questions the story context does not cover … fetches a web-searched answer with citations, or a refusal/off-topic pushback …".
- **`buildAskAboutStoryHandler`** now calls `askQuestion(story_id, question_text, [], fetch, true)` — passing `web_only=true` while keeping `conversation_turns` + `fetchImpl` at their existing defaults. The relay-verbatim + safe-refusal + never-throw behavior is otherwise unchanged.

### 3. `src/components/blip/reel/AskSheetVoice.tsx` — corpus fetch + injection (non-serial)
- Imports swapped: `fetchStoryCorpus` added; `buildInNewsSystemInstruction` → `buildInNewsSystemInstructionWithCorpus`. Kept `STORY_QA_TOOL_GROUNDING_CLAUSE` + `askAboutStoryDeclaration` imports.
- New `storyCorpus` state (default `""`).
- New mount-time `useEffect` keyed on `story.digest_id` that calls `fetchStoryCorpus(story.digest_id)` and stores the result in `storyCorpus` (with an `isCurrent` guard against late resolution after unmount/story-change). `fetchStoryCorpus` never throws (returns "").
- `useGeminiLive`'s `systemInstruction` now = `buildInNewsSystemInstructionWithCorpus(story.headline, story.digest_id, storyCorpus, STORY_QA_TOOL_GROUNDING_CLAUSE)`. The builder handles empty corpus → tool-only internally, so I call it unconditionally and never branch on emptiness in the component.
- All other wiring (greetingNudge, voiceName/fallback, tools, onToolCall, onTranscript, onMicError, connectRef/disconnectRef ref pattern) untouched.

## Files modified
- `src/lib/qa/askQuestion.ts`
- `src/lib/voice/storyQaTool.ts`
- `src/components/blip/reel/AskSheetVoice.tsx`
- `tests/lib/qa/askQuestion.test.ts` (extended)
- `tests/lib/voice/storyQaTool.test.ts` (updated guard assertions to new copy)
- `tests/lib/reel/askSheetVoiceCorpus.test.tsx` (new focused component test)

Did NOT touch `useGeminiLive.ts`, `storyVoicePrompts.ts`, or `fetchStoryCorpus.ts`. Did NOT `git add` / commit.

## How the corpus fetch OVERLAPS the token mint rather than serializing (exact mechanism)
The token mint lives INSIDE `useGeminiLive.connect()` (confirmed: `useGeminiLive.ts:567` does `await fetch(\`${tokenBaseUrl}${MINT_TOKEN_PATH}\`)` inside the `connect` useCallback at line 512). `connect()` only fires later — on the user's "Enable microphone" gesture, or from the already-granted mount path after `getMicPermissionState()` resolves.

The corpus fetch is kicked off in a **separate mount `useEffect`** that runs immediately on first render — i.e. while the user is still reading the permission CTA and before they tap. It does NOT gate `connect()`: `connect()` reads whatever `systemInstruction` the latest render produced. Because `connect`/`disconnect` are stored in refs that are reassigned every render (`connectRef.current = connect`), once the corpus lands and triggers a re-render, `useGeminiLive` rebuilds `connect` closed over the corpus-bearing `systemInstruction`, and `connectRef.current` points at it. Net effect:
- The corpus fetch and the (gesture-triggered) token mint run on independent timelines — the fetch overlaps the user's mic-permission gesture, not in front of the mint.
- If the corpus is NOT ready at `connect()` time, `systemInstruction` is built from `storyCorpus === ""` → the builder falls back to the tool-only voice and the connection proceeds without blocking (graceful "" fallback, per SP2 contract).
- A new test (`askSheetVoiceCorpus.test.tsx`) asserts the FIRST `systemInstruction` handed to the hook (before the async fetch resolves) is the tool-only build (no labeled STORY CONTEXT block), proving non-serialization, and the LATEST one (after resolve) embeds the corpus.

## Self code-review findings + fixes
- **[High, found by my own test, fixed] Wrong discriminator in the component test.** The rewritten grounding clause itself contains the substring "STORY CONTEXT" ("…NOT in your STORY CONTEXT…"), so asserting `not.toContain("STORY CONTEXT")` for the tool-only path failed. Fixed by discriminating on the labeled block header `"STORY CONTEXT (answer ONLY from this"` (only the corpus builder emits it). This is the correct, robust marker.
- **[Verified] web_only false-path byte-identical.** Test asserts the exact body `{ question_text }` for the default path (no `web_only`, no `conversation_turns`). Existing askQuestion turn tests still green.
- **[Verified] No regression to greeting/tool wiring.** Diff is +43/-… on AskSheetVoice confined to imports, one state, one effect, and the systemInstruction arg; all other hooks/handlers untouched.
- **[Verified] Type safety.** `tsc --noEmit` clean; no `any` introduced.
- No critical/high issues remaining.

## Validation output (tails)
- `npx tsc --noEmit` → `TSC_EXIT:0` (clean).
- `npx biome check` (6 touched files) → `Checked 6 files in 57ms. No fixes applied.`
- `npx vitest run` (touched files) → `Test Files 3 passed (3) | Tests 15 passed (15)`.
- `npx vitest run` (FULL suite) → `Test Files 42 passed (42) | Tests 377 passed (377)`.

Tests added/updated:
- `askQuestion.test.ts`: `web_only=true` adds `web_only:true` to body; `web_only=false` (default) body is exactly `{ question_text }`.
- `storyQaTool.test.ts`: guard updated to new clause copy (corpus-first / tool-on-miss / "let me check that" filler / verbatim relay / `answer_is_grounded false`); new test for the declaration description (web-only fallback scoping); handler-call assertions updated to `(STORY_ID, "...", [], fetch, true)` including the malformed-arg cases.
- `askSheetVoiceCorpus.test.tsx` (new): corpus fetched for the story and injected into the Live `systemInstruction`; first instruction is tool-only (non-serialization); `""` corpus degrades to tool-only. Mocks `fetchStoryCorpus`, `useGeminiLive`, `micPermission` at the boundary — no network/socket/LLM.

## Definition of done: PASS
- Voice tool path sends `web_only=true`; Detail Q&A path unchanged (byte-identical body). PASS
- Grounding clause enforces corpus-first / tool-on-miss with a spoken filler. PASS
- AskSheetVoice fetches the corpus and injects it into the Live system instruction WITHOUT serializing in front of the token mint, with graceful "" fallback. PASS
- Typecheck + lint + tests pass. PASS

## Concerns for SP4
- **Feature flag (`NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT`) is NOT yet wired here.** SP3 wires the hybrid path UNCONDITIONALLY: AskSheetVoice always calls `fetchStoryCorpus` + `buildInNewsSystemInstructionWithCorpus`. For SP4's A/B flag, the cleanest seam is in AskSheetVoice: when the flag is OFF, (a) skip the corpus `useEffect` (or just leave `storyCorpus = ""`) AND (b) call `buildInNewsSystemInstruction(story.headline, story.digest_id, ...)` with the OLD tool-only clause. NOTE: the OLD `STORY_QA_TOOL_GROUNDING_CLAUSE` copy is now GONE — I rewrote that single const to corpus-first semantics. If SP4 wants the flag-OFF path to be a true behavioral revert (tool-forced-for-every-question), it will need a SEPARATE legacy clause constant; today flag-OFF would still get the new corpus-first clause text appended to a tool-only instruction (which reads slightly oddly: "you have the full story in STORY CONTEXT" with no STORY CONTEXT block present). Recommend SP4 either (i) keep a `LEGACY_TOOL_FORCED_CLAUSE` for flag-OFF, or (ii) accept that flag-OFF + empty corpus is "tool-only persona + corpus-first clause" and that the clause's tool-on-miss wording is still safe (it never forbids the tool, and the base persona already forbids ungrounded answers). My read: option (i) is cleaner for a clean A/B.
- **Temperature change (`useGeminiLive.ts:170` `buildSetupFrame.generationConfig`)** is untouched by SP3 (out of scope, file not editable). SP4 owns it. Nothing in SP3 depends on temperature; the corpus injection is purely the `systemInstruction` string.
- **fetchStoryCorpus story-change behavior:** the effect re-fetches and replaces `storyCorpus` whenever `story.digest_id` changes; the `isCurrent` guard prevents a stale story's corpus from landing after a story switch. AskSheetVoice is keyed per active story in practice, but the guard makes it safe either way.
- **No `conversation_turns` are sent on the voice web-only path** (handler passes `[]`). That matches today's voice tool behavior (it never sent turns). If SP4 wants multi-turn web-only voice follow-ups, that's a separate enhancement.
