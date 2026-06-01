# Phase 3b — Sub-phase 2 execution report: VoiceMode lateral layer + Live wiring

**Status:** SUCCESS
**Date:** 2026-05-31

## What was implemented

The left lateral **Voice** layer, mirroring the existing right Detail layer, and the
gate → live-conversation wiring for the active story:

1. **`LayerStackContext.tsx`** — extended `LayerStackContextValue` with the four
   Voice members (`isVoiceOpen`, `openVoiceStory`, `openVoice(story)`,
   `closeVoice()`), doc-comment style matched to the existing Detail members.
2. **`LayerStack.tsx`** — added the left Voice `motion.aside` (`x: "-100%" → 0`),
   finger-following via `drag="x"` with the same offset/velocity commit thresholds
   as Detail; a thin **right-edge** drag region whose LEFTWARD drag calls
   `openVoice(activeStory)` (prototype `dx < 0 → openVoice`); the reel depth cue
   (`scale(0.94) brightness(0.45)`) now fires when **either** lateral layer is open
   (`isLateralOpen`), reusing the existing scale-back mechanism. The reel stays the
   mounted base layer — never unmounted by a Voice open. Extracted two PURE exported
   deciders `shouldCommitRightwardDrag` / `shouldCommitLeftwardDrag` (mirroring how
   the Detail commit was structured) and routed BOTH the existing Detail handlers
   and the new Voice handlers through them (single source of truth).
