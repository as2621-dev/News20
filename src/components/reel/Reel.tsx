"use client";

/**
 * Reel — the audio-first karaoke reel home surface. A vertical CSS scroll-snap
 * container (one full-viewport story per `snap-start` section) with an explicit
 * status state machine that gates audio behind a first-tap unlock, plays ONLY the
 * snapped story's audio, auto-advances when it ends, and reaches the "all caught
 * up" finish line at the last story.
 *
 * **Vertical nav = CSS scroll-snap (Rule-7 decision, see `gestures.ts`).** The
 * phase file mentions framer swipe up/down, but port-map §3.2 prefers scroll-snap
 * as the most native iOS-WebView feel; we use `snap-y snap-mandatory` here and an
 * `IntersectionObserver` ({@link useActiveStoryObserver}) to derive `activeIndex`.
 * framer is reserved for tap = pause/play (per story) and the future lateral
 * layers.
 *
 * **The reel status state machine (SP4 — full wiring).** {@link ReelStatus} is
 * `'loading' | 'tapstart' | 'playing' | 'caughtup' | 'error'`, advanced ONLY
 * through the pure, exported {@link nextReelStatus} (the unit-testable seam). The
 * flow:
 *   - mount → `loading` ({@link LoadingSkeleton}; the feed loads in an effect);
 *   - feed resolves → `tapstart` ({@link TapToStart} overlay — **no audio yet**,
 *     the iOS muted-autoplay gate);
 *   - first tap → unlock audio + start the active story + `playing`;
 *   - last story's audio `ended` → `caughtup` ({@link AllCaughtUp});
 *   - feed rejects → `error` ({@link ReelError}, retry → `loading`);
 *   - replay (from caught-up) → scroll to story 0 + `playing`.
 *
 * **No audio before the first tap.** Two guards hold this: (1) the active story
 * auto-plays only when `isAudioUnlocked` is true, which the TapToStart overlay
 * flips; (2) the overlay also starts the active story's audio SYNCHRONOUSLY
 * inside its tap handler ({@link handleStart}) via the registered play handle —
 * the only place iOS will honour `play()`. Until that gesture, nothing plays.
 *
 * Save is LOCAL in-memory state lifted to this component (Set of story ids) so it
 * survives scroll-away. Follow is PERSISTENT (Phase 3d SP3): its lifted set is
 * hydrated from the `follows` table on feed load and each toggle writes through
 * {@link toggleFollow} (optimistic UI, reconciled on the write result), so a
 * follow boosts that story's subniche in tomorrow's feed. Ask/Voice/Detail are
 * deferred no-ops in {@link ReelChrome}.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AllCaughtUp } from "@/components/reel/AllCaughtUp";
import { LoadingSkeleton } from "@/components/reel/LoadingSkeleton";
import { ReelError } from "@/components/reel/ReelError";
import { ReelStory } from "@/components/reel/ReelStory";
import { TapToStart } from "@/components/reel/TapToStart";
import { useLayerStack } from "@/components/shell/LayerStackContext";
import { getFeed } from "@/lib/feed/fixtureFeed";
import { getFollowedStoryIds, toggleFollow } from "@/lib/follows";
import { logger } from "@/lib/logger";
import { useActiveStoryObserver } from "@/lib/reel/gestures";
import { computePreloadIndices } from "@/lib/reel/preload";
import { nextReelStatus, type ReelEvent, type ReelStatus } from "@/lib/reel/reelStatus";
import type { NextReelState } from "@/lib/reel/useReelAudio";
import type { Story } from "@/types/feed";

export type { ReelEvent, ReelStatus };
// The reel-status state machine now lives in `@/lib/reel/reelStatus` — shared with
// the Blip Flow Stage-4 reel (`components/blip/reel/BlipReel`) and importable after
// this legacy reel is archived. Re-exported so existing importers of this module
// (`ReelStory`, `tests/lib/reel/reelStatus.test.ts`) keep resolving unchanged.
export { nextReelStatus };

/**
 * Mount the reel: load the feed, wire the status state machine + audio unlock,
 * and render the stories.
 */
