# CSO findings — phase-3-m3-auth-voice-foundation (SP3 + SP4 run, 2026-05-31)

Scope: the phase diff (Gemini Live transport + shared voice UI). SP1/SP2 were done-in-M1 and not re-audited here.

## Critical / High
None.

## Medium (logged for follow-up, not blocking this phase)

### M-1 — `/api/voice/live-token` mint route is unauthenticated
- **File:** `agents/worker/main.py` (`post_voice_live_token`).
- **Risk:** any caller that can reach the worker can mint single-use Gemini Live ephemeral tokens. Each token enables a Live session that consumes Gemini quota / real cost → an unauthenticated mint endpoint is a cost/abuse vector.
- **Why not fixed now:** (1) it matches the existing worker convention — `post_story_question` and the other worker routes are also unauthenticated (Rule 11, conform); (2) phase-3 SP3 did not specify auth on this route; (3) wiring Supabase-JWT verification into the worker is net-new infrastructure (scope expansion, Rule 3/5).
- **Where it gets gated:** **phase-3b SP4** explicitly owns the quota guard ("reuse the TLDW 600s/day heartbeat + hard-cap quota pattern to bound cost"). Add per-user auth + the daily Live budget there so mint is both authenticated and rate-limited. Until then the cost ceiling is the Gemini account quota.

## Verified clean
- **Secret hygiene (`agents/voice/live_token.py`):** `GEMINI_API_KEY` read only at the call boundary via `SecretStr.get_secret_value()`, sent only in the `x-goog-api-key` header, NEVER logged (logs the token-name *prefix* constant, never the token; error logs carry exception/response-body strings truncated to 200 chars, never the key) and NEVER returned to the client (response model carries only `ephemeral_token_name` + `expire_time_iso`).
- **Client never holds the key:** `src/lib/voice/useGeminiLive.ts` only ever receives the `auth_tokens/...` name from the worker and passes it via `?access_token=`. No `AIza…`/`x-goog-api-key`/`GEMINI_API_KEY` literal in client code.
- **Fail-loud:** mint helper raises on missing key / HTTP error / non-200 / malformed response; the route returns HTTP 502 (no silent fallback — a missing token has no in-conversation recovery).
- **Input validation:** the mint route takes no client body; the Gemini request body is a validated pydantic model.
