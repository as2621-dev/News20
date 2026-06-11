"use client";

/**
 * ReelStage — ONE full-viewport story rendered as the Blip Flow Stage-4 reel
 * (prototype `blip-reel.js` `reelHtml()`), emitting the vendored Blip class
 * vocabulary (`.reel/.top/.finite/.cap-wrap/.head/.sp/.ru2/.r3-bottom`) so it
 * picks up `src/styles/blip-flow.css` verbatim. It replaces the legacy
 * `ReelStory` + `ReelChrome` presentation while REUSING their data wiring:
 * {@link useReelAudio} (one `<audio>` per story, only the active one plays) and
 * {@link KaraokeCaption} (the word-by-word caption, unchanged).
 *
 * **Scroll-snap stacking.** The prototype renders ONE `.reel` at `position:
 * absolute; inset:0`. The app feed is a vertical scroll-snap stack of 30 stories,
 * so each ReelStage is wrapped by {@link BlipReel} in a `position:relative`
 * `snap-start` section — the `.reel` then fills ITS section, and the sections
 * tile vertically. The ask sheet + article layer are root singletons in
 * {@link BlipReel}, OVER the active story (matching the prototype's sibling mount).
 *
 * **Play model.** No whole-surface tap-to-pause — the prototype uses an explicit
 * `.play-btn`. The first-tap iOS audio unlock is the TapToStart overlay (owned by
 * {@link BlipReel}); after unlock, the play button toggles. The per-story progress
 * bar is driven inline off the real audio clock (overriding the CSS `storyFill`
 * keyframe) so it tracks what is actually audible.
 *
 * **Pause-under-overlay.** When an ask sheet / article opens over the active
 * story, `isOverlayOpen` flips true → narration pauses; on close it resumes only
 * if it was playing (mirrors the prototype `applyPlayState(false)` ⇄ restore).
 */
import { useReducedMotion } from "framer-motion";
import type { CSSProperties } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { BlipLogo } from "@/components/BlipLogo";
import { ic } from "@/components/blip/reel/icons";
import { KaraokeCaption } from "@/components/reel/KaraokeCaption";
import { captionStateAtTime } from "@/lib/captions/captionState";
import { FEED_START_INDEX, FEED_TOTAL } from "@/lib/reel/feedBriefing";
import { type NextReelState, useReelAudio } from "@/lib/reel/useReelAudio";
import type { AnchorSpeaker, Story } from "@/types/feed";

/** A `--accent` CSS custom property carrier (typed escape from `CSSProperties`). */
type AccentStyle = CSSProperties & { "--accent": string };

/** Legacy iOS Safari inline-audio attribute (forwarded verbatim by React). */
const IOS_INLINE_AUDIO_ATTRS: Record<string, string> = { "webkit-playsinline": "true" };

/** Fixed anchor identity colours (NOT segment accents) — prototype `app.js`. */
const SPEAKER_IDENTITY_COLOR: Record<AnchorSpeaker, string> = {
  ALEX: "#6C8CFF",
  JORDAN: "#C792EA",
};

/** Hardcoded briefing date (no per-story date in the feed contract yet) — matches the prototype chrome. */
const BRIEFING_DATE_LABEL = "THU · MAY 29";

/** Format the finite counter, e.g. `feedPosition 25` → `"26 / 30"`. */
function formatCounter(feedPosition: number): string {
  return `${String(feedPosition + 1).padStart(2, "0")} / ${FEED_TOTAL}`;
}