3. **`VoiceMode.tsx`** (new) — mounts the SP1 `<VoicePermissionGate>` with the
   specified seam (`story_id`, `onGranted`, `onOpenTextFallback`,
   `prefers_reduced_motion`); on `onGranted` reveals `<VoiceConversation>` behind an
   `isConversationReady` flag (so the socket can't open pre-grant, structurally);
   `onOpenTextFallback` → `openDetail(story)` then `closeVoice()`.
4. **`VoiceConversation.tsx`** (new) — calls `useGeminiLive` configured with a base
   in-news system instruction (`buildInNewsSystemInstruction`, scoped to the active
   story, answer-only-from-sources + clean-refusal, written to lean safe before SP3
   adds the tool-forcing clause), `voiceName="Charon"`, a story-specific
   `greetingNudge` (`buildGreetingNudge`), `onTranscript` → `TranscriptLine`,
   responseModalities AUDIO (via the hook's `buildSetupFrame`). `connect()` runs
   from the open boundary (mounted only after the gate's tap gesture); `disconnect()`
   on close/unmount. Mounts `VoiceOrb` (state via PURE `orbStateForStatus`),
   amplitude-driven `Waveform`, `TranscriptLine`. Orb tap pauses/resumes. Clear SP3
   seams (`toolsSlot`, `onToolCallSlot`, `tool_grounding_clause`) and SP4 seam
   markers (signal/persist/quota/`ended`) are present but unimplemented.

## Files created / modified

- **EDIT** `src/components/shell/LayerStackContext.tsx`
- **EDIT** `src/components/shell/LayerStack.tsx`
- **NEW**  `src/components/voice/VoiceMode.tsx`
- **NEW**  `src/components/voice/VoiceConversation.tsx`
- **NEW**  `tests/lib/voice/voiceMode.test.tsx`

## Divergences

- **`LayerStackContext.tsx` edit (authorized).** Per the sub-phase brief, the
  `LayerStackContextValue` interface lives in this file and had to grow the four
  Voice members. Surgical, additive — no existing member changed.
- **Detail commit handlers re-routed through the new pure deciders.** To satisfy the
  "extract the threshold decision as a PURE exported function like the Detail one"
  requirement without duplicating the rule, I replaced the two inline
  `info.offset.x > … || info.velocity.x > …` expressions in the EXISTING Detail
  handlers with calls to `shouldCommitRightwardDrag`. Behaviour is identical (same
  constants); this is a single-source-of-truth refactor of my own new helper's first
  callers, not a behavioural change to Detail.
- **VoiceConversation lifecycle is `isOpen`-driven, not mount-once.** Self-review
  caught that a pure mount-once `connect()` would (a) not reconnect on re-open and
  (b) — because `useGeminiLive` rebuilds `connect`/`disconnect` identities every
  render from the recomputed instruction string — thrash the socket if those were in
  the deps. Fixed by driving connect/teardown off `[isOpen, story.digest_id]` and
  reading `connect`/`disconnect` through refs. `VoiceMode` keys `VoiceConversation`
  on `story.digest_id` so a story change forces a fresh scope.

## ⚠ Reel-audio competition concern (surfaced, NOT fixed — out of scope)

The existing shell does **NOT** pause reel audio when a lateral layer opens. The reel
narration is driven purely by `isActive && isAudioUnlocked` in `ReelStory.tsx` /
`useReelAudio.ts`; there is no `isDetailOpen`/lateral seam that pauses it. Detail is
text-only so this never mattered. **For Voice it does:** when Voice mode opens, the
reel's `<audio>` narration keeps playing and will compete with the Gemini Live voice.

The brief forbids editing `Reel.tsx`/`ReelStory.tsx`/`useReelAudio.ts` (out of scope;
3d just edited `Reel.tsx`), so I did not touch them. Per the brief's instruction I am
surfacing it here rather than editing reel files out of scope. **Recommended fix
(future sub-phase or a reel-owned change):** have `ReelStory`/`Reel` read
`isVoiceOpen` (or a generic `isLateralOpen`) from `useLayerStack()` and pause the
active story's audio while a Voice layer is open (resume on close), OR have the reel
treat the active story as not-playing while `isVoiceOpen`. The context now exposes
`isVoiceOpen` for exactly this. Until then, on a real device the two audio streams
overlap. (The reel stays mounted, so audio POSITION is preserved per the DoD — this
is purely a "both play at once" concern, not a position-loss one.)

## Self-review findings + fixes

- **[HIGH — fixed]** Socket thrash / no-reconnect from a naive mount-once effect with
  unstable hook callback identities → refactored to ref-backed, `isOpen`-driven
  connect/teardown (see Divergences).
- **[MED — fixed]** Stale conversation if Voice re-opens on a different story →
  `VoiceConversation` keyed on `story.digest_id` in `VoiceMode`.
- **[LOW — noted]** `orbStateForStatus` collapses `error`/`closed`/`connecting` to
  `idle` (still orb). Acceptable for SP2; SP4 adds an explicit `ended`/error surface.

## Validation (exact counts)

- `npx tsc --noEmit` → **PASS** (0 errors)
- `npx biome check .` → **PASS** (88 files checked, 0 findings)
- `npx vitest run` → **PASS** (23 files, **204 tests**; my new file = **9 tests**)
- `npm run build` (Next static export) → **PASS** (compiled + exported, 6/6 static
  pages)

### What the tests assert (Rule 9)

`tests/lib/voice/voiceMode.test.tsx` (9 tests), `useGeminiLive` mocked via `vi.mock`:
1. **Socket boundary** — gate in `prompt` → `connect()` NOT called, no conversation
   surface mounted; on real grant → `useGeminiLive` configured with Charon +
   story-scoped instruction + story greeting nudge, `connect()` called **exactly
   once**, `tools` still undefined (SP3 seam empty).
2. **Commit logic** — `shouldCommitRightwardDrag` / `shouldCommitLeftwardDrag` unit
   tested at the offset/velocity boundaries (at-edge = no-commit; past = commit;
   wrong-direction = never).
3. **Close** — closing the layer (`isOpen → false`) calls `disconnect()` and the reel
   base children remain mounted (not unmounted by a Voice open).
4. Pure config asserts: instruction scope+refusal, SP3 clause append, greeting names
   the story, `orbStateForStatus` mapping.

**UI note (Rule 9):** the framer-motion drag *feel* and the reel scale/brightness
*visual* are device-smoke — not asserted in jsdom. The commit DECISION (the pure
deciders) is unit-tested; the gesture-to-decision plumbing and the CSS transform are
verified by hand on device.

## Definition of done (Sub-phase 2)

**PASS** (with one device-gated caveat):
- Swipe-left opens VoiceMode for the current story — wired (right-edge leftward-drag
  region → `openVoice(activeStory)`); commit logic unit-tested, drag feel device-smoke.
- Layer tracks the finger — `drag="x"` on the `motion.aside` (device-smoke).
- Reel applies the depth cue — `isLateralOpen` drives `scale(0.94) brightness(0.45)`.
- On open `useGeminiLive` sends setup (AUDIO via `buildSetupFrame`, Charon,
  story-scoped instruction) + greeting nudge — asserted in tests.
- Closing returns to the reel without unmounting reel audio — reel stays the mounted
  base; asserted. **Caveat:** reel audio is not *paused* on Voice open (see the
  competition concern above) — position is preserved, but the streams overlap until a
  reel-owned change lands.

## Seams for downstream sub-phases

**SP3 (grounded answer round-trip):**
- Pass the `ask_about_story` declaration + handler into `VoiceConversation` via the
  existing props `toolsSlot` (→ `useGeminiLive` `tools`) and `onToolCallSlot` (→
  `onToolCall`). They are already threaded to the hook; SP3 only fills them (from
  `VoiceMode`, or by wiring `src/lib/voice/storyQaTool.ts`).
- Finalize the forbidding clause via `buildInNewsSystemInstruction`'s third arg
  `tool_grounding_clause` (exposed as the `VoiceConversation` prop of the same name).
  The base instruction already forbids ungrounded answers, so the SP2 tool-less state
  is safe; SP3 hardens it to "never answer without calling the tool."

**SP4 (signals + persistence + quota + ended):**
- The `player_signals` `voice` write + the daily-quota check go at the open boundary
  — the `useEffect(..., [isOpen, story.digest_id])` in `VoiceConversation` marked
  with the SP4 seam comment (one row per session).
- `story_qa` turn persistence + the conversation `ended` state hang off the hook's
  turn lifecycle (`onTranscript` / a future `turnComplete`/`goAway` surface from
  `useGeminiLive`). `orbStateForStatus` is where the `ended` orb state slots in.
- `src/lib/signals.ts` is created by SP4 (3b creates it first per the plan).
