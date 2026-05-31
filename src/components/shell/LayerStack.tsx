"use client";

/**
 * LayerStack — the lateral-layer shell that owns the z-order, the reel
 * dim/scale-back depth cue, and the Detail layer's open/close state (port-map
 * §1, §3.3).
 *
 * **What this is (the structural shell).** The prototype mounts everything into
 * one node and stacks layers: `.layer-reel` (base) with `.layer-detail` pushing
 * in from the right (`translateX(100%) → 0`), and when a lateral layer opens the
 * reel dims + scales back (`scale(0.94) brightness(0.45)`) as a depth cue. This
 * component reproduces that container + state plumbing:
 *   - it renders the reel as the base layer (the {@link LayerStackProps.children});
 *   - it owns `isDetailOpen` + the currently-open {@link Story};
 *   - it publishes {@link useLayerStack} so the reel can `openDetail(activeStory)`
 *     and a future Detail panel can `closeDetail()`;
 *   - it applies the reel dim/scale-back while Detail is open;
 *   - it renders a MINIMAL Detail mount slot (the right lateral layer) for SP2 to
 *     fill.
 *
 * **Scope (SP2).** SP0 left the Detail slot a structural CSS-transition stub. SP2
 * (this file's current state) replaces it with a framer-motion `motion.aside` that
 * slides `x: "100%" → 0` and follows the finger via `drag="x"` (§3.2), mounting
 * the real {@link StoryDetail} panel (Playfair body, key figure, trust + timeline
 * stubs). The open/close DRAG gestures live here too:
 *   - **drag-to-open:** a thin left-edge drag region over the reel — a rightward
 *     drag (`offset.x`/`velocity.x` past threshold) calls `openDetail(activeStory)`
 *     (prototype `attachGestures`: `dx > 0 → openDetail`). The reel itself is NOT
 *     touched — the trigger lives wholly in this shell, reading `activeStory` from
 *     context;
 *   - **drag-to-close:** a rightward drag on the panel, gated on the reading
 *     container's `scrollTop < 10` (prototype `attachBackSwipe`: `dx > 70 &&
 *     scrollTop < 10`), calls `closeDetail()`.
 * Trust-strip / timeline / Q&A internals remain SP3/SP4 (they edit only their own
 * files).
 *
 * **Reduced motion (§3.3).** Read once via framer-motion's
 * {@link useReducedMotion} (matching `ReelStory` / `AllCaughtUp`). When set, both
 * the reel scale-back and the lateral slide snap instantly (no transition), and
 * the Detail reveal drops its stagger.
 *
 * @example
 *   <LayerStack>
 *     <Reel />
 *   </LayerStack>
 *   // inside the reel: const { openDetail } = useLayerStack(); openDetail(story);
 */

import { motion, type PanInfo, useReducedMotion } from "framer-motion";
import type { CSSProperties } from "react";
import { useCallback, useMemo, useRef, useState } from "react";
import { StoryDetail } from "@/components/detail/StoryDetail";
import { LayerStackContext, type LayerStackContextValue } from "@/components/shell/LayerStackContext";
import type { Story } from "@/types/feed";

/**
 * The reel dim/scale-back depth cue (`styles.css`
 * `.device.lateral-open .layer-reel`): `scale(0.94)` + `brightness(0.45)`. Same
 * timing as the lateral slide so the two move together.
 */
const REEL_SCALEBACK_TRANSFORM = "scale(0.94)";
const REEL_SCALEBACK_FILTER = "brightness(0.45)";
const REEL_SCALEBACK_TRANSITION = "transform 420ms cubic-bezier(0.22, 0.61, 0.36, 1), filter 420ms ease";

/**
 * Drag-commit thresholds (port-map §3.2). A lateral drag commits open/close when
 * the rightward offset OR velocity passes these — mirroring the prototype's
 * `dx > 56`/`dx > 70` distance checks but adding velocity so a fast flick also
 * commits (the §10 drag-to-follow upgrade). Distance is the dominant signal;
 * velocity catches quick flicks that travel less far.
 */
