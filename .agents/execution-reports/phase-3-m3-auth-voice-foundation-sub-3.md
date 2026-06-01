# Phase 3 — Sub-phase 3 execution report

**Sub-phase:** Gemini Live transport — token mint + parameterized WSS hook
**Worktree:** `News20-sub-3`
**Status:** SUCCESS

---

## What was implemented

1. **`agents/voice/live_token.py` (NEW)** — the ephemeral-token mint helper.
   - `mint_ephemeral_token(*, settings, http_client, ...)` → `POST v1alpha/auth_tokens`
     with header `x-goog-api-key: <GEMINI_API_KEY>` and body **only**
     `{uses:1, expireTime, newSessionExpireTime}` (gotcha 1 — no
     `bidiGenerateContentSetup`). Returns a typed `EphemeralTokenResponse` whose
     `ephemeral_token_name` is validated to start with `auth_tokens/` (gotcha 2);
     a malformed/`!=200`/missing-key response **fails loud** with `RuntimeError`
     (Rule 12). Key read from `Settings.gemini_api_key`, never logged (only the
     prefix `auth_tokens/` is logged), never returned to the client.
   - Pydantic models `EphemeralTokenRequestBody` / `EphemeralTokenResponse`, plus
     pure `build_token_request_body()` (the unit-testable body-shape seam).
   - `httpx.AsyncClient` injectable for testing (MockTransport).

2. **`agents/worker/main.py` (EXTENDED, additively)** — added ONE route
   `POST /api/voice/live-token` + the import line. No existing route/symbol
   touched. The route delegates to `mint_ephemeral_token()`; on success → 200 with
   the typed body, on mint failure → **HTTP 502** (fail loud — unlike the Q&A
   endpoint, a missing token has no in-conversation fallback). Added
   `HTTPException` to the existing `fastapi` import.

3. **`src/lib/voice/audio.ts` (NEW)** — PCM helpers + device wrappers:
   - Pure: `downsampleTo16kHz` (linear-interp resample to exactly 16 kHz — gotcha
     5, the most error-prone bit), `floatToPcm16`/`pcm16ToFloat` (clamped, no
     overflow), `base64FromPcm16`/`pcm16FromBase64` (chunked, no `Buffer` dep).
   - `createMicCapture` (ScriptProcessorNode → downsample → PCM16 → base64, plus
     RMS amplitude for the SP4 waveform) and `createPcmPlayer` (24 kHz ring-buffer
     scheduler, ≥2-chunk lead, gap-free).

4. **`src/lib/voice/useGeminiLive.ts` (NEW)** — the parameterized raw-WSS hook
   `useGeminiLive({ systemInstruction, tools, onTranscript, onToolCall, voiceName?,
   model?, greetingNudge? })`. Implements gotchas 2–7: constrained endpoint via
   `?access_token=`, `setup` frame + wait-for-`setupComplete` gate before any
   audio, greeting nudge (`clientContent` `turnComplete:true`), 16 kHz in / 24 kHz
   out via `audio.ts`, `string|Blob|ArrayBuffer` frame normalization, function
   round-trip `{toolResponse:{functionResponses:[{id,name,response}]}}`, and a
   double-connect guard (in-mount boolean + a connect-epoch that defeats the
   React-19 StrictMode mount→unmount→remount double-socket). Exported pure seams
   `buildSetupFrame` and `normalizeFrameToText`.

5. **Tests (NEW)**:
   - `tests/agents/voice/test_live_token.py` (9 tests, pytest + httpx MockTransport).
   - `tests/lib/voice/audio.test.ts` (10 tests, vitest).
   - `tests/lib/voice/useGeminiLive.test.tsx` (8 tests, vitest + a fake WebSocket).

## Files created / modified (paths relative to repo root)
- `agents/voice/live_token.py` (new)
- `agents/worker/main.py` (modified — additive: 1 import + 1 route)
- `src/lib/voice/audio.ts` (new)
- `src/lib/voice/useGeminiLive.ts` (new)
- `tests/agents/voice/test_live_token.py` (new)
- `tests/lib/voice/audio.test.ts` (new)
- `tests/lib/voice/useGeminiLive.test.tsx` (new)

## Divergences from the plan
- None to the contract. Two implementation notes:
  - **StrictMode single-socket** required a connect-**epoch** ref (not a boolean):
    React preserves hook refs across the StrictMode simulated remount, so only an
    epoch bumped on teardown can tell the torn-down first connect apart from the
    surviving one after the async token mint. Verified by test.
  - **Worker route returns 502 on failure** (not the Q&A endpoint's graceful
    HTTP-200 refusal) — deliberate: no token = the client cannot open the WSS at
    all, so an explicit error is the honest signal (Rule 12). Documented in the
    route docstring + asserted by a test.

## Code-review findings + fixes (Step B/C)
- **[Critical] StrictMode opened 2 sockets** (first hook impl + first test). Root
  cause: per-mount boolean guard + a teardown that reset it, with shared refs
  across the StrictMode remount. **Fixed** with the connect-epoch mechanism;
  re-verified the test now sees exactly 1 socket.
- **[High] `instanceof ArrayBuffer` unreliable across realms** (failed in jsdom;
  would also bite real WebView binary frames). **Fixed** `normalizeFrameToText`
  to duck-type via `Object.prototype.toString` + `ArrayBuffer.isView`.
- **[Medium] TDZ / stale-closure risk**: `connect` referenced `startMicAndGreeting`
  before its declaration and omitted it from deps. **Fixed** by moving
  `startMicAndGreeting` + the epoch ref above `connect` and adding it to the deps.
- **[Low] biome import-order + formatting + one `useOptionalChain`** — fixed via
  `biome check --write` + a manual optional-chain rewrite on the setupComplete
  gate. **[Low] ruff format** on the two Python files — applied `ruff format`.
