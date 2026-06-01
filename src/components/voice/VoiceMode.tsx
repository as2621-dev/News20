"use client";

/**
 * VoiceMode — the content of the left lateral Voice layer (phase-3b SP2; port-map
 * §2 row 7, §5).
 *
 * **What this is.** The container `LayerStack` mounts inside its left
 * `motion.aside` (`translateX(-100%) → 0`). It owns the gate → conversation
 * handoff for the active story:
 *   1. it mounts the SP1 {@link VoicePermissionGate} (the mic-permission boundary);
 *   2. until the mic is granted the gate UI shows (prompt / denied fallback);
 *   3. on `onGranted` — fired EXACTLY once, inside the gate's tap gesture — it
 *      renders {@link VoiceConversation}, which opens the Gemini Live socket.
 *
 * **The socket opens ONLY from `onGranted`** (the SP1 structural guarantee): no
 * conversation surface — and therefore no `useGeminiLive.connect()` — mounts
 * before a real grant. This file adds no socket code of its own.
 *
 * **Text fallback (deep-link to Detail Q&A).** A denied/unsupported user is
 * promised "read & ask by text instead". The gate's `onOpenTextFallback` routes
 * to the existing Detail mechanism via the shared {@link useLayerStack} context:
 * `openDetail(story)` slides in the Detail panel (whose pinned Q&A composer is the
 * text surface), then `closeVoice()` dismisses this layer — matching the SP1 seam
 * (`onOpenTextFallback={() => openDetail(activeStory)}`).
 *
 * **`isOpen` gates the live session.** While the layer is closed the parent keeps
 * it mounted (so the slide-out animates) but `inert`/`aria-hidden`; this prop lets
 * `VoiceConversation` tear the socket down when the layer closes WITHOUT relying on
 * a full unmount (the parent keeps the story mounted through the slide-out).
 *
 * **Reduced motion (port-map §3.3).** Read once via framer-motion's
 * {@link useReducedMotion} and forwarded to both the gate and the conversation
 * (the orb/waveform suppress their animation under it), matching `VoiceOrb` /
 * `VoicePermissionGate`'s explicit-prop pattern.
 *
 * Seams left for later sub-phases live in {@link VoiceConversation} (SP3 `tools` +
 * `onToolCall`; SP4 signals / persistence / quota / `ended`) — this file only does
 * the gate→conversation wiring and the active story scope.
 *
 * @example
 * // Mounted by LayerStack inside the left lateral motion.aside:
 * <VoiceMode story={openVoiceStory} isOpen={isVoiceOpen} prefers_reduced_motion={prm} />
 */

import { useReducedMotion } from "framer-motion";
import { useCallback, useMemo, useState } from "react";
import { useLayerStack } from "@/components/shell/LayerStackContext";
import { VoiceConversation } from "@/components/voice/VoiceConversation";
import { VoicePermissionGate } from "@/components/voice/VoicePermissionGate";
import { logger } from "@/lib/logger";
import {
  askAboutStoryDeclaration,
  buildAskAboutStoryHandler,
  STORY_QA_TOOL_GROUNDING_CLAUSE,
} from "@/lib/voice/storyQaTool";
import type { Story } from "@/types/feed";

export interface VoiceModeProps {
  /**
   * The story this Voice session is scoped to (the reel's active story, captured
   * when the layer opened). Scopes the Gemini Live system instruction + greeting
   * to this `digest_id`, and is the story the text fallback deep-links to.
   */
  story: Story;
  /**
   * Whether the parent Voice layer is currently open. False while it slides out
   * (still mounted): {@link VoiceConversation} reads this to disconnect the socket
   * on close even though the story stays mounted through the animation.
   */
  isOpen: boolean;
  /**
   * Forwarded `prefers-reduced-motion` (read once by the parent shell). Suppresses
   * the gate CTA + orb/waveform motion — same explicit-prop pattern as `VoiceOrb`.
   * Optional: when omitted this component reads it itself via `useReducedMotion`.
   */
  prefers_reduced_motion?: boolean;
}

/**
 * Render the Voice layer body: the mic gate, then (on grant) the live conversation.
 *
 * Holds a single `isConversationReady` flag flipped only by the gate's one-shot
 * `onGranted`. The conversation (and its socket) is mounted strictly behind that
 * flag, so the "never opens the socket before grant" DoD holds structurally here
 * too — there is no path to `VoiceConversation` except through a real grant.
 */
export function VoiceMode({ story, isOpen, prefers_reduced_motion }: VoiceModeProps) {
  // Prefer the forwarded value (the shell already read it once); fall back to a
  // local read so the component is usable standalone (e.g. in isolation tests).
  const localReducedMotion = useReducedMotion();
  const prefersReducedMotion = prefers_reduced_motion ?? Boolean(localReducedMotion);

  const { openDetail, closeVoice } = useLayerStack();
  const [isConversationReady, setIsConversationReady] = useState<boolean>(false);

  // The grounded-answer round-trip handler for THIS story (SP3). Memoized on the
  // story id so `useGeminiLive` isn't handed a fresh `onToolCall` identity each
  // render (an unstable handler would re-trigger the hook's effects). The
  // declaration + grounding clause are module constants (no per-story state).
  const handleAskAboutStory = useMemo(() => buildAskAboutStoryHandler(story.digest_id), [story.digest_id]);

  /** The gate's one-shot grant seam: reveal the live conversation (opens the WSS). */
  const handleGranted = useCallback((): void => {
    logger.info("voice_mode_conversation_ready", { story_id: story.digest_id });
    setIsConversationReady(true);
  }, [story.digest_id]);

  /**
   * The denied/unsupported text-fallback seam: deep-link to Detail Q&A for this
   * story, then dismiss the Voice layer (matches the SP1-specified wiring).
   */
  const handleOpenTextFallback = useCallback((): void => {
    logger.info("voice_mode_text_fallback_opened", { story_id: story.digest_id });
    openDetail(story);
    closeVoice();
  }, [story, openDetail, closeVoice]);

  return (
    <div data-voice-mode={story.digest_id} className="relative flex h-full w-full flex-col bg-background">
      <VoicePermissionGate
        story_id={story.digest_id}
        onGranted={handleGranted}
        onOpenTextFallback={handleOpenTextFallback}
        prefers_reduced_motion={prefersReducedMotion}
      >
        {isConversationReady ? (
          // Keyed on the story id so a different story gets a fresh conversation
          // (and a fresh socket scope) rather than reusing a stale one.
          <VoiceConversation
            key={story.digest_id}
            story={story}
            isOpen={isOpen}
            prefers_reduced_motion={prefersReducedMotion}
            toolsSlot={[askAboutStoryDeclaration]}
            onToolCallSlot={handleAskAboutStory}
            tool_grounding_clause={STORY_QA_TOOL_GROUNDING_CLAUSE}
          />
        ) : null}
      </VoicePermissionGate>
    </div>
  );
}
