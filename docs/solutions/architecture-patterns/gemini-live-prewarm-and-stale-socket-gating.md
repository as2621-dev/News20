---
title: Gemini Live — token pre-warm freshness window, stale-socket gating, and honest connect states
tags: [gemini-live, websocket, ephemeral-token, prewarm, react-strictmode, voice, latency-marks]
problem_type: architecture
symptoms: orb shows LISTENING over a deaf session; error view flashes on StrictMode mount; infinite reconnect loop hammering the token mint; stale socket clobbers a reconnected session's status
root_cause: connection status conflated UI state; single-use token + async handshake + React effect re-runs interleave in non-obvious ways
date: 2026-07-07
---

Learned building issue #37 (honest Connecting state + pre-warm + latency marks). Five
non-obvious rules for the Gemini Live client (`src/lib/voice/useGeminiLive.ts`,
`AskSheetVoice.tsx`):

1. **Pre-warm cache freshness must key off `newSessionExpireTime`, not `expireTime`.** The
   worker mints `uses:1` tokens with 60s `newSessionExpireTime` (can no longer START a
   session) vs 30min `expireTime`. A pre-warmed token cached at sheet-open is only safe to
   consume for ~45s (`PREWARMED_TOKEN_MAX_AGE_MS`); past that, mint fresh. Cache the mint
   PROMISE (resolve null on failure) so connect() can await an in-flight pre-warm and a
   failed pre-warm silently degrades to a fresh mint.

2. **Gate `onerror`/`onclose` on `socketRef.current === socket`.** A superseded session's
   socket can close AFTER its replacement connected (e.g. the voice-name fallback reconnects
   before the rejected socket's close event lands). Ungated, the stale close resets the
   connect guard and stamps status "closed" over the live session. Same for tool round-trips:
   only send `toolResponse` on the socket the `toolCall` arrived on (a multi-second handler
   await can span a reconnect; a stale `call.id` on the new socket poisons it).

3. **`disconnect()` lands on "closed" even from idle — "unexpected end" watchers need a
   was-live latch.** React StrictMode's dev effect-remount tears down a never-connected hook;
   status goes idle→closed and a naive closed-watcher flashes a bogus error while the real
   connect succeeds underneath. Track `hasSessionBeenLiveRef` (set on "live", cleared on
   "connecting") and only treat closed as an ended session when it's set. Also: clear
   cross-effect flags (like a pending-reconnect marker) in a status-driven effect, never
   synchronously inside the reconnect call — within one effect flush a later effect still
   sees the transient old status but the already-cleared flag.

4. **Never `setViewState("listening")` after `await connect()`.** connect() reports failure
   via status (it does not throw), so a post-await state set runs on failure too — knocking a
   just-rendered error view back to "listening" and re-triggering the auto-connect mount
   effect: an infinite reconnect loop hammering the mint endpoint. Every legitimate call path
   already sets the view state before calling.

5. **e2e recipe for live-voice UI (no API key):** reuse the reelSections scaffold (seeded
   Supabase session + intercepted PostgREST) + intercept `/api/voice/live-token` by path
   substring + replace `window.WebSocket` with a Proxy that fakes ONLY
   `generativelanguage.googleapis.com` (answers `setup` with `{setupComplete}` after a delay
   so CONNECTING is observable; real sockets — Next HMR — pass through) + Chrome
   `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream` for the mic. See
   `tests/e2e/voiceSheetStates.e2e.mjs`. Use DOM-dispatched clicks (`el.click()` in
   page.evaluate) — puppeteer coordinate clicks miss the reel's layered buttons.
