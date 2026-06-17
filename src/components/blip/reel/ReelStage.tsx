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
 * **Play model.** Tapping the reel surface (the transparent `.reel-tap` layer,
 * below the interactive chrome) toggles play/pause and surfaces a center
 * `.reel-tap-indicator`. The first-tap iOS audio unlock is the TapToStart overlay
 * (owned by {@link BlipReel}). There is no bottom audio control — playback progress
 * is shown by the top `.finite` segment bar, whose current segment fills inline off
 * the real audio clock so it tracks what is actually audible.
 *
 * **Pause-under-overlay.** When an ask sheet / article opens over the active
 * story, `isOverlayOpen` flips true → narration pauses; on close it resumes only
 * if it was playing (mirrors the prototype `applyPlayState(false)` ⇄ restore).
 */
import { useReducedMotion } from "framer-motion";
import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { BlipLogo } from "@/components/BlipLogo";
import { ic } from "@/components/blip/reel/icons";
import { KaraokeCaption } from "@/components/reel/KaraokeCaption";
import { DESIGN_BUCKETS } from "@/lib/feedBuckets";
import { type NextReelState, useReelAudio } from "@/lib/reel/useReelAudio";
import type { Story } from "@/types/feed";

/** A `--accent` CSS custom property carrier (typed escape from `CSSProperties`). */
type AccentStyle = CSSProperties & { "--accent": string };

/** Legacy iOS Safari inline-audio attribute (forwarded verbatim by React). */
const IOS_INLINE_AUDIO_ATTRS: Record<string, string> = { "webkit-playsinline": "true" };

/**
 * Format today's date as the briefing chrome label, e.g. `"THU · MAY 29"`.
 * (The feed contract has no per-story date yet, so the briefing is "today".)
 */
function formatBriefingDateLabel(date: Date): string {
  const weekday = date.toLocaleDateString("en-US", { weekday: "short" }).toUpperCase();
  const month = date.toLocaleDateString("en-US", { month: "short" }).toUpperCase();
  return `${weekday} · ${month} ${date.getDate()}`;
}

/** Format the finite counter from the REAL feed position, e.g. `(0, 30)` → `"01 / 30"`. */
function formatCounter(storyIndex: number, storyCount: number): string {
  return `${String(storyIndex + 1).padStart(2, "0")} / ${storyCount}`;
}

export interface ReelStageProps {
  /** The story to render. */
  story: Story;
  /** This story's 0-based index in the feed. */
  storyIndex: number;
  /** Total feed length (for auto-advance math in the audio hook). */
  storyCount: number;
  /** Per-story category accent hexes (feed order) — paints each progress segment its own colour. */
  segmentAccents: string[];
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
  /** Share this story (native share sheet with headline + source link). */
  onShare: () => void;
  /** Open the type-ask sheet (tap the question field). */
  onOpenType: () => void;
  /** Open the voice-ask sheet (tap the white signal button). */
  onOpenVoice: () => void;
  /** Open the full-article layer (tap the headline). */
  onOpenArticle: () => void;
  /** Open the account sheet (tap the blip wordmark). */
  onOpenAccount: () => void;
}

/**
 * Render a single story as the Stage-4 reel. The `[data-story-index]` attribute
 * is the snap target the active-index observer in {@link BlipReel} watches.
 */
export function ReelStage({
  story,
  storyIndex,
  storyCount,
  segmentAccents,
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
  onShare,
  onOpenType,
  onOpenVoice,
  onOpenArticle,
  onOpenAccount,
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
  // Reason: a reel reached by a fast scroll was preload="none" and flips to
  // preload="auto" at the same render it becomes active, so readyState is still
  // HAVE_NOTHING and the first play() loses the load race. Explicitly kick the
  // fetch here when the element hasn't started loading so a `canplay` is
  // guaranteed to fire for useReelAudio's retry-on-ready to hook (phase 7e-1).
  // Guarded on readyState===HAVE_NOTHING so we never restart an already-buffered
  // download or interrupt in-flight playback.
  // biome-ignore lint/correctness/useExhaustiveDependencies: playAudio is stable; intentionally omitted.
  useEffect(() => {
    if (isActive && isAudioUnlocked) {
      const audioElement = audioController.audioRef.current;
      if (audioElement && audioElement.readyState === HTMLMediaElement.HAVE_NOTHING) {
        audioElement.load();
      }
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

  // Drives the current segment's fill off THIS story's real audio clock.
  const progressPercent =
    story.audio_duration_ms > 0
      ? Math.min(100, Math.max(0, (audioController.currentTimeMs / story.audio_duration_ms) * 100))
      : 0;

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
        {/* category-accent tint bleeding into the notch + home-indicator safe areas */}
        <div className="reel-edge-top" aria-hidden="true" />
        <div className="reel-edge-bottom" aria-hidden="true" />

        {/* whole-surface tap target → pause/resume (below the chrome z-layers) */}
        <button type="button" className="reel-tap" aria-label="Pause or resume" onClick={audioController.togglePlay} />

        {/* top chrome: finite bar + wordmark/date (tap → account) + counter */}
        <div className="top">
          <div className="finite">
            {Array.from({ length: storyCount }, (_unused, segmentIndex) => {
              // Past stories full; the current one fills to the live audio clock; future grey.
              const fillPercent = segmentIndex < storyIndex ? 100 : segmentIndex === storyIndex ? progressPercent : 0;
              return (
                // biome-ignore lint/suspicious/noArrayIndexKey: fixed-length positional bar; index IS the identity.
                <div key={segmentIndex} className="fseg">
                  <i style={{ width: `${fillPercent}%`, background: segmentAccents[segmentIndex] }} />
                </div>
              );
            })}
          </div>
          <div className="toprow">
            <button
              type="button"
              className="brand"
              aria-label="Open account"
              onClick={onOpenAccount}
              style={{ background: "transparent", border: "none", padding: 0, cursor: "pointer" }}
            >
              <BlipLogo size={20} />
              <span className="date">{formatBriefingDateLabel(new Date())}</span>
            </button>
            <div className="topright">
              <span className="counter">{formatCounter(storyIndex, storyCount)}</span>
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

        {/* headline zone — tap the headline for the full article */}
        <div className="head">
          {/* right action rail: save / share / follow — anchored just above the
              headline via .ru2's bottom:100% (relative to this .head) */}
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
            <button type="button" className="um" aria-label="Share" onClick={onShare}>
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
          {story.feed_slot_kind === "breaking" ? (
            // Reason: a breaking-tier slot is labeled "Breaking" (yellow accent), not
            // the story's own segment — else a breaking markets story reads as "Markets".
            <div className="seg-chip" style={{ color: DESIGN_BUCKETS.breaking.color }}>
              <span className="seg-dot" />
              Breaking
            </div>
          ) : (
            <div className="seg-chip" style={{ color: story.segment_accent_hex }}>
              <span className="seg-dot" />
              {story.segment_label}
            </div>
          )}
          {/* The headline IS the article tap target (the explicit tap-cue hint
              line was removed) — a button-wrapped heading keeps it reachable. */}
          <button
            type="button"
            aria-label="Open the full article"
            onClick={onOpenArticle}
            style={{ background: "transparent", border: "none", padding: 0, cursor: "pointer", textAlign: "left" }}
          >
            <h1 className="headline">{story.headline}</h1>
          </button>
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
        </div>

        {/* center pause/resume indicator — display-only; shows the play glyph when paused */}
        <div className={`reel-tap-indicator${audioController.isPlaying ? "" : " on"}`} aria-hidden="true">
          {ic(audioController.isPlaying ? "pause" : "play")}
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