- Security: confirmed the API key never reaches the client (worker returns only
  `auth_tokens/...`) and is never logged (only the prefix is logged); asserted by
  `test_mint_sends_key_only_in_header_never_in_body`.

## Validation results (exact)
- **Python pytest** (new + regression) — **re-run with cwd = the worktree** after
  catching that `sys.path[0]` (cwd) had resolved `agents` to the *main* worktree;
  verified `agents.worker.main.__file__` points at this worktree and both routes
  (`/api/story/{story_id}/question` + `/api/voice/live-token`) are registered:
  - `tests/agents/voice/test_live_token.py`: **9 passed**.
  - `tests/agents/qa/test_worker.py` (additive-route regression): **5 passed**.
  - full `tests/agents`: **226 passed**, 0 failed.
- **Vitest**: voice files 18 passed; **full suite 154 passed / 19 files**, 0 failed.
- **`tsc --noEmit`** (full project): **0 errors** in my files (and 0 overall once
  `remotion/node_modules` was symlinked — see Concerns).
- **biome check** `src/lib/voice tests/lib/voice`: **clean** (0 errors/warnings).
- **ruff check + ruff format --check** on the 3 Python files: **clean**.

## Definition of done (per item)
- Token endpoint returns 200 with a `.name` starting `auth_tokens/` (Gemini mocked)
  — **PASS** (`test_route_returns_200_with_auth_tokens_name` +
  `test_mint_returns_auth_tokens_name_on_success`).
- Hook sends a well-formed `setup` frame and waits for `setupComplete` before any
  audio — **PASS** (`opens the CONSTRAINED endpoint and sends setup BEFORE any
  audio/greeting`: asserts setup count = 1, realtimeInput/clientContent = 0 until
  `setupComplete`, then the greeting fires).
- Downsamples mic input to 16 kHz — **PASS** (`downsampleTo16kHz` tests: 48 kHz and
  44.1 kHz → 16000 samples; no upsample; DC preserved).
- Replies to a `toolCall` with `{toolResponse:{functionResponses:[{id,name,response}]}}`
  — **PASS** (exact-shape assertion in the hook test).
- Mounting twice (StrictMode) opens exactly ONE socket — **PASS**
  (`opens EXACTLY ONE socket under React 19 StrictMode double-mount`).

## Concerns for the orchestrator

1. **`useGeminiLive` exported signature (phase-3b consumes this):**
   ```ts
   useGeminiLive({
     systemInstruction: string,
     tools?: GeminiToolDeclaration[],        // { name, description, parameters? }
     onTranscript?: (t: { role: "user"|"model"; text: string }) => void,
     onToolCall?: (c: GeminiToolCall) => Promise<Record<string,unknown>> | Record<string,unknown>,
                                              // GeminiToolCall = { id, name, args }
     voiceName?: string,                      // default "Charon"
     model?: string,                          // default gemini-2.5-flash-native-audio-preview-12-2025
     greetingNudge?: string,
   }) => {
     status: "idle"|"connecting"|"live"|"closed"|"error",
     isSetupComplete: boolean,
     inputAmplitude: number,                  // RMS 0..1, for the SP4 waveform
     connect: () => Promise<void>,            // MUST be called inside a user gesture
     disconnect: () => void,
   }
   ```
   - `connect()` mints the token, opens the WSS, runs setup, and (after
     `setupComplete`) requests the mic via `navigator.mediaDevices.getUserMedia`
     and sends the greeting. 3b should call `connect()` from the orb-tap gesture
     (iOS audio/mic unlock) — not on mount.
   - `onToolCall`'s returned object becomes the `response` in the tool round-trip;
     3b wires this to its grounded-answer tool.
   - `inputAmplitude` is exposed for SP4's `Waveform`; model-output amplitude is
     not surfaced yet (the player owns the 24 kHz stream) — flag if SP4 needs it.

2. **`GEMINI_API_KEY` gate:** the live token-mint route can only be **smoke-tested
   end-to-end once `GEMINI_API_KEY` is added to `.env`** (per memory
   `news20-m2-2b-2c-gated-state` the key/worker are not yet live). All tests mock
   the Gemini HTTP call, so the contract is verified, but a real `auth_tokens/`
   round-trip + a real WSS open is unverified until the key + a deployed worker
   exist. `.env.example` already documents `GEMINI_API_KEY`; no new env var added.

3. **Worktree environment note:** this worktree has no `node_modules` (gitignored,
   not copied into worktrees). I temporarily symlinked `node_modules` and
   `remotion/node_modules` from the main worktree to run vitest/tsc/biome, then
   **removed both symlinks** — `git status` now shows only the intended changes.
   Without the `remotion/node_modules` symlink, `tsc` reports pre-existing
   `Cannot find module 'remotion'` errors in `remotion/**` (unrelated to this
   sub-phase; 0 errors with it symlinked). If the orchestrator typechecks in this
   worktree, ensure deps are installed/symlinked or scope tsc to exclude
   `remotion/`.

4. **Python test invocation:** run pytest with **cwd = the worktree root** (or put
   the worktree first on `PYTHONPATH` *and* clear the cwd `sys.path[0]`). The repo
   uses a top-level `agents` package with no install, so a stray cwd (e.g. the main
   worktree) silently imports the wrong `agents` — which made an initial run look
   green against the main worktree's code. Confirmed correct after re-running from
   the worktree cwd.

5. **Scope:** did not touch `agents/qa/*`, `agents/pipeline/*`, `agents/rag/*`,
   `src/components/voice/*` (SP4), or any phase-2b file. The `main.py` edit is
   purely additive; nothing else there needed changing.
