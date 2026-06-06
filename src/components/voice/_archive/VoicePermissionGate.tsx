"use client";

/**
 * VoicePermissionGate — the mic-permission gate for in-news Voice mode (phase-3b
 * SP1; port-map §2 row 7, §5, §6).
 *
 * **What this is.** A gate that stands in front of Voice mode and resolves the
 * mic-permission question *before* any socket opens. It ports the prototype's
 * `voicePermission` → `voiceMicDenied` flow, replacing the prototype's
 * `localStorage("n20-mic")` fake with the real browser permission result
 * ({@link requestMicPermission} / {@link getMicPermissionState}).
 *
 * **State → UI contract:**
 * | gate state    | what renders                                                    |
 * | ------------- | --------------------------------------------------------------- |
 * | `prompt`      | a calm "enable mic" CTA (the request fires on TAP, never mount)  |
 * | `granted`     | the gate resolves: renders `children` + fires `onGranted` once    |
 * | `denied`      | the `voiceMicDenied` calm text-fallback CTA → Detail Q&A          |
 * | `unsupported` | same text-fallback as `denied` (Voice mode can't run here)        |
 *
 * **Gesture rule (port-map §6).** Browsers and the iOS WebView require a user
 * gesture for `getUserMedia`, so the request fires from the CTA's `onClick`, NEVER
 * on mount. On mount we only *read* the current state (no prompt) so an
 * already-granted user skips the CTA and an already-denied user sees the fallback
 * straight away.
 *
 * **This component never opens a socket.** It only gates. The WSS belongs to SP2/3
 * (`useGeminiLive`); this file resolves the gate to `granted` (rendering children /
 * firing `onGranted`) and stops there. The DoD — "never opens the socket before
 * grant" — holds structurally because there is no socket code in this file at all.
 *
 * **Deep-link seam (the controlled prop, not a router route).** The denied/
 * unsupported fallback's CTA calls {@link VoicePermissionGateProps.onOpenTextFallback}.
 * The app has NO router route for Detail — the existing mechanism is the
 * `LayerStack` context (`useLayerStack().openDetail(story)`), which slides in the
 * Detail panel whose pinned `QaComposer` is the "ask by text" surface. SP2 mounts
 * this gate inside `LayerStack` (where it holds the active `Story`) and wires
 * `onOpenTextFallback={() => openDetail(activeStory)}`. Keeping it a callback (not
 * importing `useLayerStack` here) mirrors `VoiceOrb`'s props-in/callbacks-out style
 * and keeps the gate testable in isolation.
 *
 * **Reduced motion (port-map §3.3).** Honoured via an explicit
 * {@link VoicePermissionGateProps.prefers_reduced_motion} prop (matching `VoiceOrb`):
 * when set, the CTA's pulse/transition affordances are suppressed.
 *
 * @example
 * // SP2, mounted inside LayerStack with the active story in hand:
 * <VoicePermissionGate
 *   story_id={activeStory.digest_id}
 *   onGranted={() => setConversationReady(true)}
 *   onOpenTextFallback={() => openDetail(activeStory)}
 *   prefers_reduced_motion={prefersReducedMotion}
 * >
 *   <VoiceConversation story_id={activeStory.digest_id} />
 * </VoicePermissionGate>
 */

import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { logger } from "@/lib/logger";
import { getMicPermissionState, type MicPermissionState, requestMicPermission } from "@/lib/voice/micPermission";

export interface VoicePermissionGateProps {
  /**
   * The active `stories.story_id` slug (the reel `Story.digest_id`) this gate is
   * for — scopes logging and is the story the text fallback deep-links to.
   */
  story_id: string;
  /**
   * The Voice-mode UI to render once the mic is granted. Hidden behind the gate
   * until then so no conversation surface (and no socket) mounts before grant.
   */
  children: ReactNode;
  /**
   * Fired EXACTLY ONCE when the gate resolves to `granted` (the seam SP2 uses to
   * start the conversation / open the WSS). Never fired before a real grant — this
   * is the structural guarantee that the socket can't open pre-grant.
   */
  onGranted?: () => void;
  /**
   * Fired when the user taps the denied/unsupported fallback CTA. SP2 wires this to
   * the existing detail-open mechanism (`useLayerStack().openDetail(activeStory)`)
   * so the user can "read & ask by text instead" via the Detail Q&A composer.
   */
  onOpenTextFallback?: () => void;
  /**
   * When true (`prefers-reduced-motion`), the CTA's motion affordances (pulse /
   * transition) are suppressed — same explicit-prop pattern as {@link VoiceOrb}.
   */
  prefers_reduced_motion?: boolean;
}

/** The calm denial copy ported from the prototype's `voiceMicDenied` fallback. */
const MIC_DENIED_HEADLINE = "Mic access is off";
const MIC_DENIED_BODY = "No problem — you can read this story and ask it questions by text instead.";
const MIC_DENIED_CTA_LABEL = "Read & ask by text";

/** The prompt-state copy: a calm invitation to enable hands-free mode. */
const MIC_PROMPT_HEADLINE = "Talk to this story";
const MIC_PROMPT_BODY = "Enable your mic to ask about this story hands-free. We only listen while you're talking.";
const MIC_PROMPT_CTA_LABEL = "Enable mic";

