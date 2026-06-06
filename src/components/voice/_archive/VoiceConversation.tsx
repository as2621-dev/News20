"use client";

/**
 * VoiceConversation — the live in-news Voice surface (phase-3b SP2; port-map §2
 * row 7, §5).
 *
 * **What this is.** The hands-free conversation about ONE story. It mounts only
 * AFTER {@link VoiceMode}'s permission gate fires `onGranted` (inside the user's
 * tap gesture), so its `useGeminiLive.connect()` runs under that gesture — the
 * browser/iOS-WebView requirement (port-map §6). It:
 *   - configures {@link useGeminiLive} with the **in-news system instruction**
 *     ({@link buildInNewsSystemInstruction}) scoped to this `story_id`, voice
 *     `Charon`, `responseModalities: AUDIO`, and a story-specific greeting nudge;
 *   - opens the socket exactly once on mount (the gate guarantees the gesture);
 *   - streams transcripts into {@link TranscriptLine};
 *   - mounts the shared {@link VoiceOrb} (state derived from the hook's `status`)
 *     + amplitude-driven {@link Waveform};
 *   - tapping the orb pauses/resumes (the mic-in-orb contract, port-map §5.1);
 *   - tears the socket down on unmount OR when the layer closes (`isOpen` false).
 *
 * **Grounding-oriented instruction NOW (SP3 finalizes the forbidding clause).**
 * SP3 wires the real `ask_about_story` tool + the hard "never answer without
 * calling the tool" clause. Until then the instruction is written to lean SAFE:
 * it already tells the model it is scoped to this single story, must answer only
 * from the story's sources, and must refuse cleanly otherwise — so the
 * intermediate (tool-less) state never invents off-source facts. {@link toolsSlot}
 * + {@link onToolCallSlot} are the explicit props SP3 fills.
 *
 * **Seams (do NOT implement here):**
 *   - **SP3** — pass `tools={[askAboutStoryDeclaration]}` and
 *     `onToolCall={handleAskAboutStory}` (props {@link VoiceConversationProps.toolsSlot}
 *     / {@link VoiceConversationProps.onToolCallSlot}); extend the instruction via
 *     {@link buildInNewsSystemInstruction}'s `tool_grounding_clause` arg.
 *   - **SP4 (implemented)** — at the open boundary this writes ONE
 *     `player_signals` `voice` row ({@link recordVoiceSignal}, once per open) and
 *     enforces the daily Live-session quota ({@link getVoiceQuotaState}): over the
 *     cap it blocks the session with a calm message instead of opening the socket
 *     (Rule 12), and while live it ticks the {@link startVoiceQuotaHeartbeat}
 *     budget. The conversation `ended` surface shows once a live session closes
 *     (status `closed`/`error` after going live — `useGeminiLive` ends the session
 *     internally on `goAway`/error, so there is no separate callback to wire).
 *     Q&A turn persistence to `story_qa` already happens server-side via the M2
 *     `/api/story/{story_id}/question` route the SP3 tool calls — NOT re-done here.
 *
 * @example
 * <VoiceConversation story={activeStory} isOpen prefers_reduced_motion={false} />
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { TranscriptLine, type TranscriptRole } from "@/components/voice/TranscriptLine";
import { type OrbState, VoiceOrb } from "@/components/voice/VoiceOrb";
import { Waveform } from "@/components/voice/Waveform";
import { logger } from "@/lib/logger";
import { getVoiceQuotaState, recordVoiceSignal, startVoiceQuotaHeartbeat } from "@/lib/signals";
import { buildGreetingNudge, buildInNewsSystemInstruction } from "@/lib/voice/storyVoicePrompts";
import {
  GEMINI_LIVE_DEFAULT_VOICE,
  type GeminiLiveStatus,
  type GeminiToolCall,
  type GeminiToolDeclaration,
  useGeminiLive,
} from "@/lib/voice/useGeminiLive";
import type { Story } from "@/types/feed";

// Re-exported so existing test importers (`tests/_archive/voice/*`) keep resolving
// these PURE prompt builders from their original home after the move to
// `@/lib/voice/storyVoicePrompts` (their real source of truth).
export { buildGreetingNudge, buildInNewsSystemInstruction } from "@/lib/voice/storyVoicePrompts";

/**
 * Map the {@link useGeminiLive} connection status to a {@link VoiceOrb} state.
 *
 * PURE + exported (Rule 9). `live` + a paused override resolves the four orb
 * states: a paused live session shows `paused` (still orb); an active live session
 * shows `listening`; `connecting`/`idle` show `idle`; `closed`/`error` rest on
 * `idle` too (the layer is closing). SP4 will add an explicit `ended` surface.
 *
 * @param status - The hook's connection lifecycle status.
 * @param is_paused - Whether the user has tapped the orb to pause.
 * @returns The orb state to render.
 *
 * @example
 * orbStateForStatus("live", false); // "listening"
 * orbStateForStatus("live", true);  // "paused"
 * orbStateForStatus("connecting", false); // "idle"
 */