const DRAG_OPEN_OFFSET_THRESHOLD_PX = 64;
const DRAG_CLOSE_OFFSET_THRESHOLD_PX = 70;
const DRAG_VELOCITY_THRESHOLD_PX_PER_S = 480;

/**
 * The reading container is "at the top" (so a rightward drag means close, not
 * read-scroll) when its `scrollTop` is below this — the prototype's
 * `attachBackSwipe` gate (`scrollTop < 10`).
 */
const CLOSE_SCROLLTOP_GATE_PX = 10;

/**
 * The framer transition for the lateral panel slide — the prototype's
 * `.layer-detail` curve (`420ms cubic-bezier(0.22,0.61,0.36,1)`, `styles.css`)
 * expressed for framer-motion. Supersedes SP0's CSS-string `LATERAL_TRANSITION`
 * (same curve, structured form) now that the panel is a `motion.aside`.
 */
const LATERAL_PANEL_TRANSITION = { duration: 0.42, ease: [0.22, 0.61, 0.36, 1] as const };

/** Width of the left-edge drag region that opens Detail without touching the reel. */
const OPEN_EDGE_REGION_WIDTH_PX = 28;

export interface LayerStackProps {
  /** The reel layer — rendered as the base of the stack. */
  children: React.ReactNode;
}

/**
 * Host the reel layer, own the Detail-layer open/close state, and render the
 * (stubbed) lateral Detail mount slot.
 */