/** Format an audio clock value in ms as `m:ss` (e.g. `46000` → `"0:46"`). */
function formatClock(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export interface ReelStageProps {
  /** The story to render. */
  story: Story;
  /** This story's 0-based index in the feed. */
  storyIndex: number;
  /** Total feed length (for auto-advance math in the audio hook). */
  storyCount: number;
  /** Whether this is the currently-active (snapped) story. */
  isActive: boolean;
  /** Whether audio has been unlocked by the first user tap (gates auto-play). */
  isAudioUnlocked: boolean;
  /** Whether this story's audio should be eagerly buffered (`preload="auto"` vs `"none"`). */
  shouldPreload: boolean;
  /** Whether an ask sheet / article is open over the active story (pauses narration). */
  isOverlayOpen: boolean;
  /** Register/deregister this story's `playAudio` while active (iOS in-gesture unlock). */
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
  /** Open the type-ask sheet (tap the question field). */
  onOpenType: () => void;
  /** Open the voice-ask sheet (tap the white signal button). */
  onOpenVoice: () => void;
  /** Open the full-article layer (tap the headline). */
  onOpenArticle: () => void;
}

/**
 * Render a single story as the Stage-4 reel. The `[data-story-index]` attribute
 * is the snap target the active-index observer in {@link BlipReel} watches.
 */
export function ReelStage({
  story,
  storyIndex,
  storyCount,
  isActive,
  isAudioUnlocked,
  shouldPreload,
  isOverlayOpen,
  onRegisterActivePlay,
  onAudioEnded,
  isSaved,
  isFollowed,
  onToggleSave,
  onToggleFollow,
  onOpenType,
  onOpenVoice,
  onOpenArticle,
}: ReelStageProps) {
  const prefersReducedMotion = useReducedMotion();

  // Reason: a broken poster URL fails silently on <img>; track it so the reel
  // falls back to the flat .reel-bg wash instead of an invisible broken image.
  const [posterFailedToLoad, setPosterFailedToLoad] = useState<boolean>(false);

  const audioController = useReelAudio({ storyIndex, storyCount, isActive, onEnded: onAudioEnded });

  // Register this story's play handle while active so the TapToStart overlay can
  // start playback in-gesture (iOS unlock); compare-and-clear on cleanup so a late
  // auto-advance cleanup never clobbers the next active story's registration.
  // biome-ignore lint/correctness/useExhaustiveDependencies: stable callbacks; re-run only on isActive.
  useEffect(() => {
    if (isActive) {
      const playHandle = audioController.playAudio;
      onRegisterActivePlay(playHandle);
      return () => onRegisterActivePlay(null, playHandle);
    }
    return undefined;
  }, [isActive]);

  // Auto-play the active story once audio is unlocked (the hook pauses + rewinds
  // when it goes inactive, so this only ever starts the snapped story).
  // biome-ignore lint/correctness/useExhaustiveDependencies: playAudio is stable; intentionally omitted.
  useEffect(() => {
    if (isActive && isAudioUnlocked) {
      void audioController.playAudio();
    }
  }, [isActive, isAudioUnlocked]);

  // Pause narration while an overlay (ask sheet / article) is open over the active
  // story; resume on close only if it was playing (prototype applyPlayState ⇄ restore).
  const resumeOnOverlayCloseRef = useRef<boolean>(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: stable controller methods; re-run only on overlay/active change.
  useEffect(() => {
    if (!isActive) {
      return;
    }
    if (isOverlayOpen) {
      resumeOnOverlayCloseRef.current = audioController.isPlaying;
      audioController.pauseAudio();
    } else if (resumeOnOverlayCloseRef.current) {
      resumeOnOverlayCloseRef.current = false;
      void audioController.playAudio();
    }
  }, [isOverlayOpen, isActive]);

  // Current speaker from THIS story's clock (drives the speaker chip + colour).
  const currentSpeaker = useMemo(
    () =>
      captionStateAtTime(story.caption_sentences, audioController.currentTimeMs, story.speech_end_ms).current_speaker,
    [story.caption_sentences, story.speech_end_ms, audioController.currentTimeMs],
  );

  const feedPosition = FEED_START_INDEX + storyIndex;
  const progressPercent =
    story.audio_duration_ms > 0
      ? Math.min(100, Math.max(0, (audioController.currentTimeMs / story.audio_duration_ms) * 100))
      : 0;
  const speakerColor = currentSpeaker ? SPEAKER_IDENTITY_COLOR[currentSpeaker] : "rgba(255,255,255,.5)";

  const reelStyle: AccentStyle = { "--accent": story.segment_accent_hex };
  const reelClassName = `reel${audioController.isPlaying ? " playing" : ""}${isOverlayOpen && isActive ? " dimmed" : ""}`;
  const posterStyle: CSSProperties = prefersReducedMotion
    ? {}
    : { animation: "drift 26s ease-in-out infinite alternate" };

  return (
    <section
      data-story-index={storyIndex}
      className="relative h-full w-full shrink-0 snap-start overflow-hidden"
      style={reelStyle}
    >
      <div className={reelClassName}>
        {/* background: flat wash + visible drifting poster + legibility dim + scrims + accent halo */}
        <div className="reel-bg" aria-hidden="true" />
        {story.poster_url !== "" && !posterFailedToLoad ? (
          // biome-ignore lint/performance/noImgElement: decorative full-bleed backdrop in a static export; next/image is inappropriate here.
          <img
            src={story.poster_url}
            alt=""
            aria-hidden="true"
            className="pointer-events-none absolute inset-[-4%] z-0 h-[108%] w-[108%] object-cover"
            style={posterStyle}
            onError={() => setPosterFailedToLoad(true)}
          />
        ) : null}
        <div className="reel-poster-dim" aria-hidden="true" />
        <div className="reel-scrim-top" aria-hidden="true" />
        <div className="reel-halo" aria-hidden="true" />
        <div className="reel-scrim" aria-hidden="true" />

        {/* top chrome: finite bar + wordmark/date + counter + profile */}
        <div className="top">
          <div className="finite">
            {Array.from({ length: FEED_TOTAL }, (_unused, segmentIndex) => {
              let stateClass = "";
              if (segmentIndex < feedPosition) {
                stateClass = " done";
              } else if (segmentIndex === feedPosition) {
                stateClass = " cur";
              }
              return (
                // biome-ignore lint/suspicious/noArrayIndexKey: fixed-length positional bar; index IS the identity.
                <div key={segmentIndex} className={`fseg${stateClass}`} />
              );
            })}
          </div>
          <div className="toprow">
            <div className="brand">
              <BlipLogo size={20} />
              <span className="date">{BRIEFING_DATE_LABEL}</span>
            </div>
            <div className="topright">
              <span className="counter">{formatCounter(feedPosition)}</span>
              <button type="button" className="act" aria-label="Profile" onClick={onOpenType}>
                {ic("profile")}
              </button>
            </div>
          </div>
        </div>

        {/* karaoke caption hero */}
        <div className="cap-wrap">
          <div style={{ width: "100%" }}>
            <KaraokeCaption
              captionSentences={story.caption_sentences}
              currentTimeMs={audioController.currentTimeMs}
              speechEndMs={story.speech_end_ms}
              reduceMotion={Boolean(prefersReducedMotion)}
            />
          </div>
        </div>

        {/* right action rail: save / share / follow */}
        <div className="ru2">
          <button
            type="button"
            className={`um${isSaved ? " on" : ""}`}
            aria-label="Save"
            aria-pressed={isSaved}
            onClick={onToggleSave}
          >
            {ic("save")}
          </button>
          <button type="button" className="um" aria-label="Share" onClick={onOpenType}>
            {ic("share")}
          </button>
          <button
            type="button"
            className={`um${isFollowed ? " follow-on" : ""}`}
            aria-label="Follow"
            aria-pressed={isFollowed}
            onClick={onToggleFollow}
          >
            {ic("following")}
          </button>
        </div>

        {/* headline zone — tap the headline for the full article */}
        <div className="head">
          <div className="seg-chip" style={{ color: story.segment_accent_hex }}>
            <span className="seg-dot" />
            {story.segment_label}
          </div>
          <h1 className="headline">{story.headline}</h1>
          {/* The headline is a heading (non-interactive); the article opens from the
              explicit tap-cue button below + the ask bar, keeping semantics clean. */}
          <button
            type="button"
            className="tap-cue"
            onClick={onOpenArticle}
            style={{ background: "transparent", border: "none", padding: 0, cursor: "pointer" }}
          >
            {ic("arrow")}
            TAP THE HEADLINE FOR THE FULL ARTICLE
          </button>
        </div>

        {/* speaker label + per-story progress + play/pause */}
        <div className="sp">
          <div className="sp-row">
            <span className="seg-chip" style={{ color: "rgba(255,255,255,.8)" }}>
              <span className="seg-dot" style={{ background: speakerColor, boxShadow: `0 0 12px ${speakerColor}` }} />
              {currentSpeaker ?? " "}
            </span>
            <span className="sp-time">
              {formatClock(audioController.currentTimeMs)} / {formatClock(story.audio_duration_ms)}
            </span>
          </div>
          <div className="player-row">
            <button type="button" className="play-btn" aria-label="Play or pause" onClick={audioController.togglePlay}>
              {ic(audioController.isPlaying ? "pause" : "play")}
            </button>
            <div className="story-progress">
              {/* inline width + animation:none — drive the fill off the real audio clock, not the CSS keyframe */}
              <i style={{ width: `${progressPercent}%`, animation: "none" }} />
            </div>
          </div>
        </div>

        {/* ask bar — white signal (voice) + question field (type) */}
        <div className="r3-bottom">
          <div className="r3-row">
            <button type="button" className="sig-btn" aria-label="Ask with your voice" onClick={onOpenVoice}>
              <span className="ring" />
              <span className="ring r2" />
              {ic("voice")}
            </button>
            <button type="button" className="qfield field" aria-label="Type a question" onClick={onOpenType}>
              <span className="q">Ask anything about this story…</span>
              <span className="kbd">{ic("keyboard")}</span>
            </button>
          </div>
          <div className="r3-hint">
            <span className="dot" />
            PRESS TO TALK · OR TYPE YOUR OWN
          </div>
        </div>
      </div>

      {/* this story's audio element — iOS inline; only the active story plays */}
      <audio
        ref={audioController.audioRef}
        src={story.digest_audio_url}
        preload={shouldPreload ? "auto" : "none"}
        playsInline
        {...IOS_INLINE_AUDIO_ATTRS}
      >
        <track kind="captions" />
      </audio>
    </section>
  );
}