export function Reel() {
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  // Imperative play handle for the ACTIVE story — registered by that ReelStory so
  // the TapToStart overlay can start playback SYNCHRONOUSLY inside its tap gesture
  // (iOS unlock reality; an effect-driven play() runs too late to unlock audio).
  const activeStoryPlayRef = useRef<(() => Promise<void>) | null>(null);

  const [stories, setStories] = useState<Story[]>([]);
  const [reelStatus, setReelStatus] = useState<ReelStatus>("loading");
  const [isAudioUnlocked, setIsAudioUnlocked] = useState<boolean>(false);
  const [savedDigestIds, setSavedDigestIds] = useState<Set<string>>(() => new Set());
  // Followed state is PERSISTENT: seeded empty, then hydrated from the `follows`
  // table once the feed resolves (one batched read), and written through on each
  // toggle. Signed-out users hydrate to empty (no crash) — see follows.ts.
  const [followedDigestIds, setFollowedDigestIds] = useState<Set<string>>(() => new Set());

  const activeIndex = useActiveStoryObserver({
    containerRef: scrollContainerRef,
    storyCount: stories.length,
  });

  // Surface the active (snapped) story UP to the LayerStack shell so it — and
  // SP2's swipe-right trigger — can open Detail for whatever the user is looking
  // at, without prop-drilling through ReelStory/ReelChrome. This is the ONLY
  // shell seam in the reel; it touches no audio/karaoke/scroll/status logic.
  const { setActiveStory } = useLayerStack();
  const currentStory = stories[activeIndex] ?? null;
  // Reason: keep the shell's active story in sync as the snapped story changes
  // (and clear it before the feed resolves); `currentStory` is derived from
  // state so this runs only on a genuine active-story change.
  useEffect(() => {
    setActiveStory(currentStory);
  }, [currentStory, setActiveStory]);

  // The active story + the next 1–2 (preload window) get <audio preload="auto">;
  // the rest stay "none" (port-map §6 — gap-free auto-advance). Pure + finite.
  const preloadIndexSet = new Set<number>([activeIndex, ...computePreloadIndices(activeIndex, stories.length)]);

  /**
   * Load (or reload) the fixture feed. On success → `tapstart`; on failure →
   * `error`. Extracted so both the initial mount effect and the error-screen
   * retry share one path.
   */
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

  // Load the fixture feed once on mount (async to match the production seam).
  useEffect(() => loadFeed(), [loadFeed]);

  // Hydrate the persisted follow set once the feed has stories (one batched read
  // of the `follows` table). Signed-out users resolve to an empty set (no crash).
  // Runs when the loaded stories change so a swapped feed re-hydrates correctly.
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
        // Reason: getFollowedStoryIds already swallows errors to an empty set;
        // this catch is a belt-and-braces guard so a rejected hydrate never
        // unmounts the reel. Leave the existing (empty) followed state.
        logger.error("reel_follow_hydrate_failed", {
          error_message: hydrateError instanceof Error ? hydrateError.message : "unknown",
          fix_suggestion: "Confirm migration 0005 applied and the follows RLS allows the authed SELECT.",
        });
      });
    return () => {
      isMounted = false;
    };
  }, [stories]);

  /**
   * Register/unregister the ACTIVE story's play handle. Each `ReelStory` calls
   * this with its `playAudio` when it becomes active and with `null` when it
   * stops being active, so `handleStart` always plays the right story in-gesture.
   *
   * Deregistration is compare-and-clear: a `null` registration only takes effect
   * if the stored handle still equals `previousPlay`. During auto-advance the next
   * story may register before the previous story's cleanup runs (cross-instance
   * effect ordering is not guaranteed) — this guard stops a late cleanup from
   * nulling out the newer story's handle.
   */
  const registerActiveStoryPlay = useCallback(
    (play: (() => Promise<void>) | null, previousPlay?: () => Promise<void>): void => {
      if (play === null && previousPlay !== undefined && activeStoryPlayRef.current !== previousPlay) {
        return;
      }
      activeStoryPlayRef.current = play;
    },
    [],
  );

  /**
   * First-tap handler for the TapToStart overlay. Unlocks audio, starts the
   * active story's playback SYNCHRONOUSLY (iOS gesture requirement), and moves the
   * machine to `playing`.
   */
  const handleStart = useCallback((): void => {
    setIsAudioUnlocked(true);
    logger.info("reel_audio_unlocked", {});
    // Reason: play() MUST run inside this gesture call stack to unlock the iOS
    // audio element. The registered active-story handle is that in-gesture call.
    void activeStoryPlayRef.current?.();
    setReelStatus((current) => nextReelStatus(current, "first_tap"));
  }, []);

  /**
   * Auto-advance: when the active story's audio ends, scroll to the next story
   * (the observer then promotes it to active and its audio plays), or flip to
   * `caughtup` when the last story finishes.
   */
  const handleAudioEnded = useCallback((nextState: NextReelState): void => {
    if (nextState.isCaughtUp) {
      logger.info("reel_reached_caught_up", {});
      setReelStatus((current) => nextReelStatus(current, "reached_caught_up"));
      return;
    }
    const containerElement = scrollContainerRef.current;
    if (containerElement) {
      // Reason: one story == one viewport height; scroll to nextIndex * height so
      // the snap container lands on (and the observer activates) the next story.
      containerElement.scrollTo({
        top: nextState.nextIndex * containerElement.clientHeight,
        behavior: "smooth",
      });
    }
  }, []);

  /**
   * Replay the briefing from the start (caught-up → playing). Scrolls the snap
   * container back to story 0; the observer re-activates it and (audio already
   * unlocked) the active-story effect replays digest-1.
   */
  const handleReplay = useCallback((): void => {
    logger.info("reel_replay_requested", {});
    const containerElement = scrollContainerRef.current;
    if (containerElement) {
      containerElement.scrollTo({ top: 0, behavior: "smooth" });
    }
    setReelStatus((current) => nextReelStatus(current, "replay"));
  }, []);

  /**
   * Retry the feed load from the error screen (error → loading). Re-runs
   * `getFeed`, which on success moves to `tapstart` again.
   */
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

  /**
   * Persist a follow toggle (Phase 3d SP3). Optimistically flips the lifted
   * followed set for snappy `follow-on` feedback, writes through
   * {@link toggleFollow}, then RECONCILES to the authoritative persisted state
   * the write returns — so a failed/skipped (e.g. signed-out) write reverts the
   * accent instead of leaving the UI lying about a non-existent row.
   *
   * `digestId` here is the story id (`Story.digest_id` carries `stories.story_id`).
   */
  const toggleFollowedForStory = useCallback((digestId: string): void => {
    // Optimistic flip for immediate UI feedback.
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
        // Reconcile: snap the set to the persisted truth the write returned.
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
        // Reason: toggleFollow already swallows write errors to the unchanged
        // persisted state; this guard handles an unexpected throw by re-reading
        // truth so the optimistic flip never sticks on a failed write.
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

  return (
    <div className="relative h-full w-full bg-background">
      <div
        ref={scrollContainerRef}
        className="h-full w-full snap-y snap-mandatory overflow-y-scroll overscroll-y-contain [scrollbar-width:none]"
      >
        {stories.map((story, storyIndex) => (
          <ReelStory
            key={story.digest_id}
            story={story}
            storyIndex={storyIndex}
            storyCount={stories.length}
            isActive={storyIndex === activeIndex}
            isAudioUnlocked={isAudioUnlocked}
            shouldPreload={preloadIndexSet.has(storyIndex)}
            onFirstTap={handleStart}
            onRegisterActivePlay={registerActiveStoryPlay}
            onAudioEnded={handleAudioEnded}
            isSaved={savedDigestIds.has(story.digest_id)}
            isFollowed={followedDigestIds.has(story.digest_id)}
            onToggleSave={() => toggleSavedForStory(story.digest_id)}
            onToggleFollow={() => toggleFollowedForStory(story.digest_id)}
          />
        ))}
      </div>

      {/* loading skeleton — the buffering state before the feed resolves */}
      {reelStatus === "loading" ? <LoadingSkeleton /> : null}

      {/* tap-to-start overlay — the iOS audio-unlock gate (no audio before tap) */}
      {reelStatus === "tapstart" ? <TapToStart onStart={handleStart} /> : null}

      {/* all-caught-up finish line — replaces the SP3 placeholder */}
      {reelStatus === "caughtup" ? <AllCaughtUp onReplay={handleReplay} /> : null}

      {/* offline / failed-load screen */}
      {reelStatus === "error" ? <ReelError onRetry={handleRetry} /> : null}
    </div>
  );
}