/**
 * Render the mic-permission gate.
 *
 * On mount it reads the current permission state WITHOUT prompting. From there:
 * `granted` resolves the gate (children + a one-shot `onGranted`); `prompt` shows
 * the enable-mic CTA whose tap triggers the real request; `denied` / `unsupported`
 * show the calm text-fallback CTA that deep-links to Detail Q&A.
 */
export function VoicePermissionGate({
  story_id,
  children,
  onGranted,
  onOpenTextFallback,
  prefers_reduced_motion = false,
}: VoicePermissionGateProps) {
  // `null` = still reading the initial (non-prompting) state on mount.
  const [micPermissionState, setMicPermissionState] = useState<MicPermissionState | null>(null);
  const [isRequesting, setIsRequesting] = useState<boolean>(false);

  // Reason: fire onGranted EXACTLY once per grant — guard against the effect
  // re-running (React 19 StrictMode double-mount) firing it twice.
  const hasFiredGrantedRef = useRef<boolean>(false);

  // Mount: read the current state WITHOUT prompting (no gesture yet). A stale-guard
  // drops a late resolution if the story changed before it returned.
  useEffect(() => {
    let isCurrent = true;
    getMicPermissionState().then((state) => {
      if (isCurrent) {
        setMicPermissionState(state);
      }
    });
    return () => {
      isCurrent = false;
    };
  }, []);

  // Resolve the gate exactly once when we reach `granted` (from mount-read OR the
  // request). Kept in an effect so it fires after render commits, never during.
  useEffect(() => {
    if (micPermissionState === "granted" && !hasFiredGrantedRef.current) {
      hasFiredGrantedRef.current = true;
      logger.info("voice_permission_gate_resolved", { story_id, mic_permission_state: "granted" });
      onGranted?.();
    }
  }, [micPermissionState, story_id, onGranted]);

  /**
   * CTA handler: request the mic INSIDE the user gesture (this onClick). On grant
   * the gate flips to `granted` (the effect above fires `onGranted`); on denial /
   * unsupported it flips to the text-fallback state. No socket is opened here.
   */
  const handleEnableMicTap = useCallback(async (): Promise<void> => {
    if (isRequesting) {
      return;
    }
    setIsRequesting(true);
    logger.info("voice_permission_request_tapped", { story_id });
    const { mic_permission_state } = await requestMicPermission();
    setMicPermissionState(mic_permission_state);
    setIsRequesting(false);
  }, [isRequesting, story_id]);

  /** Text-fallback handler: deep-link to Detail Q&A via the controlled callback. */
  const handleOpenTextFallback = useCallback((): void => {
    logger.info("voice_permission_text_fallback_opened", { story_id });
    onOpenTextFallback?.();
  }, [story_id, onOpenTextFallback]);

  // Initial non-prompting read still in flight — render nothing rather than a
  // flash of the CTA that might vanish a tick later if already granted.
  if (micPermissionState === null) {
    return null;
  }

  // Granted: the gate is resolved — render the Voice-mode children. The one-shot
  // onGranted has already fired from the effect above.
  if (micPermissionState === "granted") {
    return <>{children}</>;
  }

  // Denied OR unsupported: the calm read-and-ask-by-text fallback (prototype
  // `voiceMicDenied`), CTA deep-links to Detail Q&A for this story.
  if (micPermissionState === "denied" || micPermissionState === "unsupported") {
    return (
      <div
        data-voice-permission-gate="denied"
        className="flex h-full w-full flex-col items-center justify-center gap-4 px-8 text-center"
      >
        <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-white/40">{MIC_DENIED_HEADLINE}</div>
        <p className="max-w-[300px] font-sans text-[15px] leading-relaxed text-white/70">{MIC_DENIED_BODY}</p>
        <button
          type="button"
          data-voice-permission-cta="text-fallback"
          onClick={handleOpenTextFallback}
          className={`mt-2 grid min-h-[44px] place-items-center rounded-control bg-primary/[0.18] px-6 font-sans text-[14px] font-medium text-[#93b4ff] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60 active:scale-[0.98] ${prefers_reduced_motion ? "" : "transition-transform"}`}
        >
          {MIC_DENIED_CTA_LABEL}
        </button>
      </div>
    );
  }

  // Prompt: the calm enable-mic CTA. The request fires on TAP (gesture), not mount.
  return (
    <div
      data-voice-permission-gate="prompt"
      className="flex h-full w-full flex-col items-center justify-center gap-4 px-8 text-center"
    >
      <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-white/40">{MIC_PROMPT_HEADLINE}</div>
      <p className="max-w-[300px] font-sans text-[15px] leading-relaxed text-white/70">{MIC_PROMPT_BODY}</p>
      <button
        type="button"
        data-voice-permission-cta="enable-mic"
        onClick={handleEnableMicTap}
        disabled={isRequesting}
        className={`mt-2 grid min-h-[44px] place-items-center rounded-control bg-primary px-6 font-sans text-[14px] font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60 active:scale-[0.98] disabled:opacity-50 ${prefers_reduced_motion ? "" : "transition-transform"}`}
      >
        {isRequesting ? "Enabling…" : MIC_PROMPT_CTA_LABEL}
      </button>
    </div>
  );
}
