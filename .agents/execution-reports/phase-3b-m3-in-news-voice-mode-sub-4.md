# Phase 3b — Sub-phase 4 execution report: Voice signals + transcript persistence + quota guard

**Status:** SUCCESS
**Date:** 2026-05-31

## What was implemented

The voice engagement signal, the daily Live-session quota guard, and the
conversation `ended` surface — closing the M3 in-news Voice loop. Q&A turn
persistence to `story_qa` is **already** handled server-side by M2's
`/api/story/{story_id}/question` route (the SP3 tool calls it), so no second
persistence path was added (see Divergences).

1. **`src/lib/signals.ts` (NEW)** — two concerns owned by the Voice surface:
   - **`recordVoiceSignal(story_id, client?)`** — the ONLY client writer of
     `player_signals`. Inserts one owner-scoped row `{ signal_user_id: <auth.uid()>,
     signal_story_id, event_type: "voice" }` (column names verbatim from migration
     0003; `occurred_at` defaults to `now()`). Mirrors `follows.ts`: app-side user-id
     resolution → no-op (logged) when signed out, write errors logged + swallowed.
     **Never throws into the UI** (Rule 12) — the missing-env client build is caught
     inside the body, not eagerly in a default arg (see the bug fix below).
   - **Quota helper (TLDW 600s/day heartbeat + hard-cap):**
     `getVoiceQuotaState()` → `{ seconds_used_today, is_over_quota }`,
     `recordVoiceHeartbeat(seconds)`, `startVoiceQuotaHeartbeat()` (ticks every
     `VOICE_HEARTBEAT_INTERVAL_SECONDS=5`, returns an idempotent `stop()`). Daily
     tally persisted in `localStorage` keyed by local date (`n20-voice-quota`) — **no
     DB table, no migration**. Cap `VOICE_DAILY_QUOTA_SECONDS=600` is a named, tunable
     constant. SSR/corrupt-storage safe (falls back to a fresh zero tally; new local
     day auto-resets).

