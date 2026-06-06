"use client";

/**
 * StoryTimelineDrawer — the Detail "HOW IT DEVELOPED" expandable timeline
 * (port-map §2 row 6: "timeline collapsed/expanded"; prototype `#trust-toggle` /
 * `.trust-drawer` / `.tl-item` in `app.js` + `styles.css`).
 *
 * A collapsed-by-default drawer under a tappable header. Tapping the header
 * expands it to reveal every {@link TimelineEvent} in the order received (already
 * `timeline_event_index`-ordered by `fetchStoryDetail` — NOT re-sorted), each row
 * showing the `timeline_when_label` (mono) above the `timeline_what_text`. Tapping
 * again collapses it.
 *
 * **Expand/collapse motion (port-map §3.3).** The drawer body animates its height
 * `0 → auto` on the lateral easing curve (`cubic-bezier(0.22,0.61,0.36,1)`,
 * matching the prototype `.trust-drawer` transition). Under
 * {@link useReducedMotion} the open/close **snaps** (`duration: 0`, no animated
 * height) — the prototype disables `.trust-drawer` transitions under
 * `prefers-reduced-motion`, mirrored here.
 *
 * **Empty guard (Rule 12).** A story may carry no timeline events. When
 * `timeline` is empty the drawer renders **nothing** (returns `null`) — no empty
 * header for a drawer with nothing to open, matching the `KeyFigureCard` /
 * `OpposingViewCard` null-omission idiom.
 *
 * **Prop contract (preserved from the SP2 stub).** `StoryDetail` passes the
 * already-fetched, already-ordered {@link TimelineEvent} array
 * (`detail.timeline`); the {@link StoryTimelineDrawerProps} shape is unchanged
 * from SP2 (`StoryDetail` mounts `<StoryTimelineDrawer timeline={detail.timeline} />`).
 *
 * @example
 * <StoryTimelineDrawer timeline={detail.timeline} />
 * // → a collapsed "HOW IT DEVELOPED" header; tapping reveals the ordered events
 *
 * @example
 * <StoryTimelineDrawer timeline={[]} />
 * // → renders nothing
 */

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useState } from "react";
import type { TimelineEvent } from "@/types/detail";

/**
 * The drawer-body open/close transition: the prototype `.trust-drawer`
 * `max-height` ease (`cubic-bezier(0.22,0.61,0.36,1)`), in framer form, animating
 * `height`. Swapped for a `{ duration: 0 }` snap under reduced motion.
 */
const DRAWER_TRANSITION = { duration: 0.38, ease: [0.22, 0.61, 0.36, 1] as const };

/**
 * Props for the "HOW IT DEVELOPED" timeline drawer.
 *
 * SP4 keeps this interface verbatim from the SP2 stub: `StoryDetail` passes the
 * populated, index-ordered {@link TimelineEvent} array it already fetched.
 */
export interface StoryTimelineDrawerProps {
  /**
   * The story's development events, already ordered by `timeline_event_index`
   * (do NOT re-sort). From `fetchStoryDetail(...).timeline`.
   */
  timeline: TimelineEvent[];
}

/**
 * Render the collapsed/expandable "HOW IT DEVELOPED" timeline drawer.
 *
 * Starts collapsed; the header button toggles {@link StoryTimelineDrawerProps.timeline}
 * open/closed. Events render in the received order (no re-sort). The body height
 * animates open/closed, snapping under reduced motion. Renders nothing for an
 * empty `timeline`.
 */
export function StoryTimelineDrawer({ timeline }: StoryTimelineDrawerProps) {
  const prefersReducedMotion = useReducedMotion();
  const [isExpanded, setIsExpanded] = useState<boolean>(false);

  // Reason: a story may have no development events — omit the whole drawer rather
  // than render a header that opens onto nothing (matches KeyFigureCard's null
  // branch).
  if (timeline.length === 0) {
    return null;
  }

  return (
    <section aria-label="How it developed" className="mt-8">
      <button
        type="button"
        data-timeline-toggle={isExpanded ? "expanded" : "collapsed"}
        aria-expanded={isExpanded}
        onClick={() => setIsExpanded((expanded) => !expanded)}
        className="flex min-h-[44px] w-full items-center justify-between gap-2 border-t border-white/10 pt-3.5 text-white/55 transition-transform active:scale-[0.99]"
      >
        <span className="whitespace-nowrap font-mono text-[10px] tracking-[0.14em]">HOW IT DEVELOPED</span>
        <motion.span
          aria-hidden="true"
          className="font-mono text-[13px] leading-none"
          animate={{ rotate: isExpanded ? 180 : 0 }}
          transition={prefersReducedMotion ? { duration: 0 } : DRAWER_TRANSITION}
        >
          ⌄
        </motion.span>
      </button>

      <AnimatePresence initial={false}>
        {isExpanded ? (
          <motion.div
            key="timeline-drawer-body"
            data-timeline-drawer="open"
            className="overflow-hidden"
            initial={prefersReducedMotion ? { height: "auto" } : { height: 0 }}
            animate={{ height: "auto" }}
            exit={prefersReducedMotion ? { height: "auto" } : { height: 0 }}
            transition={prefersReducedMotion ? { duration: 0 } : DRAWER_TRANSITION}
          >
            <ol className="mt-4 space-y-3">
              {/* Render in the received order (already timeline_event_index-ordered
                  by fetchStoryDetail — do NOT re-sort). */}
              {timeline.map((timelineEvent, eventPosition) => {
                const isLastEvent = eventPosition === timeline.length - 1;
                return (
                  <li
                    key={timelineEvent.timeline_event_index}
                    data-timeline-event-index={timelineEvent.timeline_event_index}
                    className="relative pl-[22px]"
                  >
                    {/* accent dot + connector, ported from the prototype .tl-item.
                        The connector is hidden on the last event (no line dangling
                        off the end) — prototype `.tl-item:last-child::after`. */}
                    <span
                      aria-hidden="true"
                      className="absolute left-[4px] top-[6px] h-[7px] w-[7px] rounded-pill"
                      style={{ backgroundColor: "var(--accent)" }}
                    />
                    {isLastEvent ? null : (
                      <span
                        aria-hidden="true"
                        className="absolute bottom-[-10px] left-[7px] top-[15px] w-px bg-white/[0.14]"
                      />
                    )}
                    <span className="font-mono text-[10px] text-white/45">{timelineEvent.timeline_when_label}</span>
                    <p className="mt-0.5 font-sans text-[13px] leading-snug text-white/75">
                      {timelineEvent.timeline_what_text}
                    </p>
                  </li>
                );
              })}
            </ol>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}
