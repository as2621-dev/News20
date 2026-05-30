/**
 * Audio controller for one reel story — the bridge between the real `<audio>`
 * element clock and the karaoke caption + progress UI.
 *
 * **Why the audio clock, not a wall-clock timer (port-map §3.1).** The karaoke
 * captions and the per-story progress bar are both driven off
 * `audioRef.current.currentTime`. A `requestAnimationFrame` loop is used ONLY to
 * *sample* `currentTime` each frame; it never *accumulates* elapsed time. Wall-
 * clock accumulation drifts against the audio (buffering, rate changes, the OS
 * throttling rAF) — sampling the element's own clock cannot drift, because it IS
 * the audio's position. The sampled value (`currentTimeMs`) is fed verbatim to
 * `captionStateAtTime` so the word lighting tracks what is actually audible.
 *
 * **Auto-advance.** When the audio fires its native `ended` event, the reel must
 * advance to the next story — or, on the last story, signal "all caught up". That
 * decision is extracted into the PURE {@link computeNextReelState} so it is unit-
 * testable without a real `<audio>` element (jsdom cannot drive playback). The
 * hook merely wires the `ended` event to it and calls back.
 *
 * Only the ACTIVE story's hook should be `is_active`; inactive stories pause +
 * reset so a single narration plays at a time (Reel.tsx enforces this).
 */

import type { RefObject } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { logger } from "@/lib/logger";

/** The outcome of an auto-advance decision when a story's audio ends. */
export interface NextReelState {
  /** The story index to move to next (clamped to the last index when caught up). */
  nextIndex: number;
  /** True when the ended story was the LAST one — the reel reached the finish line. */
  isCaughtUp: boolean;
}

/**
 * Decide where the reel goes when the story at `currentIndex` finishes.
 *
 * PURE and exported as the unit-testable seam (the `ended` event handler in
 * {@link useReelAudio} is the only caller). The invariant that matters
 * (Rule 9): auto-advance must reach the caught-up state EXACTLY at the last
 * story — never skip a story, never loop back to the start, never report
 * caught-up early.
 *
 * @param currentIndex - 0-based index of the story whose audio just ended.
 * @param storyCount - Total number of stories in the feed (≥ 1).
 * @returns The next index and whether the finish line was reached.
 *
 * @example
 * computeNextReelState(0, 5); // { nextIndex: 1, isCaughtUp: false }
 * computeNextReelState(4, 5); // { nextIndex: 4, isCaughtUp: true }  (last story)
 * computeNextReelState(0, 1); // { nextIndex: 0, isCaughtUp: true }  (only story)
 */
export function computeNextReelState(currentIndex: number, storyCount: number): NextReelState {
  const lastIndex = storyCount - 1;
  // Reason: at (or past) the last story there is nowhere to advance to — this is
  // the finish line. Clamp nextIndex to the last index so callers that still read
  // it (e.g. to keep the last story mounted) get a valid, in-range value.
  if (currentIndex >= lastIndex) {
    return { nextIndex: lastIndex, isCaughtUp: true };
  }
  return { nextIndex: currentIndex + 1, isCaughtUp: false };
}

/** What {@link useReelAudio} hands back to a `ReelStory`. */
export interface ReelAudioController {
  /** Bind to the `<audio ref>` so the hook can read its clock + drive play/pause. */
  audioRef: RefObject<HTMLAudioElement | null>;
  /** The sampled audio position in ms (per rAF) — feeds the karaoke + progress. */
  currentTimeMs: number;
  /** True while the audio element is playing (mirrors the `play`/`pause` events). */
  isPlaying: boolean;
  /** Start playback (used by the first-tap unlock and tap-to-resume). Resolves once `play()` settles. */
  playAudio: () => Promise<void>;
  /** Pause playback (tap-to-pause). */
  pauseAudio: () => void;
  /** Toggle play/pause — the tap gesture target. */
  togglePlay: () => void;
}

/** Inputs to {@link useReelAudio}. */
export interface UseReelAudioParams {
  /** This story's index in the feed (for advance math + logging). */
  storyIndex: number;
  /** Total feed length (for {@link computeNextReelState}). */
  storyCount: number;
  /** Whether this story is the currently-active one (only the active story plays). */
  isActive: boolean;
  /** Fired on the audio `ended` event with the computed next state (auto-advance). */
  onEnded: (nextState: NextReelState) => void;
}

