/**
 * Reel gesture helpers.
 *
 * **Rule-7 conflict resolution (documented).** The phase file lists
 * `gestures.ts (framer-motion: swipe up/down = next/prev)`, but port-map §3.2
 * says scroll-snap "is simpler and feels most native on iOS WebView — pick one"
 * and specs `Reel.tsx` as a "scroll-snap container". We resolve in favour of
 * **CSS scroll-snap** for vertical next/prev navigation (one full-viewport
 * `snap-start` story; an {@link useActiveStoryObserver} IntersectionObserver
 * promotes the snapped story to the active index). framer-motion's role here is
 * narrowed to **tap = pause/play**: {@link createTapPlayHandler} returns an
 * `onTap` that framer fires only for genuine taps (it distinguishes tap from a
 * scroll/drag via its own movement threshold), so the tap never fights the
 * scroll. (framer `drag="x"` is reserved for the lateral Detail/Voice layers —
 * out of scope in this sub-phase.)
 */

import type { TapHandlers } from "framer-motion";
import type { RefObject } from "react";
import { useEffect, useState } from "react";

/**
 * Build the framer-motion `onTap` handler for the reel's tap = pause/play
 * gesture.
 *
 * framer fires `onTap` only when the pointer goes down and up without crossing
 * its drag/scroll threshold, so this will not trigger while the user is
 * scroll-snapping between stories. The returned value is spread onto the
 * `motion.*` element that wraps the story surface.
 *
 * @param onTap - Called on a genuine tap (the host wires this to toggle play/pause).
 * @returns A `{ onTap }` object to spread onto a framer `motion` element.
 *
 * @example
 * const tapHandlers = createTapPlayHandler(() => audio.togglePlay());
 * // <motion.div {...tapHandlers}>…</motion.div>
 */
export function createTapPlayHandler(onTap: () => void): Pick<TapHandlers, "onTap"> {
  return {
    onTap: () => {
      onTap();
    },
  };
}

/** Inputs to {@link useActiveStoryObserver}. */
export interface UseActiveStoryObserverParams {
  /** The scroll-snap container whose direct children are the story sections. */
  containerRef: RefObject<HTMLElement | null>;
  /** Number of story sections (the observer expects this many `[data-story-index]` children). */
  storyCount: number;
}

/**
 * Track which scroll-snapped story is currently centered, as an active index.
 *
 * Watches each `[data-story-index]` section with an `IntersectionObserver` and
 * promotes the most-visible one (≥ 60% in view) to the active index. This is the
 * scroll-snap analogue of the prototype's `S.idx` — it is what tells the reel
 * which story's audio to play. Returns the active index (starts at 0).
 *
 * Falls back gracefully when `IntersectionObserver` is unavailable (older test
 * environments): the active index simply stays at its initial value.
 *
 * @example
 * const activeIndex = useActiveStoryObserver({ containerRef, storyCount: 5 });
 */
export function useActiveStoryObserver({ containerRef, storyCount }: UseActiveStoryObserverParams): number {
  const [activeStoryIndex, setActiveStoryIndex] = useState<number>(0);

  useEffect(() => {
    const containerElement = containerRef.current;
    if (!containerElement || typeof IntersectionObserver === "undefined") {
      return;
    }

    const sections = Array.from(containerElement.querySelectorAll<HTMLElement>("[data-story-index]"));
    if (sections.length === 0) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) {
            continue;
          }
          const indexAttr = entry.target.getAttribute("data-story-index");
          if (indexAttr === null) {
            continue;
          }
          const parsedIndex = Number.parseInt(indexAttr, 10);
          // Reason: clamp to a valid story index; a malformed attribute must not
          // promote an out-of-range story (which would play no audio).
          if (Number.isInteger(parsedIndex) && parsedIndex >= 0 && parsedIndex < storyCount) {
            setActiveStoryIndex(parsedIndex);
          }
        }
      },
      {
        root: containerElement,
        // Reason: 60% visible = "this is the snapped story". Above 50% guarantees
        // exactly one section can cross the threshold at a time on a snap viewport.
        threshold: 0.6,
      },
    );

    for (const section of sections) {
      observer.observe(section);
    }
    return () => observer.disconnect();
  }, [containerRef, storyCount]);

  return activeStoryIndex;
}