export function orbStateForStatus(status: GeminiLiveStatus, is_paused: boolean): OrbState {
  if (status === "live") {
    return is_paused ? "paused" : "listening";
  }
  // connecting / idle / closed / error all rest the orb (no live animation).
  return "idle";
}

export interface VoiceConversationProps {
  /** The story this conversation is grounded to (scopes the system instruction). */
  story: Story;
  /**
   * Whether the parent Voice layer is open. When it flips to false the socket is
   * torn down (the layer is closing) even though the parent keeps this mounted
   * through the slide-out animation.
   */
  isOpen: boolean;
  /** Forwarded `prefers-reduced-motion` (suppresses orb/waveform animation). */
  prefers_reduced_motion?: boolean;
  /**
   * **SP3 seam.** The grounded-answer function declarations passed straight to
   * {@link useGeminiLive}'s `tools`. Omitted in SP2 (no tool yet) — the base
   * instruction already forbids ungrounded answers so the tool-less state is safe.
   */
  toolsSlot?: GeminiToolDeclaration[];
  /**
   * **SP3 seam.** The `onToolCall` handler passed to {@link useGeminiLive} — fulfils
   * the grounded-answer round-trip. Omitted in SP2.
   */
  onToolCallSlot?: (toolCall: GeminiToolCall) => Promise<Record<string, unknown>> | Record<string, unknown>;
  /**
   * **SP3 seam.** A clause appended to the system instruction to FORBID answering
   * without calling the grounded-answer tool. Omitted in SP2.
   */
  tool_grounding_clause?: string;
}

/** One streamed transcript line surfaced to {@link TranscriptLine}. */
interface CurrentTranscript {
  /** Which side of the conversation produced it. */
  role: TranscriptRole;
  /** The latest transcribed text. */
  text: string;
}

/**
 * Render the live Voice conversation for one story.
 *
 * Opens the socket once on mount (the gate guarantees the mount is inside a tap
 * gesture), reflects status → orb, streams transcripts, and disconnects on unmount
 * or when `isOpen` flips false.
 */
