"use client";

/**
 * `useBottomAnchoredScroll` — the chat scroll contract (issue #40), shared by
 * the typed ask thread and the voice transcript thread.
 *
 * A chat list follows new content (newest message pinned into view) ONLY while
 * the user is at (or near) the bottom. Once the user scrolls up to reread,
 * new content must NOT yank them back down; returning to the bottom re-arms
 * following. "Near" is {@link BOTTOM_PIN_THRESHOLD_PX} so sub-pixel scroll
 * positions and momentum-scroll settling still count as "at the bottom".
 *
 * Usage: put `scrollContainerRef` + `onScroll={handleScroll}` on the
 * `overflow-y: auto` container, and pass the values whose change means "the
 * thread grew" (e.g. turn count, streaming text) as `observedGrowthKeys`.
 *
 * @example
 * const { scrollContainerRef, handleScroll } = useBottomAnchoredScroll<HTMLDivElement>([turns.length]);
 * <div className="thread" ref={scrollContainerRef} onScroll={handleScroll}>…</div>
 */

import { type RefObject, useCallback, useEffect, useRef } from "react";

/** How close (px) to the bottom still counts as "pinned to the bottom". */
export const BOTTOM_PIN_THRESHOLD_PX = 48;

/** What {@link useBottomAnchoredScroll} hands back to a chat thread. */
export interface BottomAnchoredScrollController<T extends HTMLElement> {
  /** Attach to the `overflow-y: auto` scroll container. */
  scrollContainerRef: RefObject<T | null>;
  /** Attach as the container's `onScroll` — tracks whether the user is at the bottom. */
  handleScroll: () => void;
}

/**
 * Keep a chat scroll container pinned to its newest content — unless the user
 * has scrolled up.
 *
 * @param observedGrowthKeys - Values whose change signals new/updated content
 *   (turn counts, streaming transcript text). The pin runs on every change.
 * @returns The container ref + scroll handler to spread onto the container.
 */
export function useBottomAnchoredScroll<T extends HTMLElement>(
  observedGrowthKeys: readonly unknown[],
): BottomAnchoredScrollController<T> {
  const scrollContainerRef = useRef<T | null>(null);
  // Reason: a chat opens pinned — the first content render must land at the bottom.
  const isPinnedToBottomRef = useRef<boolean>(true);

  const handleScroll = useCallback((): void => {
    const scrollContainer = scrollContainerRef.current;
    if (!scrollContainer) {
      return;
    }
    const distanceFromBottomPx =
      scrollContainer.scrollHeight - scrollContainer.scrollTop - scrollContainer.clientHeight;
    isPinnedToBottomRef.current = distanceFromBottomPx <= BOTTOM_PIN_THRESHOLD_PX;
  }, []);

  useEffect(
    () => {
      const scrollContainer = scrollContainerRef.current;
      if (!scrollContainer || !isPinnedToBottomRef.current) {
        return;
      }
      scrollContainer.scrollTop = scrollContainer.scrollHeight;
    },
    // Reason: the caller's growth keys ARE the dependency list — this hook
    // exists to run exactly when the thread content grows/streams.
    // biome-ignore lint/correctness/useExhaustiveDependencies: caller-supplied growth keys drive the pin.
    observedGrowthKeys,
  );

  return { scrollContainerRef, handleScroll };
}
