# SP2 — Client: fetch corpus + corpus-aware system instruction

**Status: SUCCESS**

## What I implemented

### 1. `src/lib/voice/fetchStoryCorpus.ts` (NEW)
- `export async function fetchStoryCorpus(story_id: string, fetchImpl: typeof fetch = fetch): Promise<string>`.
- `GET ${base}/api/story/{story_id}/corpus` (story_id URL-encoded), returns the `context_block` string.
- Base-URL resolution replicated from `askQuestion.ts` as a private `getQaApiBaseUrl()` (reads `NEXT_PUBLIC_QA_API_BASE_URL`, strips trailing slash, empty = same-origin). `askQuestion.ts` was NOT modified (it does not export that helper — that file is SP3's).
- Graceful by contract: every failure path (`!response.ok`, fetch throw, non-object body, missing/non-string `context_block`) returns `""` and logs a structured `logger.warn` with `fix_suggestion`. Never throws.
- Private `parseContextBlock(body: unknown): string | null` narrows the body; only `context_block` is consumed (`approx_token_count` ignored, as the client doesn't need it).
- Full JSDoc + `@example`, mirroring askQuestion.ts density.

### 2. `src/lib/voice/storyVoicePrompts.ts` (EXTENDED)
- New PURE exported builder `buildInNewsSystemInstructionWithCorpus(story_headline, story_id, corpus_context_block, tool_grounding_clause?)`.
  - Empty/whitespace corpus → delegates to `buildInNewsSystemInstruction(story_headline, story_id, tool_grounding_clause)` (the single graceful-degradation seam).
  - Non-empty → persona + scope, a corpus-only answering directive, a labeled `STORY CONTEXT (answer ONLY from this; each line is [passage_id] text):\n<corpus>` block, "Never use outside knowledge. Never read passage ids or citations aloud.", the shared reply-style line, then the optional `tool_grounding_clause` appended LAST.
- To avoid string drift (Rule 3) I factored the shared persona/scope lines into private `buildPersonaScopePrefix()` and the shared reply-style into private const `REPLY_STYLE_LINE`, and refactored `buildInNewsSystemInstruction` to consume them. **Output of `buildInNewsSystemInstruction` is byte-identical** to before — proven by the unchanged, still-passing `tests/lib/voice/storyQaTool.test.ts` which asserts on the assembled instruction.
- `buildInNewsSystemInstruction` and `buildGreetingNudge` signatures/behavior unchanged.

## Files created / modified
- `src/lib/voice/fetchStoryCorpus.ts` (new)
- `src/lib/voice/storyVoicePrompts.ts` (extended)
- `tests/lib/voice/fetchStoryCorpus.test.ts` (new)
- `tests/lib/voice/storyVoicePromptsCorpus.test.ts` (new)

Touched ONLY the two source files listed in the mission plus their mirrored tests. Did NOT touch askQuestion.ts, storyQaTool.ts, or AskSheetVoice.tsx (SP3). Did NOT `git add` / commit.

## Divergences (and why)
- **Factored shared persona/reply-style into private helpers in storyVoicePrompts.ts.** The mission explicitly invited this ("factor the shared persona/scope lines into a small private const or helper so you don't duplicate the string and risk drift"). `buildInNewsSystemInstruction`'s observable output is unchanged.
- None beyond that.

## Self code-review findings + fixes
- **[None / Info] `buildInNewsSystemInstruction` refactor** — split into helpers; output byte-identical, guarded by the existing storyQaTool test (passes). No behavior change.
- **[None] Graceful "" contract** — all four fetchStoryCorpus failure modes return `""`; no throw escapes.
- **[None] Type safety** — no `any`; `unknown` narrowed via guards; explicit return types.
- **[Low, fixed by linter] Formatting** — Biome reflowed two test files' import/object wrapping; applied `biome check --write` to my test files only; re-check clean.
- No critical/high/medium issues.

## Validation results

`npx tsc --noEmit` — clean (exit 0, no output):
```
EXIT:
```

`npx biome check` (4 touched files), after autofix:
```
Checked 4 files in 4ms. No fixes applied.
EXIT:
```

`npx vitest run` (new tests + existing storyQaTool guard):
```
 Test Files  3 passed (3)
      Tests  20 passed (20)
   Duration  1.03s
```
Tests added:
- fetchStoryCorpus: happy path (returns context_block, asserts GET + URL), server-empty 200 (passes "" through), non-200 → "", fetch throw → "" (resolves, never rethrows), malformed body (no context_block) → "", non-string context_block → "", non-object body → "".
- buildInNewsSystemInstructionWithCorpus: empty corpus equals tool-only instruction (with clause, whitespace-only, and no-clause variants); non-empty embeds the labeled STORY CONTEXT block + the corpus, reuses identical persona/scope, carries corpus-only + no-outside-knowledge + no-citations-aloud + reply-style directives, appends the tool clause LAST.
- Existing `storyQaTool.test.ts` still green → the buildInNewsSystemInstruction refactor is behavior-preserving.

`fetch` is mocked at the boundary via `vi.fn()`; no network hit.

## Definition of done: PASS
- fetchStoryCorpus returns context_block on success, "" on every failure. PASS
- buildInNewsSystemInstructionWithCorpus embeds corpus + corpus-first instruction, falls back to tool-only on empty corpus. PASS
- Typecheck + lint + tests pass. PASS

## Concerns for the orchestrator (exact contract SP3 must import)
- **SP3 imports for `AskSheetVoice.tsx`:**
  - `import { fetchStoryCorpus } from "@/lib/voice/fetchStoryCorpus";`
    - Signature: `fetchStoryCorpus(story_id: string, fetchImpl?: typeof fetch): Promise<string>`. Returns the context block, or `""` (the tool-only fallback signal). Per the plan, call `fetchStoryCorpus(story.digest_id)` in PARALLEL with the token mint.
  - `import { buildInNewsSystemInstructionWithCorpus } from "@/lib/voice/storyVoicePrompts";`
    - Signature: `buildInNewsSystemInstructionWithCorpus(story_headline: string, story_id: string, corpus_context_block: string, tool_grounding_clause?: string): string`.
    - **SP3 should always call this builder** and pass the (possibly `""`) corpus straight through — the builder itself does the empty-corpus → tool-only fallback internally (single seam). No need for SP3 to branch on empty corpus. Pass `STORY_QA_TOOL_GROUNDING_CLAUSE` as `tool_grounding_clause` (the rewritten corpus-first / tool-on-miss clause SP3 owns).
- **Tool clause is appended last.** SP3's rewritten `STORY_QA_TOOL_GROUNDING_CLAUSE` lands at the very end of the instruction (after the STORY CONTEXT block), which is the intended position for the corpus-first / tool-on-miss rule.
- **CORS dependency:** consuming this GET cross-origin relies on SP1's `allow_methods` now including `GET` (noted in SP1 report). No action for SP2.
- No changes needed in `useGeminiLive.ts` from SP2 — the corpus only enlarges the `systemInstruction` string SP3 passes in.
