# Phase 2b — Sub-phase 3 execution report

**Sub-phase:** Q&A frontend — composer + thread + suggested chips + citation/refusal contract
**Status:** SUCCESS
**Date:** 2026-05-31

## What I implemented

The Detail-view grounded-Q&A frontend: a pinned bottom composer + suggested-chip
row that call the SP2 worker endpoint, and a thread that renders the
`.dot-typing` thinking beat → a grounded `.qa-bubble-a` with one `.cite-chip` per
citation, OR the `.qa-refusal` blush card with the mono `⌀ CAN'T ANSWER FROM
SOURCE` header. `answer_is_grounded` is the single switch between the two states
(the trust guarantee, ported byte-for-byte from `prototype-port-map.md` §7 +
`app.js`'s `askQuestion()`/`resolveAnswer()`).

Flow (mounted in `StoryDetail`):
1. Tap a suggested chip OR submit the composer → `handleAsk(questionText)`.
2. A `thinking` turn is appended (renders `.dot-typing`); the composer + chips
   disable while in flight so questions can't stack.
3. `askQuestion(story_id, questionText)` POSTs to the SP2 endpoint and maps the
   HTTP-200 `QuestionAnswer` body.
4. The turn flips to `answered`; `QaThread` branches on `answer_is_grounded` →
   grounded bubble + chips, or the refusal card (never an answer bubble).
5. A `story switch mid-flight` guard (`activeStoryIdRef`) drops a stale answer so
   it can't write into a different story's thread; turns reset on story change.

## Files created / modified

Created:
- `src/types/qa.ts` — `QuestionAnswer`, `AnswerCitation`, `QuestionRequest` TS
  types, mirroring the SP2 runtime contract (NOT in `src/types/detail.ts` —
  phase-2c owns that file).
- `src/lib/qa/askQuestion.ts` — calls `POST /api/story/{story_id}/question`,
  validates the body, enforces the refusal invariant (un-grounded ⇒ no chips
  surfaced), and degrades EVERY failure (network / non-200 / malformed) to a SAFE
  refusal — never a thrown error, never a fabricated grounded answer. Structured
  JSON logging with `fix_suggestion` on every error path.
- `src/components/detail/QaComposer.tsx` — pinned bottom input + send button
  (≥44px touch targets; trims, ignores empty, disables in-flight).
- `src/components/detail/QaThread.tsx` — renders the `.dot-typing` / grounded
  bubble / refusal card states; exports the `QaTurn` shape the Detail owns.
- `src/components/detail/SuggestedQuestionChips.tsx` — the mono pill chip row from
  `detail.suggested_questions`; tap = ask that exact question; empty → renders nothing.
- `tests/lib/qa/qaThread.test.tsx`, `tests/lib/qa/storyDetailQaFlow.test.tsx`.

Modified (additive only):
- `src/components/detail/StoryDetail.tsx` — mounted `QaThread` in the scrolling
  body and `SuggestedQuestionChips` + `QaComposer` as a pinned bottom bar over a
  fade-to-canvas gradient; added the Q&A turn state + `handleAsk`. The root was
  restructured to a flex column (scroll area flexes, composer pins below) so the
  `scrollContainerRef` STILL sits on the scroll container (LayerStack's
  `scrollTop < 10` close-gate is preserved). The existing body/trust/timeline
  composition + the stale-fetch guard are untouched.
- `.env.example` — added `NEXT_PUBLIC_QA_API_BASE_URL` (the worker origin; empty →
  same-origin relative path) with onboarding docs (CLAUDE.md new-env-var rule).

Did NOT touch (cross-phase guardrails): `src/types/detail.ts`,
`src/lib/detail/fetchStoryDetail.ts` (both phase-2c's), or anything under
`agents/**`. Those three files (+ their test) show in `git diff` as pre-existing
uncommitted phase-2c sibling work that was already in the tree before SP3 started.

## Endpoint base-URL decision

The static-export Capacitor SPA has no same-origin server, so `askQuestion`
resolves the worker origin from `NEXT_PUBLIC_QA_API_BASE_URL` (trailing slash
stripped). Empty (the default) → a same-origin relative `/api/story/...` path,
which is right for a dev rewrite / reverse-proxy front. No worker base URL existed
in the repo before; this is the minimal additive config, matching the existing
`NEXT_PUBLIC_*` convention.

## Test-harness decision (Rule 11)

The brief mentioned testing-library, but the existing `tests/lib/detail/*.test.tsx`
explicitly forbid adding it ("not a project dependency; scope lock forbids adding
one") and use React 19's `react-dom/client` + `react`'s `act` directly. I matched
that idiom (conformance > taste). Controlled-input typing goes through the native
value setter so React's `onChange` fires (the standard React-controlled technique).

## Self-review findings + fixes

- **[fixed]** First stale-guard draft compared `askStoryId !== story.digest_id`
  inside the closure — always equal (same render). Replaced with an
  `activeStoryIdRef` that always holds the current story id, so an in-flight
  answer from a previous story correctly detects it is stale and drops itself.
- **[fixed]** Double bottom safe-inset (composer form + bar wrapper both had
  `pb-safe-b`). Removed it from the composer form; the pinned bar owns the inset.
- **[fixed, formatting]** Biome `--write` reflowed StoryDetail + sorted imports in
  the test file; applied.
- **[ok]** Refusal invariant enforced in TWO places (defense in depth, Rule 9):
  `askQuestion` drops citations when not grounded, AND `QaThread`'s render branch
  shows the refusal card with no chips regardless of payload — a test asserts a
  (deliberately invalid) refusal-carrying-citations payload still renders zero chips.
- **[ok]** Verbose entity-prefixed names, full JSDoc with examples, structured
  logging with `fix_suggestion`. All components small (each < 130 LoC). Colours use
  tokens (`primary`/`accent`/`border`/`rounded-pill`/`rounded-control`); the one
  hardcoded value is the prototype's blush refusal-header `#E8B7BC`, matching the
  `TrustStrip`/`OpposingViewCard` precedent for that exact prototype treatment.

## Validation results

- `npx tsc --noEmit` → **PASS** (exit 0, no errors).
- `npm run lint` (`biome check .`) → **PASS** ("Checked 72 files. No fixes applied.").
- `npx vitest run` → **PASS** — **136 passed (17 files)**; the 10 new Q&A tests
  pass, no regressions in the existing 126.
- `npm run build` (full `next build` static export) → **PASS** — compiled, 6 static
  pages generated, export OK. (Pre-existing tailwind `MODULE_TYPELESS` warning is
  unrelated to SP3.)

Tests cover (Rule 9 — the trust guarantee):
- **Grounded branch:** a grounded turn renders exactly one `.cite-chip` per
  `answer_citations` entry (chip label = outlet name) and NO refusal card.
- **THE refusal test:** `answer_is_grounded === false` renders the
  `⌀ CAN'T ANSWER FROM SOURCE` card and **NEVER** an answer bubble and **NEVER** a
  citation chip — including a defense-in-depth case where an (invalid) refusal
  payload carries citations.
- **Thinking → answer transition:** the StoryDetail flow test taps a chip, asserts
  the `.dot-typing` thinking state shows while the (deferred) answer is in flight,
  then asserts it flips to the grounded bubble + chip; a second test asserts the
  off-source chip flips to the refusal card with no answer bubble.
- **Composer + chips:** trim/ignore-empty submit; tap-chip-equals-ask; empty chip
  list renders nothing.

## Definition of done: PASS

- Typing OR tapping a suggested question shows the thinking state, then a grounded
  bubble with citation chips (on-topic) OR the refusal card (off-source): **PASS**
  (StoryDetail flow test asserts both transitions).
- The component test asserts `answer_is_grounded=false` renders the refusal card
  and never an answer bubble: **PASS**.
- **Pending-human:** the real-device swipe/tap UI smoke (composer feel, chip-row
  scroll, gradient over the pinned bar, keyboard-avoidance) is the owner's device
  check — NOT faked here.

## Concerns / notes for SP4 + CSO

1. **SP4 (cache) is unblocked.** SP3 is purely a frontend consumer of the SP2
   `QuestionAnswer` contract — it adds no constraint on the backend cache layer.
   When SP4 lands the `story_qa` cache inside the worker route, `askQuestion`
   needs no change (same endpoint, same shape). The client already degrades a
   failed request to a refusal, so a cache miss/error stays graceful.
2. **Worker base URL:** `NEXT_PUBLIC_QA_API_BASE_URL` must be set to the deployed
   worker origin for the Capacitor build (no same-origin server). Flagged in
   `.env.example`. CORS on the worker must allow the app origin (out of SP3 scope).
3. **Multi-turn:** `conversation_id` is modeled in `QuestionRequest` but unused in
   M2 (single-turn). `askQuestion` does not send it — M3's concern.
4. **No worker integration test here** (the worker is Python + a hard infra
   prereq); `askQuestion` is unit-tested via the mocked StoryDetail flow + the
   pure component tests. A live end-to-end against the running worker is a
   pending-human / SP4-integration check.
