"use client";

/**
 * BlipReel — the Blip Flow Stage-4 home surface: the audio-first karaoke reel with
 * the unified ASK model (type sheet + voice sheet) and the tap-headline ARTICLE
 * layer, all in the dark Blip palette. It is the route the onboarding flow lands
 * on (`router.push("/")`) and supersedes the legacy `components/reel/Reel` +
 * `components/shell/LayerStack` Detail/Voice lateral-layer model.
 *
 * **What it reuses unchanged.** The proven feed + audio plumbing: the
 * {@link nextReelStatus} machine, {@link useActiveStoryObserver},
 * {@link computePreloadIndices}, {@link getFeed}, the follows persistence, and the
 * loading / tap-to-start (iOS audio unlock) / caught-up / error overlays. Each
 * story renders through {@link ReelStage} (which owns its own `<audio>` via
 * {@link useReelAudio} and the {@link KaraokeCaption}).
 *
 * **Overlay model (the Stage-4 change).** Instead of lateral layers, the ask sheet
 * and article are ROOT singletons positioned over the active story (matching the
 * prototype's sibling mount). One {@link Overlay} state drives which is open; the
 * `.sheet`/`.layer-article`/`.sheet-scrim` slide via their `.on` class. The active
 * `ReelStage` receives `isOverlayOpen` so it pauses narration + dims while a sheet
 * is up, and resumes on close. The active story's accent is cascaded onto the
 * singletons via `--accent` so their seg-dots/halos match.
 *
 * Static-export safe: client-only, `window`-guarded async feed load.
 */
import type { CSSProperties } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { BlipIconDefs } from "@/components/blip/BlipIconDefs";
import { ArticleLayer } from "@/components/blip/reel/ArticleLayer";
import { AskSheet, type AskSheetMode } from "@/components/blip/reel/AskSheet";
import { ReelStage } from "@/components/blip/reel/ReelStage";
import { AllCaughtUp } from "@/components/reel/AllCaughtUp";
import { LoadingSkeleton } from "@/components/reel/LoadingSkeleton";
import { ReelError } from "@/components/reel/ReelError";
import { TapToStart } from "@/components/reel/TapToStart";
import { getFeed } from "@/lib/feed/fixtureFeed";
import { getFollowedStoryIds, toggleFollow } from "@/lib/follows";
import { logger } from "@/lib/logger";
import { useActiveStoryObserver } from "@/lib/reel/gestures";
import { computePreloadIndices } from "@/lib/reel/preload";
import { nextReelStatus, type ReelStatus } from "@/lib/reel/reelStatus";
import type { NextReelState } from "@/lib/reel/useReelAudio";
import type { Story } from "@/types/feed";

/**
 * Which overlay is open over the active story, if any.
 * - `{ kind: "sheet", mode }` — the ask sheet (type or voice composer).
 * - `{ kind: "article" }`     — the full-article layer (tap-headline).
 * - `null`                    — the bare reel.
 */
type Overlay = { kind: "sheet"; mode: AskSheetMode } | { kind: "article" } | null;

/**
 * Mount the Stage-4 reel: load the feed, wire the status machine + audio unlock +
 * save/follow, and render the dark reel with the ask sheet + article singletons.
 */