export function LayerStack({ children }: LayerStackProps) {
  const prefersReducedMotion = useReducedMotion();

  const [isDetailOpen, setIsDetailOpen] = useState<boolean>(false);
  const [openDetailStory, setOpenDetailStory] = useState<Story | null>(null);
  const [activeStory, setActiveStory] = useState<Story | null>(null);

  /**
   * The Detail reading container, populated by {@link StoryDetail}. Read
   * synchronously inside the drag-to-close handler so the `scrollTop < 10` gate
   * doesn't re-render this shell on every scroll.
   */
  const detailScrollRef = useRef<HTMLDivElement | null>(null);

  /** Open the Detail layer for `story` (the reel calls this with its active story). */
  const openDetail = useCallback((story: Story): void => {
    setOpenDetailStory(story);
    setIsDetailOpen(true);
  }, []);

  /**
   * Close the Detail layer. The story is kept mounted in state so the slide-out
   * still shows its content (the panel animates back to `x: 100%` before the
   * content would matter).
   */
  const closeDetail = useCallback((): void => {
    setIsDetailOpen(false);
  }, []);

  /**
   * Commit drag-to-OPEN: a rightward drag on the left-edge region opens Detail
   * for the reel's active story (prototype `dx > 0 → openDetail`). No-op if there
   * is no active story yet or Detail is already open.
   */
  const handleEdgeDragEnd = useCallback(
    (_event: PointerEvent | MouseEvent | TouchEvent, info: PanInfo): void => {
      if (activeStory === null || isDetailOpen) {
        return;
      }
      const committed =
        info.offset.x > DRAG_OPEN_OFFSET_THRESHOLD_PX || info.velocity.x > DRAG_VELOCITY_THRESHOLD_PX_PER_S;
      if (committed) {
        openDetail(activeStory);
      }
    },
    [activeStory, isDetailOpen, openDetail],
  );

  /**
   * Commit drag-to-CLOSE: a rightward drag on the panel closes Detail, but ONLY
   * when the reading container is at the top (`scrollTop < 10`) so it never fights
   * vertical reading scroll (prototype `attachBackSwipe`: `dx > 70 &&
   * scrollTop < 10`). framer snaps the panel back to `x: 0` when not committed.
   */
  const handlePanelDragEnd = useCallback(
    (_event: PointerEvent | MouseEvent | TouchEvent, info: PanInfo): void => {
      const scrollTop = detailScrollRef.current?.scrollTop ?? 0;
      const atTop = scrollTop < CLOSE_SCROLLTOP_GATE_PX;
      const committed =
        info.offset.x > DRAG_CLOSE_OFFSET_THRESHOLD_PX || info.velocity.x > DRAG_VELOCITY_THRESHOLD_PX_PER_S;
      if (atTop && committed) {
        closeDetail();
      }
    },
    [closeDetail],
  );

  const layerStackContextValue = useMemo<LayerStackContextValue>(
    () => ({ isDetailOpen, openDetailStory, activeStory, setActiveStory, openDetail, closeDetail }),
    [isDetailOpen, openDetailStory, activeStory, openDetail, closeDetail],
  );

  // Reel base layer: when Detail is open, scale + dim it as the depth cue. Under
  // reduced motion the change still applies but snaps (no transition).
  const reelLayerStyle: CSSProperties = {
    height: "100%",
    width: "100%",
    transform: isDetailOpen ? REEL_SCALEBACK_TRANSFORM : "none",
    filter: isDetailOpen ? REEL_SCALEBACK_FILTER : "none",
    transformOrigin: "center center",
    transition: prefersReducedMotion ? "none" : REEL_SCALEBACK_TRANSITION,
    willChange: "transform, filter",
  };

  // The left-edge drag region that opens Detail. Only present (and only on top of
  // the reel) when a story is active and Detail is closed, so it never traps taps
  // over the reel once Detail is open. A thin strip — reel taps/scroll elsewhere
  // are untouched (the reel itself is never edited; the trigger lives here).
  const showOpenEdgeRegion = activeStory !== null && !isDetailOpen;
  const openEdgeRegionStyle: CSSProperties = {
    position: "absolute",
    left: 0,
    top: 0,
    bottom: 0,
    width: OPEN_EDGE_REGION_WIDTH_PX,
    zIndex: 15,
    touchAction: "pan-y",
  };

  return (
    <LayerStackContext.Provider value={layerStackContextValue}>
      <div className="relative h-full w-full overflow-hidden bg-background">
        {/* base reel layer */}
        <div style={reelLayerStyle}>{children}</div>

        {/* drag-to-open: a thin left-edge region. A rightward drag opens Detail for
            the active story. Snaps back to origin when not committed. Mounted only
            while closed + a story is active, so it never blocks the reel surface. */}
        {showOpenEdgeRegion ? (
          <motion.div
            aria-hidden="true"
            style={openEdgeRegionStyle}
            drag="x"
            dragSnapToOrigin
            dragConstraints={{ left: 0, right: 0 }}
            dragElastic={0.6}
            dragMomentum={false}
            onDragEnd={handleEdgeDragEnd}
          />
        ) : null}

        {/* lateral Detail layer — slides x: 100% → 0; follows the finger on a
            rightward drag, committing close via handlePanelDragEnd (scrollTop-gated).
            Inert while closed so the off-screen panel never traps taps. */}
        <motion.aside
          aria-label="Story detail"
          aria-hidden={!isDetailOpen}
          inert={!isDetailOpen}
          className="absolute inset-0 z-20 bg-background"
          style={{ willChange: "transform" }}
          initial={false}
          animate={{ x: isDetailOpen ? "0%" : "100%" }}
          transition={prefersReducedMotion ? { duration: 0 } : LATERAL_PANEL_TRANSITION}
          drag={isDetailOpen ? "x" : false}
          dragConstraints={{ left: 0, right: 0 }}
          dragElastic={{ left: 0, right: 0.7 }}
          dragMomentum={false}
          onDragEnd={handlePanelDragEnd}
        >
          {openDetailStory !== null ? (
            <StoryDetail story={openDetailStory} scrollContainerRef={detailScrollRef} />
          ) : null}
        </motion.aside>
      </div>
    </LayerStackContext.Provider>
  );
}
