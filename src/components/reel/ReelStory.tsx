"use client";

/**
 * ReelStory — one full-viewport story in the reel: the ambient accent wash + the
 * blurred drifting poster + the scrims (background), with the KaraokeCaption hero
 * and the ReelChrome overlay on top, plus the story's own `<audio>` element.
 *
 * **Owns its own audio controller.** Each story calls {@link useReelAudio}
 * (one `<audio>` per story); only the ACTIVE story's audio plays — the hook
 * pauses + rewinds a story the moment it stops being active, so a single
 * narration is ever audible. The current speaker is derived here from this
 * story's own sampled clock via the pure {@link captionStateAtTime} selector and
 * handed to {@link ReelChrome}.
 *
 * **Tap = pause/play + first-tap unlock (port-map §3.2, §6).** A framer-motion
 * `onTap` (which won't fire while scroll-snapping) toggles play/pause; the very
 * first tap also unlocks audio (iOS muted-autoplay reality) via `onFirstTap`
 * before playing. SP4 will formalize this as a `TapToStart` overlay — here it is
 * a plain tap, as briefed.
 *
 * **Per-story accent cascade (port-map §3.4).** `style={{ "--accent": ... }}` on
 * the root makes the ambient wash, scrims, finite-bar current segment, seg dot
 * and Follow-on tint read `var(--accent)` — the prototype's `setAccent()`, scoped
 * per story.
 *
 * **Background layers:** (1) `.ambient` accent duotone (CSS-drift, reduced-motion
 * safe), (2) a blurred drifting poster `<img>` (recessed photo; drift gated on
 * `useReducedMotion()` in JS), (3) `.reel-scrim-top` + `.reel-scrim`.
 *
 * The `<audio>` carries `playsinline` + `webkit-playsinline` (iOS: never
 * fullscreen).
 */
import { motion, useReducedMotion } from "framer-motion";
import type { CSSProperties } from "react";
import { useEffect, useMemo } from "react";
import { KaraokeCaption } from "@/components/reel/KaraokeCaption";
import { ReelChrome } from "@/components/reel/ReelChrome";
import { captionStateAtTime } from "@/lib/captions/captionState";
import { createTapPlayHandler } from "@/lib/reel/gestures";
import { type NextReelState, useReelAudio } from "@/lib/reel/useReelAudio";
import type { Story } from "@/types/feed";

/** A `--accent` CSS custom property carrier (typed escape from `CSSProperties`). */
type AccentStyle = CSSProperties & { "--accent": string };

/**
 * The non-standard `webkit-playsinline` attribute (legacy iOS Safari) spread onto
 * the `<audio>` element so it never goes fullscreen on older WebViews. Kept as a
 * typed record so no `any`/`@ts-expect-error` is needed (React forwards unknown
 * lowercase-hyphenated attrs verbatim).
 */
const IOS_INLINE_AUDIO_ATTRS: Record<string, string> = { "webkit-playsinline": "true" };

export interface ReelStoryProps {
  /** The story to render. */
  story: Story;
  /** This story's 0-based index in the feed. */
  storyIndex: number;
  /** Total feed length (for auto-advance math in the audio hook). */
  storyCount: number;
  /** Whether this is the currently-active (snapped) story. */
  isActive: boolean;
  /** Whether audio has been unlocked by a first user tap (gates auto-play). */
  isAudioUnlocked: boolean;
  /**
   * Whether this story's audio should be eagerly buffered (`preload="auto"`) vs
   * `"none"`. The reel sets this for the active story + the next 1–2 (the preload
   * window from {@link computePreloadIndices}) for gap-free auto-advance.
   */
  shouldPreload: boolean;
  /** Called on the FIRST tap when audio is still locked (fallback unlock path). */
  onFirstTap: () => void;
  /**
   * Register this story's imperative `playAudio` with the reel WHILE it is active
   * (and `null` when it stops being active), so the TapToStart overlay can start
   * playback synchronously inside its tap gesture (iOS unlock reality). On
   * deregister, `previousPlay` is this story's handle so the reel can
   * compare-and-clear (only forget it if it still points here).
   */
  onRegisterActivePlay: (play: (() => Promise<void>) | null, previousPlay?: () => Promise<void>) => void;
  /** Fired when THIS story's audio `ended` (auto-advance), with the next state. */
  onAudioEnded: (nextState: NextReelState) => void;
  /** Whether this story is saved (lifted state). */
  isSaved: boolean;
  /** Whether this story is followed (lifted state). */
  isFollowed: boolean;
  /** Toggle saved for this story. */
  onToggleSave: () => void;
  /** Toggle followed for this story. */
  onToggleFollow: () => void;
}

/**
 * Render a single story section — the `[data-story-index]` snap target the
 * active-index observer watches. Fills the viewport and snaps to start.
 */
