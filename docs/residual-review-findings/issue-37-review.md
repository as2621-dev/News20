# Residual review findings — issue #37 (voice connect UX)

Panel: correctness, simplicity/reuse, security+concurrency lenses on the slice diff.
All critical/high/medium findings with concrete fixes were APPLIED (see the fix commit).
Deferred below — advisory or human-call items only.

## Deferred

1. **Architecture (medium, advisory): fold the "unexpected session end" distinction into the
   hook as a first-class `"ended"` status.** The component currently reconstructs it with
   `pendingReconnectRef` + `hasSessionBeenLiveRef` + a status-sync effect (~45 lines, three
   flag-set call sites). The hook's post-setup `socket.onclose` (current-socket, setup done)
   is the single point that knows "a live session ended unexpectedly" — emitting a distinct
   status there would delete the component machinery. Deferred because it widens the
   `GeminiLiveStatus` union consumed by tests and the archived voice components; do it as its
   own small slice.
2. **Test scaffolding dedup (low):** `makeStory()` + the localStorage stub are duplicated
   across `askSheetVoiceCorpus.test.tsx` and `askSheetVoiceState.test.tsx`; extract a shared
   helper next time either changes.
3. **liveLatency nits (low):** `emitOnce`'s internal null-guard is redundant with its callers'
   checks (kept for TS narrowing simplicity); the default `emitMark` param eta-wraps
   `logLiveLatencyMark`.

## Concurrency/state-machine changes to flag (applied, green tests)

- Hook `onerror`/`onclose` now no-op for sockets that are no longer `socketRef.current`
  (stale-session gating); tool responses only send on the socket the call arrived on.
- `frame.error` and pre-setup `goAway` now tear down and land on `"error"`; post-setup
  `goAway` lands on `"closed"` (component shows "session ended" + retry, after teardown).
- Component: voice-name fallback is suppressed for mic-origin errors
  (`micErrorOccurredRef`); the closed-watcher requires the session to have been live since
  the last connect.
