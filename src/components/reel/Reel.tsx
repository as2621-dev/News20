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
 * Save/Follow are LOCAL in-memory state lifted to this component (Set of story
 * ids) so they survive scroll-away; seeded to mirror the prototype (one followed
 * story). Ask/Voice/Detail are deferred no-ops in {@link ReelChrome}.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AllCaughtUp } from "@/components/reel/AllCaughtUp";
import { LoadingSkeleton } from "@/components/reel/LoadingSkeleton";
import { ReelError } from "@/components/reel/ReelError";
import { ReelStory } from "@/components/reel/ReelStory";
import { TapToStart } from "@/components/reel/TapToStart";
import { useLayerStack } from "@/components/shell/LayerStackContext";
import { getFeed } from "@/lib/feed/fixtureFeed";
import { logger } from "@/lib/logger";
import { useActiveStoryObserver } from "@/lib/reel/gestures";
import { computePreloadIndices } from "@/lib/reel/preload";
import type { NextReelState } from "@/lib/reel/useReelAudio";
import type { Story } from "@/types/feed";

/**
 * The reel's high-level status (SP4 — full union).
 *
 * - `loading`  — initial buffer; {@link LoadingSkeleton} until the feed resolves.
 * - `tapstart` — feed ready, audio NOT yet unlocked; {@link TapToStart} overlay.
 * - `playing`  — audio unlocked; a story is playing / paused.
 * - `caughtup` — the last story finished; {@link AllCaughtUp} finish line.
 * - `error`    — the feed load failed; {@link ReelError} with retry.
 */
export type ReelStatus = "loading" | "tapstart" | "playing" | "caughtup" | "error";

/**
 * The events that drive {@link nextReelStatus}. Each corresponds to one real
 * thing that happens in the reel; the transition function maps `(status, event)`
 * to the next status and is the unit-testable seam (Rule 9).
 *
 * - `feed_loaded`       — `getFeed()` resolved.
 * - `feed_failed`       — `getFeed()` rejected.
 * - `first_tap`         — the user tapped the TapToStart overlay (audio unlock).
 * - `reached_caught_up` — the last story's audio ended.
 * - `replay`            — the user tapped replay on the caught-up screen.
 * - `retry`             — the user tapped retry on the error screen.
 */
export type ReelEvent = "feed_loaded" | "feed_failed" | "first_tap" | "reached_caught_up" | "replay" | "retry";

/**
 * Pure reel-status transition function — the single source of truth for how the
 * machine moves, extracted so it is unit-testable without rendering (Rule 9).
 *
 * Only the legal `(status, event)` pairs transition; every other pair is a
 * guarded no-op that returns the current status unchanged (an event that doesn't
 * apply to the current state must never silently corrupt it — e.g. a stray
 * `reached_caught_up` while still `loading`).
 *
 * Transition table:
 * | from        | event              | to        |
 * |-------------|--------------------|-----------|
 * | `loading`   | `feed_loaded`      | `tapstart`|
 * | `loading`   | `feed_failed`      | `error`   |
 * | `tapstart`  | `first_tap`        | `playing` |
 * | `playing`   | `reached_caught_up`| `caughtup`|
 * | `caughtup`  | `replay`           | `playing` |
 * | `error`     | `retry`            | `loading` |
 * | *any other* | *any other*        | unchanged |
 *
 * @param current - The current reel status.
 * @param event - The event that occurred.
 * @returns The next status (or `current` unchanged if the pair is not legal).
 *
 * @example
 * nextReelStatus("loading", "feed_loaded");      // "tapstart"
 * nextReelStatus("tapstart", "first_tap");       // "playing"
 * nextReelStatus("loading", "reached_caught_up"); // "loading" (guarded no-op)
 */
export function nextReelStatus(current: ReelStatus, event: ReelEvent): ReelStatus {
  switch (current) {
    case "loading":
      if (event === "feed_loaded") {
        return "tapstart";
      }
      if (event === "feed_failed") {
        return "error";
      }
      return current;
    case "tapstart":
      return event === "first_tap" ? "playing" : current;
    case "playing":
      return event === "reached_caught_up" ? "caughtup" : current;
    case "caughtup":
      return event === "replay" ? "playing" : current;
    case "error":
      return event === "retry" ? "loading" : current;
    default:
      return current;
  }
}

/** Seed one followed story to mirror the prototype (`S.followed = new Set(["s1"])`). */
const INITIALLY_FOLLOWED_DIGEST_IDS: readonly string[] = ["digest-1"];

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
  const [followedDigestIds, setFollowedDigestIds] = useState<Set<string>>(() => new Set(INITIALLY_FOLLOWED_DIGEST_IDS));

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