/**
 * Drive one reel story's audio: sample its clock per frame, expose play/pause,
 * and auto-advance on `ended`.
 *
 * The rAF sampler runs only while `isActive && isPlaying`, so inactive/paused
 * stories cost nothing. On going inactive the element is paused and rewound to 0
 * so re-entering the story restarts its narration (matches the prototype's
 * `resetPlayback`).
 *
 * @example
 * const audio = useReelAudio({ storyIndex: 0, storyCount: 5, isActive, onEnded });
 * // <audio ref={audio.audioRef} ... />  +  audio.currentTimeMs → captions/progress
 */
export function useReelAudio({ storyIndex, storyCount, isActive, onEnded }: UseReelAudioParams): ReelAudioController {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [currentTimeMs, setCurrentTimeMs] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const rafIdRef = useRef<number | null>(null);
  // Reason: keep the latest onEnded in a ref so the `ended` listener effect does
  // not re-subscribe on every render (the callback identity changes upstream).
  const onEndedRef = useRef(onEnded);
  onEndedRef.current = onEnded;

  const playAudio = useCallback(async (): Promise<void> => {
    const audioElement = audioRef.current;
    if (!audioElement) {
      return;
    }
    try {
      await audioElement.play();
    } catch (playError) {
      // Reason: iOS rejects play() outside a user gesture before unlock. This is
      // expected pre-first-tap; log once and let the tap handler retry.
      logger.warn("reel_audio_play_rejected", {
        story_index: storyIndex,
        error_message: playError instanceof Error ? playError.message : "unknown",
        fix_suggestion: "play() must run inside a user-gesture handler until the audio element is unlocked (iOS).",
      });
    }
  }, [storyIndex]);

  const pauseAudio = useCallback((): void => {
    audioRef.current?.pause();
  }, []);

  const togglePlay = useCallback((): void => {
    if (audioRef.current?.paused === false) {
      pauseAudio();
    } else {
      void playAudio();
    }
  }, [pauseAudio, playAudio]);

  // Sample the audio clock each animation frame while active + playing. This is
  // the no-drift karaoke driver: it reads currentTime, never accumulates.
  useEffect(() => {
    if (!isActive || !isPlaying) {
      return;
    }
    const sampleClock = (): void => {
      const audioElement = audioRef.current;
      if (audioElement) {
        setCurrentTimeMs(audioElement.currentTime * 1000);
      }
      rafIdRef.current = requestAnimationFrame(sampleClock);
    };
    rafIdRef.current = requestAnimationFrame(sampleClock);
    return () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
    };
  }, [isActive, isPlaying]);

  // Mirror the element's play/pause/ended state into React + wire auto-advance.
  useEffect(() => {
    const audioElement = audioRef.current;
    if (!audioElement) {
      return;
    }
    const handlePlay = (): void => setIsPlaying(true);
    const handlePause = (): void => setIsPlaying(false);
    const handleEnded = (): void => {
      setIsPlaying(false);
      const nextState = computeNextReelState(storyIndex, storyCount);
      logger.info("reel_audio_ended", {
        story_index: storyIndex,
        next_index: nextState.nextIndex,
        is_caught_up: nextState.isCaughtUp,
      });
      onEndedRef.current(nextState);
    };
    audioElement.addEventListener("play", handlePlay);
    audioElement.addEventListener("pause", handlePause);
    audioElement.addEventListener("ended", handleEnded);
    return () => {
      audioElement.removeEventListener("play", handlePlay);
      audioElement.removeEventListener("pause", handlePause);
      audioElement.removeEventListener("ended", handleEnded);
    };
  }, [storyIndex, storyCount]);

  // When this story stops being active, pause + rewind so re-entry replays it.
  useEffect(() => {
    if (isActive) {
      return;
    }
    const audioElement = audioRef.current;
    if (audioElement) {
      audioElement.pause();
      audioElement.currentTime = 0;
    }
    setCurrentTimeMs(0);
  }, [isActive]);

  return { audioRef, currentTimeMs, isPlaying, playAudio, pauseAudio, togglePlay };
}