export function VoiceConversation({
  story,
  isOpen,
  prefers_reduced_motion = false,
  toolsSlot,
  onToolCallSlot,
  tool_grounding_clause,
}: VoiceConversationProps) {
  const [currentTranscript, setCurrentTranscript] = useState<CurrentTranscript | null>(null);
  // The mic-in-orb pause toggle (port-map §5.1): a paused live session shows the
  // still orb. SP4 may persist/quota around this; SP2 keeps it local UI state.
  const [isPaused, setIsPaused] = useState<boolean>(false);
  // SP4: true when this open was blocked because today's Live-session quota is
  // spent — we render the calm cap message instead of opening the socket.
  const [isQuotaBlocked, setIsQuotaBlocked] = useState<boolean>(false);
  // SP4: latched once a live session has actually started, so a later `closed`
  // status can be told apart from the pre-connect `idle` and surfaced as `ended`.
  const [hasBeenLive, setHasBeenLive] = useState<boolean>(false);

  /** Map a hook transcript ({user|model}) to a {@link TranscriptLine} role. */
  const handleTranscript = useCallback((transcript: { role: "user" | "model"; text: string }): void => {
    setCurrentTranscript({
      role: transcript.role === "user" ? "input" : "output",
      text: transcript.text,
    });
  }, []);

  const { status, inputAmplitude, connect, disconnect } = useGeminiLive({
    systemInstruction: buildInNewsSystemInstruction(story.headline, story.digest_id, tool_grounding_clause),
    // SP3 fills these; in SP2 they are undefined (no grounded-answer tool yet).
    tools: toolsSlot,
    onToolCall: onToolCallSlot,
    onTranscript: handleTranscript,
    voiceName: GEMINI_LIVE_DEFAULT_VOICE,
    greetingNudge: buildGreetingNudge(story.headline),
  });

  // Reason: useGeminiLive rebuilds connect/disconnect identities each render (the
  // systemInstruction/greeting strings are recomputed), so depend on STABLE refs
  // here — otherwise the open/teardown effect would thrash the socket on every
  // re-render (a new connect identity → cleanup disconnect → reconnect). Refs keep
  // the latest functions without retriggering the effect; the effect re-runs only
  // on the open boundary (`isOpen`) and the story scope (`story.digest_id`).
  const connectRef = useRef(connect);
  connectRef.current = connect;
  const disconnectRef = useRef(disconnect);
  disconnectRef.current = disconnect;

  // Open the socket while the layer is OPEN; tear it down when it closes or this
  // unmounts. The gate only mounts this component after its tap-gesture grant, so
  // the FIRST connect() (open) runs under that gesture (the WSS requirement). A
  // re-open (isOpen false → true, same mounted instance) reconnects with a fresh
  // token. useGeminiLive's own double-connect guard makes a StrictMode double-mount
  // safe. The story is fixed per keyed mount (VoiceMode keys on digest_id), so the
  // scope can't change underneath an open session.
  //
  // SP4 (open boundary): (1) write exactly ONE `player_signals` `voice` row per
  // open — fire-and-forget so the signal never blocks/breaks the conversation;
  // (2) BEFORE connecting, check the daily Live-session quota — over the cap we
  // block (no connect()) and show the calm cap message (Rule 12); (3) while live,
  // tick the quota heartbeat so today's budget reflects real usage.
  useEffect(() => {
    if (!isOpen) {
      return;
    }

    // Reason: a re-open (isOpen false→true) must start from a clean slate — reset
    // the "went live" latch so a leftover `closed` status from the prior session
    // doesn't flash the `ended` message before the new connect reaches `live`.
    setHasBeenLive(false);

    // (1) One voice engagement signal per open. recordVoiceSignal swallows its own
    // errors (no-op signed-out, logs on failure); the extra .catch is belt-and-
    // suspenders so a fire-and-forget signal can never surface as an unhandled
    // rejection and break the open.
    void recordVoiceSignal(story.digest_id).catch(() => {});

    // (2) Quota gate — block a NEW session once today's hard cap is reached.
    const quota = getVoiceQuotaState();
    if (quota.is_over_quota) {
      logger.warn("voice_session_blocked_over_quota", {
        story_id: story.digest_id,
        seconds_used_today: quota.seconds_used_today,
        fix_suggestion:
          "Daily Live-session cap reached; the session is blocked with a calm message until local midnight resets the tally.",
      });
      setIsQuotaBlocked(true);
      // Reason (Rule 12): do NOT open the socket; surface the calm cap message
      // instead of silently failing. No heartbeat/connect on this open.
      return;
    }
    setIsQuotaBlocked(false);

    logger.info("voice_conversation_connecting", { story_id: story.digest_id });
    void connectRef.current();
    // (3) Accumulate Live-session seconds toward the daily cap while open.
    const stopHeartbeat = startVoiceQuotaHeartbeat();

    return () => {
      // Disconnect on close, unmount, or story change (also the StrictMode unmount
      // — the hook is idempotent). The visual surface stays mounted through the
      // parent's slide-out; only the socket is torn down here.
      logger.info("voice_conversation_disconnecting", { story_id: story.digest_id });
      stopHeartbeat();
      disconnectRef.current();
    };
  }, [isOpen, story.digest_id]);

  // SP4: latch "this session went live" so a later `closed`/`error` status reads
  // as the conversation having ENDED (vs. the pre-connect idle). Surfaced as the
  // calm `ended` region below; the orb itself rests on idle (no `ended` orb state).
  useEffect(() => {
    if (status === "live") {
      setHasBeenLive(true);
    }
  }, [status]);

  /** Mic-in-orb tap: pause/resume the conversation (still orb = paused). */
  const handlePauseToggle = useCallback((): void => {
    setIsPaused((wasPaused) => !wasPaused);
  }, []);

  const orbState = orbStateForStatus(status, isPaused);
  // SP4: the conversation has ENDED once a live session has closed or errored.
  // `useGeminiLive` ends the session internally on a `goAway`/error frame (no
  // separate callback), so a `closed`/`error` status AFTER going live is the end.
  const isEnded = hasBeenLive && (status === "closed" || status === "error");

  // SP4 (Rule 12): the daily Live-session cap is spent — render a calm cap message
  // and DO NOT open the socket. The quota gate above already skipped connect().
  if (isQuotaBlocked) {
    return (
      <div
        data-voice-conversation={story.digest_id}
        data-voice-quota-blocked=""
        className="flex h-full w-full flex-col items-center justify-center gap-4 px-8 text-center"
        style={{ "--accent": story.segment_accent_hex } as React.CSSProperties}
      >
        <p className="text-balance text-base text-white/80">
          You&apos;ve reached today&apos;s voice limit. It resets tomorrow — you can still read and ask about this story
          by text.
        </p>
      </div>
    );
  }

  return (
    <div
      data-voice-conversation={story.digest_id}
      className="flex h-full w-full flex-col items-center justify-center gap-8 px-8"
      style={{ "--accent": story.segment_accent_hex } as React.CSSProperties}
    >
      <VoiceOrb
        orb_state={orbState}
        amplitude_level={inputAmplitude}
        prefers_reduced_motion={prefers_reduced_motion}
        onPauseToggle={handlePauseToggle}
      />
      {/* The amplitude-driven waveform also renders standalone under the orb so the
          signal reads even at a glance (the orb embeds its own inner waveform). */}
      <Waveform
        amplitude_level={inputAmplitude}
        is_active={orbState === "listening"}
        prefers_reduced_motion={prefers_reduced_motion}
      />
      {/* SP4: when the live session has ended (closed/error after going live),
          surface it calmly rather than leaving a dead silent orb (Rule 12). */}
      {isEnded ? (
        <p data-voice-ended="" className="text-balance text-center text-sm text-white/70">
          Conversation ended. Swipe back to the story, or reopen to ask more.
        </p>
      ) : currentTranscript !== null ? (
        <TranscriptLine
          transcript_role={currentTranscript.role}
          transcript_text={currentTranscript.text}
          is_streaming
        />
      ) : null}
    </div>
  );
}