export function ReelStory({
  story,
  storyIndex,
  storyCount,
  isActive,
  isAudioUnlocked,
  shouldPreload,
  onFirstTap,
  onRegisterActivePlay,
  onAudioEnded,
  isSaved,
  isFollowed,
  onToggleSave,
  onToggleFollow,
}: ReelStoryProps) {
  const prefersReducedMotion = useReducedMotion();

  const audioController = useReelAudio({
    storyIndex,
    storyCount,
    isActive,
    onEnded: onAudioEnded,
  });

  // Register this story's play handle with the reel WHILE active so the
  // TapToStart overlay can start playback in-gesture (iOS unlock); deregister it
  // on going inactive so a stale handle never plays the wrong story. The handle
  // is passed on cleanup too so the reel only clears it if it still points HERE
  // (cross-instance effect/cleanup ordering during auto-advance is not
  // guaranteed — compare-and-clear keeps a late cleanup from clobbering the next
  // active story's registration).
  // Reason: playAudio + onRegisterActivePlay are stable useCallbacks; this effect
  // only needs to re-run when active-ness flips, not on their identity churn.
  // biome-ignore lint/correctness/useExhaustiveDependencies: stable callbacks; re-run only on isActive.
  useEffect(() => {
    if (isActive) {
      const playHandle = audioController.playAudio;
      onRegisterActivePlay(playHandle);
      return () => onRegisterActivePlay(null, playHandle);
    }
    return undefined;
  }, [isActive]);

  // Reason: auto-play the active story once audio is unlocked; the hook pauses +
  // rewinds when it goes inactive, so this only ever starts the snapped story.
  // audioController.playAudio is stable (useCallback) — depending on it would
  // re-run this effect on identity churn, not on the state we care about.
  // biome-ignore lint/correctness/useExhaustiveDependencies: playAudio is stable; intentionally omitted.
  useEffect(() => {
    if (isActive && isAudioUnlocked) {
      void audioController.playAudio();
    }
  }, [isActive, isAudioUnlocked]);

  // Current speaker from THIS story's clock (for the chrome's speaker label).
  const currentSpeaker = useMemo(
    () =>
      captionStateAtTime(story.caption_sentences, audioController.currentTimeMs, story.speech_end_ms).current_speaker,
    [story.caption_sentences, story.speech_end_ms, audioController.currentTimeMs],
  );

  const handleTap = (): void => {
    if (!isAudioUnlocked) {
      onFirstTap();
      void audioController.playAudio();
      return;
    }
    audioController.togglePlay();
  };
  const tapHandlers = createTapPlayHandler(handleTap);

  const rootStyle: AccentStyle = { "--accent": story.segment_accent_hex };

  // Reuse the ported `drift` keyframe for the poster only when motion is allowed.
  const posterStyle: CSSProperties = prefersReducedMotion
    ? {}
    : { animation: "drift 26s ease-in-out infinite alternate" };

  return (
    <motion.section
      data-story-index={storyIndex}
      className="relative h-full w-full shrink-0 snap-start overflow-hidden bg-background"
      style={rootStyle}
      {...tapHandlers}
    >
      {/* (1) accent duotone wash */}
      <div className="ambient" aria-hidden="true" />

      {/* (2) blurred, drifting recessed poster */}
      {/* Reason: a raw <img>, not next/image — this is a decorative, heavily-
          blurred atmospheric backdrop in a static export (images.unoptimized);
          next/image's sizing/optimization adds no value to an aria-hidden wash. */}
      {/* biome-ignore lint/performance/noImgElement: decorative blurred backdrop in a static export; next/image is inappropriate here. */}
      <img
        src={story.poster_url}
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute inset-[-12%] h-[124%] w-[124%] object-cover opacity-35 blur-[44px]"
        style={posterStyle}
      />

      {/* (3) scrims */}
      <div className="reel-scrim-top" aria-hidden="true" />
      <div className="reel-scrim" aria-hidden="true" />

      {/* caption hero — centered, nudged slightly below middle (prototype +34px) */}
      <div className="pointer-events-none absolute inset-0 z-[5] flex items-center justify-center px-6">
        <div style={{ transform: "translateY(34px)" }} className="w-full">
          <KaraokeCaption
            captionSentences={story.caption_sentences}
            currentTimeMs={audioController.currentTimeMs}
            speechEndMs={story.speech_end_ms}
            reduceMotion={Boolean(prefersReducedMotion)}
          />
        </div>
      </div>

      {/* overlay chrome */}
      <ReelChrome
        story={story}
        storyIndex={storyIndex}
        currentSpeaker={currentSpeaker}
        currentTimeMs={audioController.currentTimeMs}
        isSaved={isSaved}
        isFollowed={isFollowed}
        onToggleSave={onToggleSave}
        onToggleFollow={onToggleFollow}
      />

      {/* the story's audio element — iOS inline; only the active story plays.
          preload="auto" for the active story + the preload window (next 1–2) so
          auto-advance is gap-free (port-map §6); the rest stay "none". */}
      <audio
        ref={audioController.audioRef}
        src={story.digest_audio_url}
        preload={shouldPreload ? "auto" : "none"}
        playsInline
        {...IOS_INLINE_AUDIO_ATTRS}
      >
        <track kind="captions" />
      </audio>
    </motion.section>
  );
}
