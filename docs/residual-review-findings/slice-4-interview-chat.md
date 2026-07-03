# Residual review findings — slice #4 (chat interview stage)

Multi-agent review panel on commits `27bb767` + `06c5157`. Gate invariant: **PASS**.
Turn-protocol fidelity: **PASS**. Security: **CLEAN**. All concrete correctness /
simplicity / Rule-12 findings were fixed in `06c5157`. The items below are advisory or
human-call and were deliberately deferred (out of slice #4's scope) — not defects that
block the slice.

## Deferred (advisory)

- **`getWorkerBaseUrl` is duplicated 6× across client→worker modules.** `turnClient.ts`
  adds the 6th private copy of `process.env.NEXT_PUBLIC_QA_API_BASE_URL ?? "" → strip
  trailing slash` (also in `assembleFirstRunFeed.ts`, `qa/askQuestion.ts`,
  `voice/fetchStoryCorpus.ts`, `sourceSearch.ts`, `voice/useGeminiLive.ts`). The new
  copy MATCHES the established per-module convention (Rule 11), so it was left as-is; a
  repo-wide extraction to one shared `getWorkerBaseUrl()` util is its own refactor slice
  (touches 5 foreign files on a shared tree).

- **Returning-user strand via `isSourceOnboardingComplete()` → `router.push("/")`
  (pre-existing, cross-flow).** A user who completed the source step in a prior session
  but abandoned at "Build your 30" (so `user_onboarded_at` is still null) will, on
  re-entry, finish the interview and be pushed straight to `/` — never reaching `build`,
  so the gate is never stamped and the root gate can bounce them back. This logic is
  carried over verbatim from the retired picker path (identical in `handleInterviewComplete`
  and the old `handleComplete`), so slice #4 perpetuates rather than introduces it. Fix is
  a flow-wide decision (route to `build`/stamp when the gate is unstamped) — file as a
  separate slice.

- **Worker `InterviewBubble.bubble_label` is unbounded (`str`) while request
  `bubbles_offered` items are `max_length=200`.** A worker-generated label > 200 chars,
  echoed back by the client, would 422 the next request → the client degrades to a `retry`
  turn → an unbreakable retry loop on that turn. Worker-controlled and unlikely; the fix is
  a `max_length=200` on the response model in `agents/interview/models.py` (slice #1
  worker-side, not this client slice).

## Note for slice #9 (rebuild-my-feed, the consumer of `InterviewChat`)

- `InterviewChat` unconditionally offers resume from a cached transcript on mount and has
  no prop to force a fresh start. A rebuild-my-feed entry may want a `forceRestart` /
  `skipResume` prop so a stale onboarding transcript doesn't surface an unwanted "Pick up
  where you left off?" prompt.

## Informational (no fix)

- The localStorage interview transcript is stored unencrypted with no TTL until terminal
  confirm clears it. It holds only the user's own topic phrases (no token/JWT — verified by
  the security lens) on their own device, and is cleared on confirm and on restart. By-design
  UX state, not a security hole.
