# Phase 3b SP1 — Mic permission gate + denied fallback — Execution Report

**Status:** SUCCESS
**Date:** 2026-05-31
**Scope:** Sub-phase 1 of `phase-3b-m3-in-news-voice-mode` only. No commit (orchestrator commits at phase end).

## What was implemented

A mic-permission gate for in-news Voice mode, in two pieces:

1. **`src/lib/voice/micPermission.ts`** (NEW) — a pure, typed permission helper wrapping the **web-standard** browser APIs (no `@capacitor/*`):
   - `MicPermissionState = "granted" | "denied" | "prompt" | "unsupported"` and `MicPermissionResult { mic_permission_state }`.
   - `isMicCaptureSupported()` — guards SSR / insecure-context / old-WebView (`navigator.mediaDevices.getUserMedia` absent).
   - `getMicPermissionState()` — reads current state via `navigator.permissions.query({ name: "microphone" })` **without prompting**; falls back to `"prompt"` when the Permissions API is absent but capture is supported; `"unsupported"` when no capture API.
   - `requestMicPermission(getUserMediaImpl?)` — triggers the prompt via `getUserMedia({ audio: true })`, **stops the obtained stream's tracks immediately** (only confirms the grant; SP3's `useGeminiLive` opens its own capture stream), returns `"granted"`; any rejection → `"denied"`; missing API → `"unsupported"`. Never throws. Injectable `getUserMediaImpl` for mock testing.
   - Structured JSON logging with `fix_suggestion` on every warn/error path.

2. **`src/components/voice/VoicePermissionGate.tsx`** (NEW) — the gate, controlled/composable in the `VoiceOrb` style (props in, callbacks out, explicit `prefers_reduced_motion` prop, double quotes, design tokens only):
   - On mount, reads state **without prompting**; `null` initial state renders nothing (no CTA flash).
   - **prompt:** calm "Enable mic" CTA; the request fires **on tap (user gesture)**, never on mount.
   - **granted:** renders `children` and fires `onGranted` **exactly once** (StrictMode-safe via a ref guard) — the SP2 socket-open seam. No socket code exists in this file.
   - **denied / unsupported:** the calm `voiceMicDenied` text fallback ("read & ask by text instead") with a CTA that calls `onOpenTextFallback` to deep-link to Detail Q&A.
   - Replaces the prototype's `localStorage("n20-mic")` with the live permission API as the source of truth.

3. **`tests/lib/voice/voicePermissionGate.test.tsx`** (NEW) — 10 tests, all mocking `getUserMedia` / `navigator.permissions` at the boundary (no real mic/socket). Location matches the ph3 voice tests under `tests/lib/voice/` and is inside biome's `tests/lib/**` include.

## Files created
- `src/lib/voice/micPermission.ts`
- `src/components/voice/VoicePermissionGate.tsx`
- `tests/lib/voice/voicePermissionGate.test.tsx`

No files modified. No files outside the allowed list touched.

## Divergences from the plan (and why)

1. **Web-standard mic API, no `ios/` / `Info.plist` / `@capacitor/*`** — per the owner-approved scope decision (2026-05-31): phase-1c never ran, so there is no native platform. Used `navigator.mediaDevices.getUserMedia` + `navigator.permissions.query`, which is portable into the future Capacitor WebView. The iOS `NSMicrophoneUsageDescription` string remains the documented phase-1c follow-up (not added).

2. **`unsupported` state added** beyond the plan's prompt/granted/denied trio. SSR / insecure-context / old-WebView have no capture API; rather than mislabel that as `"denied"` and risk a misleading log, it's a distinct typed state that renders the **same** text fallback as `denied`. This keeps "never throw" honest and the UX identical.