export function BlipReel() {
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  // Imperative play handle for the ACTIVE story so the TapToStart overlay can
  // start playback SYNCHRONOUSLY inside its tap gesture (iOS audio unlock).
  const activeStoryPlayRef = useRef<(() => Promise<void>) | null>(null);

  const [stories, setStories] = useState<Story[]>([]);
  const [reelStatus, setReelStatus] = useState<ReelStatus>("loading");
  const [isAudioUnlocked, setIsAudioUnlocked] = useState<boolean>(false);
  const [savedDigestIds, setSavedDigestIds] = useState<Set<string>>(() => new Set());
  const [followedDigestIds, setFollowedDigestIds] = useState<Set<string>>(() => new Set());
  // Which ask sheet / article is open over the active story (the Stage-4 overlay).
  const [overlay, setOverlay] = useState<Overlay>(null);

  const activeIndex = useActiveStoryObserver({
    containerRef: scrollContainerRef,
    storyCount: stories.length,
  });
  const currentStory = stories[activeIndex] ?? null;

  // The active story + the next 1–2 (preload window) get <audio preload="auto">.
  const preloadIndexSet = new Set<number>([activeIndex, ...computePreloadIndices(activeIndex, stories.length)]);

  /** Load (or reload) the feed. Success → `tapstart`; failure → `error`. */
  const loadFeed = useCallback((): (() => void) => {
    let isMounted = true;
    getFeed()
      .then((loadedStories) => {
        if (!isMounted) {
          return;
        }
        setStories(loadedStories);
        setReelStatus((current) => nextReelStatus(current, "feed_loaded"));
      })
      .catch((feedError: unknown) => {
        if (!isMounted) {
          return;
        }
        logger.error("reel_feed_load_failed", {
          error_message: feedError instanceof Error ? feedError.message : "unknown",
          fix_suggestion: "Verify getFeed() fixtures bundle (caption JSON imports + public/fixtures assets).",
        });
        setReelStatus((current) => nextReelStatus(current, "feed_failed"));
      });
    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => loadFeed(), [loadFeed]);

  // Hydrate the persisted follow set once the feed has stories (one batched read).
  // Signed-out users resolve to an empty set (no crash).
  useEffect(() => {
    if (stories.length === 0) {
      return;
    }
    let isMounted = true;
    getFollowedStoryIds()
      .then((persistedFollowedStoryIds) => {
        if (isMounted) {
          setFollowedDigestIds(persistedFollowedStoryIds);
        }
      })
      .catch((hydrateError: unknown) => {
        logger.error("reel_follow_hydrate_failed", {
          error_message: hydrateError instanceof Error ? hydrateError.message : "unknown",
          fix_suggestion: "Confirm migration 0005 applied and the follows RLS allows the authed SELECT.",
        });
      });
    return () => {
      isMounted = false;
    };
  }, [stories]);

  /** Register/deregister the ACTIVE story's play handle (compare-and-clear, see ReelStory). */
  const registerActiveStoryPlay = useCallback(
    (play: (() => Promise<void>) | null, previousPlay?: () => Promise<void>): void => {
      if (play === null && previousPlay !== undefined && activeStoryPlayRef.current !== previousPlay) {
        return;
      }
      activeStoryPlayRef.current = play;
    },
    [],
  );

  /** First-tap handler: unlock audio, start the active story in-gesture, → `playing`. */
  const handleStart = useCallback((): void => {
    setIsAudioUnlocked(true);
    logger.info("reel_audio_unlocked", {});
    void activeStoryPlayRef.current?.();
    setReelStatus((current) => nextReelStatus(current, "first_tap"));
  }, []);

  /** Auto-advance: scroll to the next story, or flip to `caughtup` on the last. */
  const handleAudioEnded = useCallback((nextState: NextReelState): void => {
    if (nextState.isCaughtUp) {
      logger.info("reel_reached_caught_up", {});
      setReelStatus((current) => nextReelStatus(current, "reached_caught_up"));
      return;
    }
    const containerElement = scrollContainerRef.current;
    if (containerElement) {
      containerElement.scrollTo({
        top: nextState.nextIndex * containerElement.clientHeight,
        behavior: "smooth",
      });
    }
  }, []);

  /** Replay from the start (caught-up → playing). */
  const handleReplay = useCallback((): void => {
    logger.info("reel_replay_requested", {});
    scrollContainerRef.current?.scrollTo({ top: 0, behavior: "smooth" });
    setReelStatus((current) => nextReelStatus(current, "replay"));
  }, []);

  /** Retry the feed load from the error screen (error → loading). */
  const handleRetry = useCallback((): void => {
    logger.info("reel_feed_retry_requested", {});
    setReelStatus((current) => nextReelStatus(current, "retry"));
    loadFeed();
  }, [loadFeed]);

  const toggleSavedForStory = useCallback((digestId: string): void => {
    setSavedDigestIds((previous) => {
      const next = new Set(previous);
      if (next.has(digestId)) {
        next.delete(digestId);
      } else {
        next.add(digestId);
      }
      return next;
    });
  }, []);

  /** Persist a follow toggle (optimistic flip, write through, reconcile to truth). */
  const toggleFollowedForStory = useCallback((digestId: string): void => {
    setFollowedDigestIds((previous) => {
      const next = new Set(previous);
      if (next.has(digestId)) {
        next.delete(digestId);
      } else {
        next.add(digestId);
      }
      return next;
    });

    toggleFollow(digestId)
      .then((persistedIsFollowed) => {
        setFollowedDigestIds((previous) => {
          const next = new Set(previous);
          if (persistedIsFollowed) {
            next.add(digestId);
          } else {
            next.delete(digestId);
          }
          return next;
        });
      })
      .catch((toggleError: unknown) => {
        logger.error("reel_follow_toggle_failed", {
          story_id: digestId,
          error_message: toggleError instanceof Error ? toggleError.message : "unknown",
          fix_suggestion: "Confirm migration 0005 applied and the follows RLS allows the authed write.",
        });
        getFollowedStoryIds().then((persistedFollowedStoryIds) => {
          setFollowedDigestIds(persistedFollowedStoryIds);
        });
      });
  }, []);

  // ---- overlay open/close (the Stage-4 ask + article plumbing) ----
  const openType = useCallback((): void => setOverlay({ kind: "sheet", mode: "type" }), []);
  const openVoice = useCallback((): void => setOverlay({ kind: "sheet", mode: "voice" }), []);
  const openArticle = useCallback((): void => setOverlay({ kind: "article" }), []);
  const closeOverlay = useCallback((): void => setOverlay(null), []);

  const isOverlayOpen = overlay !== null;
  // Cascade the active story's accent onto the singletons so their seg-dots/halos match.
  const accentStyle: CSSProperties | undefined = currentStory
    ? ({ "--accent": currentStory.segment_accent_hex } as CSSProperties)
    : undefined;

  return (
    <div className="relative h-full w-full overflow-hidden bg-background">
      <BlipIconDefs />

      <div
        ref={scrollContainerRef}
        className="h-full w-full snap-y snap-mandatory overflow-y-scroll overscroll-y-contain [scrollbar-width:none]"
      >
        {stories.map((story, storyIndex) => (
          <ReelStage
            key={story.digest_id}
            story={story}
            storyIndex={storyIndex}
            storyCount={stories.length}
            isActive={storyIndex === activeIndex}
            isAudioUnlocked={isAudioUnlocked}
            shouldPreload={preloadIndexSet.has(storyIndex)}
            isOverlayOpen={isOverlayOpen}
            onRegisterActivePlay={registerActiveStoryPlay}
            onAudioEnded={handleAudioEnded}
            isSaved={savedDigestIds.has(story.digest_id)}
            isFollowed={followedDigestIds.has(story.digest_id)}
            onToggleSave={() => toggleSavedForStory(story.digest_id)}
            onToggleFollow={() => toggleFollowedForStory(story.digest_id)}
            onOpenType={openType}
            onOpenVoice={openVoice}
            onOpenArticle={openArticle}
          />
        ))}
      </div>

      {/* ---- ask sheet + article singletons (over the active story) ---- */}
      {/* scrim is a redundant dismiss target (the sheet has a labelled close button); a
          tabIndex=-1 button keeps it click-dismissable without adding a tab stop. */}
      <button
        type="button"
        className={`sheet-scrim${isOverlayOpen ? " on" : ""}`}
        aria-label="Close"
        tabIndex={-1}
        style={accentStyle}
        onClick={closeOverlay}
      />
      <div className={`sheet${overlay?.kind === "sheet" ? " on" : ""}`} style={{ ...accentStyle, height: "66%" }}>
        {overlay?.kind === "sheet" && currentStory ? (
          <AskSheet story={currentStory} mode={overlay.mode} onClose={closeOverlay} onOpenArticle={openArticle} />
        ) : null}
      </div>
      <div className={`layer-article${overlay?.kind === "article" ? " on" : ""}`} style={accentStyle}>
        {overlay?.kind === "article" && currentStory ? (
          <ArticleLayer story={currentStory} onClose={closeOverlay} />
        ) : null}
      </div>

      {/* ---- reel status overlays (reused from the legacy reel) ---- */}
      {reelStatus === "loading" ? <LoadingSkeleton /> : null}
      {reelStatus === "tapstart" ? <TapToStart onStart={handleStart} /> : null}
      {reelStatus === "caughtup" ? <AllCaughtUp onReplay={handleReplay} /> : null}
      {reelStatus === "error" ? <ReelError onRetry={handleRetry} /> : null}
    </div>
  );
}