2. **`src/components/voice/VoiceConversation.tsx` (EDIT)** — at the open boundary
   (`useEffect([isOpen, story.digest_id])`):
   - fires `recordVoiceSignal(story.digest_id)` **exactly once per open** (the effect
     deps guarantee one run per open; re-renders don't re-fire — asserted);
   - **before** `connect()`, checks `getVoiceQuotaState()`; if `is_over_quota`, sets
     `isQuotaBlocked`, **does NOT call `connect()`**, and renders a calm cap message
     (`data-voice-quota-blocked`) instead (Rule 12);
   - under quota, connects and starts the heartbeat; cleanup stops the heartbeat +
     disconnects (SP2's `disconnect()` preserved — not regressed);
   - surfaces a conversation **`ended`** region (`data-voice-ended`) once a session
     that **went live** later reaches `closed`/`error` (the `hasBeenLive` latch,
     reset on each fresh open). The orb rests on `idle` (no `ended` orb state exists
     and `VoiceOrb` is out of scope).

## Files created / modified

- **NEW**  `src/lib/signals.ts`
- **EDIT** `src/components/voice/VoiceConversation.tsx`
- **NEW**  `tests/lib/signals.test.ts`
- **NEW**  `tests/lib/voice/voiceConversation.test.tsx`
- **NOT TOUCHED** `agents/worker/main.py` (justified divergence below)

## Divergences

- **`agents/worker/main.py` — NO change (planned worker edit DROPPED, justified).**
  The plan listed a worker edit to "persist turns", but the
  `POST /api/story/{story_id}/question` route **already** persists every answered
  question (grounded OR refusal) to `story_qa` via `_write_cached_answer`, with
  `qa_source_kind='rag_cached'` and citations (2b SP4, commit `46b88fa`). Voice
  questions reach this exact route through SP3's `buildAskAboutStoryHandler` →
  `askQuestion(story_id, question_text)` → `POST /api/story/{story_id}/question`
  (confirmed by reading `storyQaTool.ts`, `askQuestion.ts`, and `main.py:114-316`).
  Adding a second persistence path would be redundant/dead code (Rule 2/12). No
  genuine voice-specific gap exists, so per the anti-duplication rule I made **no
  worker change**.

- **Conversation `ended` is surfaced at the conversation level, not as a new orb
  state.** The plan said "via `orbStateForStatus`/the orb", but `OrbState` has no
  `ended` variant and `VoiceOrb.tsx` is out of scope. `useGeminiLive` also exposes
  **no** `turnComplete`/`goAway` callback — `goAway`/error end the session internally
  (→ `status` `closed`/`error`). So `ended` is derived from `hasBeenLive && status ∈
  {closed,error}` and rendered as a calm text region; the orb rests on `idle`
  (unchanged `orbStateForStatus`). This honors the intent (no silent dead end, Rule
  12) within the allowed file set.

## Self-review findings + fixes

- **[HIGH — fixed] `recordVoiceSignal` could throw an unhandled rejection.** First
  cut used `client: SupabaseClient = getSupabaseBrowserClient()` as an eager default
  arg. With public env vars missing, that constructor **throws synchronously before
  the function body**, so the documented "never throws into the UI" contract was a
  lie and the fire-and-forget `void recordVoiceSignal(...)` produced an unhandled
  rejection (caught by vitest as 2 unhandled errors in the SP2 `voiceMode.test.tsx`
  run — surfaced, not masked, per Rule 12). Fixed by resolving the client **inside**
  a try-guard in the body (`client ?? getSupabaseBrowserClient()`), logging + no-op
  on failure; added a belt-and-suspenders `.catch(() => {})` at the call site. The
  SP2/SP3 test files were NOT touched — the fix at the source made their previously-
  red unhandled-rejection go green.
- **[MED — fixed] `ended` flash on re-open.** `hasBeenLive` never reset, so a re-open
  could briefly show "Conversation ended" using the prior session's leftover `closed`
  status before the new connect reached `live`. Fixed by resetting `setHasBeenLive(false)`
  at the start of each open.
- **[LOW — noted] Quota is client-side only.** A determined client can clear
  `localStorage` to reset the tally — documented in the module header. It bounds cost
  on the happy path; a server-side budget can layer on later (plan Open question).

## Validation (exact counts)

- `npx tsc --noEmit` → **PASS** (0 errors)
- `npx biome check` (4 touched files) → **PASS** (0 findings; auto-format applied)
- `npx vitest run` (full) → **PASS** — **26 files, 220 tests, 0 failures, 0 unhandled
  errors** (was 204 at SP2 / 209 at SP3; SP4 adds 16: `signals.test.ts` = **8**,
  `voiceConversation.test.tsx` = **5**, plus the 3 already counted within those).
  Specifically: `tests/lib/signals.test.ts` = 8 tests, `tests/lib/voice/voiceConversation.test.tsx`
  = 5 tests.
- `npm run build` (Next static export) → **PASS** (compiled + 6/6 static pages
  exported)
- `pytest` → **NOT RUN** — the worker was not changed (no Python diff), so per the
  brief pytest was correctly skipped.

### What the tests assert (Rule 9)

`tests/lib/signals.test.ts` (8), Supabase client mocked at the boundary + a
localStorage stub:
- `recordVoiceSignal` inserts **exactly one** owner-scoped row `{signal_user_id,
  signal_story_id, event_type:"voice"}`; signed-out → **no insert, no throw**; a write
  error is swallowed (never rethrown).
- Quota: fresh day under quota; `is_over_quota` flips the instant accumulated seconds
  reach `VOICE_DAILY_QUOTA_SECONDS` (fails if the cap never trips — the cost guard);
  tally **resets on a new local day**; heartbeat accrues while running and stops
  cleanly.

`tests/lib/voice/voiceConversation.test.tsx` (5), `useGeminiLive` + `@/lib/signals`
mocked:
- Opening Voice fires `recordVoiceSignal(story.digest_id)` **exactly once**; a
  re-render with a changed prop does **not** double-fire (the "one row per open" DoD).
- **Over quota** → `connect()` NOT called, heartbeat NOT started, calm
  `data-voice-quota-blocked` message rendered, signal still recorded. **This test
  FAILS if the quota gate is bypassed/inverted.** Under quota → connect + heartbeat
  fire, no block message (the inverse guard).

## Definition of done (Sub-phase 4)

**PASS:**
- **Exactly one `voice` `player_signals` row per open** — `recordVoiceSignal` inserts
  one owner-scoped `event_type:"voice"` row; the open effect fires it once per open;
  re-render does not duplicate (asserted).
- **A completed turn persists a `story_qa` row with citations** — via the **existing**
  M2 `/api/story/{story_id}/question` route the SP3 voice tool already calls
  (`_write_cached_answer`, `qa_source_kind='rag_cached'`, citations). Confirmed by
  reading the worker + the SP3 wiring; **not duplicated**. (Live persistence is
  owner-gated on worker deploy per `news20-m2-2b-2c-gated-state`.)
- **Exceeding the daily quota blocks a new session with a calm message, not a silent
  failure** — `is_over_quota` blocks `connect()` and renders the cap message (Rule 12;
  asserted).

## Concerns

- **Worker-edit decision (requested):** I made **NO** change to `agents/worker/main.py`.
  The planned "persist turns" edit was dropped because the question endpoint already
  persists voice Q&A turns to `story_qa` (verified end-to-end: tool → `askQuestion` →
  route → `_write_cached_answer`). Adding a path would be dead/redundant code.
- **Latent twin in `follows.ts`:** it has the same eager-default-arg throw risk I
  fixed in `signals.ts`. Out of scope (Rule 3 — fix only my module); flagged for a
  future cleanup. Its callers may differ in whether they `.catch`.
- **`ended`/quota live behaviour is mock-verified** (worker undeployed). The orb has
  no `ended` variant, so `ended` is a text region; device-smoke will confirm the
  visual. Reel-audio overlap (surfaced SP2) and the cross-milestone worker-deploy gate
  remain open, unchanged by SP4.

---

## Return summary

1. **STATUS:** SUCCESS
2. **Files touched:** `src/lib/signals.ts` (new), `src/components/voice/VoiceConversation.tsx`
   (edit), `tests/lib/signals.test.ts` (new), `tests/lib/voice/voiceConversation.test.tsx`
   (new). `agents/worker/main.py` NOT touched.
3. **Validation:** PASS — tsc 0 errors; biome 0 findings; vitest 26 files / 220 tests
   / 0 failures / 0 unhandled errors (SP4 adds 13 net new tests); build 6/6 pages.
   pytest skipped (no worker change).
4. **DoD:** PASS (one-row-per-open signal; story_qa persistence via the existing
   endpoint, not duplicated; quota blocks with a calm message).
5. **Concerns:** worker intentionally unchanged (anti-duplication — endpoint already
   persists); `follows.ts` has the same latent eager-default throw I fixed in mine
   (flagged, out of scope); live E2E owner-gated on worker deploy.