3. **Deep-link via a controlled `onOpenTextFallback` callback, not a direct `useLayerStack()` call.** The app has **no router route** for Detail — the existing mechanism is `useLayerStack().openDetail(story: Story)` (in `src/components/shell/LayerStackContext.tsx`), which slides in the Detail panel whose pinned `QaComposer` (`src/components/detail/QaComposer.tsx`) is the "ask by text" surface. `openDetail` needs the full `Story`, which the gate does not hold (it only takes `story_id` per the plan). The gate therefore exposes `onOpenTextFallback` as a callback and SP2 — which mounts the gate inside `LayerStack` and holds the active `Story` — wires `onOpenTextFallback={() => openDetail(activeStory)}`. This mirrors `VoiceOrb`'s props-in/callbacks-out style, keeps the gate decoupled + unit-testable, and reuses the existing seam rather than inventing a route (Rule 2/3).

## Code-review findings + fixes (Step B/C)
- **PermissionName cast** (`"microphone" as PermissionName`) — `"microphone"` is valid at runtime but missing from the lib.dom union in this TS version. Documented with a `// Reason:` comment; not an `any`. No fix needed.
- **`onGranted` effect dep + inline-identity parents** — if a parent passes an inline `onGranted`, the resolve effect re-runs, but the `hasFiredGrantedRef` guard fires it exactly once. Verified by the "fires onGranted ONCE" + "already-granted" tests.
- **Async `onClick` returning a Promise** — React ignores the returned Promise; the `isRequesting` guard blocks double-taps. Acceptable, no fix.
- **Mic released after grant** — `requestMicPermission` stops all tracks post-grant so it doesn't hold the mic open against SP3's own stream. Asserted by the happy-path helper test (tracks stopped). No outstanding critical/high/medium findings.

## Validation results (Step D)
- **`npx tsc --noEmit`:** PASS (exit 0, no errors).
- **`npx biome check .`:** PASS — 85 files checked, no fixes/errors (also ran scoped on the 3 new files: clean).
- **`npx vitest run` (full suite):** PASS — **22 test files, 195/195 tests passing**, including the **10 new** tests in `voicePermissionGate.test.tsx`. No regressions. (`--localstorage-file` warnings are pre-existing Node noise, unrelated.)

Test coverage of the three required cases:
1. **Happy path grant** → children rendered + `onGranted` fired once; helper returns `granted` + releases the mic.
2. **Denial** → text-fallback CTA rendered, deep-link (`onOpenTextFallback`) invoked on tap, children/`onGranted` never reached (socket never opens).
3. **Edge: missing `navigator.mediaDevices`** → `unsupported`, no throw, same text fallback. Plus: already-granted skips the CTA without prompting; Permissions-API-absent falls back to the CTA.

## Definition of done — PASS
- Granting resolves the gate to "ready" (renders children, fires `onGranted`) and **never opens the socket before grant** — structurally guaranteed (no socket code in this file) and asserted (children/`onGranted` absent in prompt/denied states). PASS.
- Denying renders the text-fallback CTA that deep-links to Detail Q&A (`onOpenTextFallback`). PASS.
- Verifiable against mocked `getUserMedia` / permissions API. PASS.

## Concerns / hand-off for the orchestrator (SP2)
- **SP2 mount seam:** Mount `<VoicePermissionGate>` **inside `LayerStack`** (`src/components/shell/LayerStack.tsx`), in the left lateral Voice layer SP2 adds, where the active `Story` is in scope. Wire:
  - `story_id={activeStory.digest_id}`
  - `onGranted={...}` → start the conversation / open the WSS (`useGeminiLive`). **This is the only place the socket should open** — the gate guarantees it isn't called pre-grant.
  - `onOpenTextFallback={() => openDetail(activeStory)}` (from `useLayerStack()`), and likely also `closeVoice()` so the user lands in Detail, not behind the closing Voice layer.
  - `prefers_reduced_motion={useReducedMotion()}`.
- **Detail-open mechanism reused:** `useLayerStack().openDetail(story: Story)` from `src/components/shell/LayerStackContext.tsx` — opens the Detail lateral layer; its pinned `QaComposer` is the "ask by text" surface. No new route added.
- **Deferred (phase-1c):** iOS `Info.plist` `NSMicrophoneUsageDescription` — required for `getUserMedia` inside the native WebView, but `ios/` doesn't exist yet. Unchanged follow-up; not in this phase.
