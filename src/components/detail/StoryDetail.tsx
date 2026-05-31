"use client";

/**
 * StoryDetail — the swipe-right Detail reading panel (port-map §2 row 6, §3.3).
 *
 * **What this is.** The content of the lateral Detail layer that
 * {@link import("@/components/shell/LayerStack").LayerStack} slides in from the
 * right. On open it fetches the full {@link StoryDetail} payload for the active
 * story and renders, top-to-bottom, in a staggered `.reveal` entrance (§3.3):
 *   1. the chunked **Playfair** reading body from `detail_chunks` (in
 *      `chunk_index` order — already ordered by the fetch, NOT re-sorted),
 *   2. the {@link KeyFigureCard} (accent-coded; omitted when null),
 *   3. the {@link TrustStrip} (SP3 stub — fed `detail.trust_summary`),
 *   4. the {@link StoryTimelineDrawer} (SP4 stub — fed `detail.timeline`),
 *   5. the grounded {@link QaThread} (Phase 2b SP3 — the ask/answer turns).
 *
 * **Pinned Q&A bar (Phase 2b SP3).** Below the scrolling body, a pinned bottom bar
 * mounts {@link SuggestedQuestionChips} + {@link QaComposer} over a fade-to-canvas
 * gradient. Typing a question or tapping a chip calls {@link askQuestion} (the SP2
 * worker endpoint), appending a `thinking` turn that flips to a grounded bubble
 * with citation chips OR the `⌀ CAN'T ANSWER FROM SOURCE` refusal card, keyed on
 * `answer_is_grounded` (the trust contract — port-map §7, Rule 9). The bar shows
 * only once the detail has loaded.
 *
 * **The close-gate seam (port-map §3.2).** The back-swipe-to-close gesture lives
 * in `LayerStack`, but it must be gated on the reading container being scrolled
 * to the top (`scrollTop < 10`) so a drag-to-close doesn't fight vertical reading
 * scroll. `LayerStack` owns that gate, so it passes down a ref
 * ({@link StoryDetailProps.scrollContainerRef}) that this component attaches to
 * its scroll container — letting `LayerStack` read `scrollTop` synchronously
 * inside its drag handler WITHOUT this component re-rendering on every scroll.
 *
 * **Reduced motion (§3.3).** Read once via {@link useReducedMotion} (matching
 * `ReelStory` / `AllCaughtUp`). When set, the stagger is dropped and every reveal
 * item is shown instantly (no `y` offset, no transition) — the panel simply
 * appears, fully populated.
 *
 * **Fetch lifecycle.** Keyed on `story.digest_id` (the reel `Story.digest_id`
 * holds the `stories.story_id` slug — pass it straight to `fetchStoryDetail`).
 * A stale-response guard drops a resolved fetch if the active story changed
 * mid-flight. Loading shows a minimal placeholder; a failed fetch shows a calm
 * inline error (fail-loud, Rule 12 — never a silent blank panel).
 *
 * @example
 * <StoryDetail story={openDetailStory} scrollContainerRef={detailScrollRef} />
 */

import { motion, useReducedMotion } from "framer-motion";
import { type CSSProperties, type RefObject, useCallback, useEffect, useRef, useState } from "react";
import { KeyFigureCard } from "@/components/detail/KeyFigureCard";
import { QaComposer } from "@/components/detail/QaComposer";
import { QaThread, type QaTurn } from "@/components/detail/QaThread";
import { StoryTimelineDrawer } from "@/components/detail/StoryTimelineDrawer";
import { SuggestedQuestionChips } from "@/components/detail/SuggestedQuestionChips";
import { TrustStrip } from "@/components/detail/TrustStrip";
import { fetchStoryDetail } from "@/lib/detail/fetchStoryDetail";
import { askQuestion } from "@/lib/qa/askQuestion";
import type { StoryDetail as StoryDetailPayload } from "@/types/detail";
import type { Story } from "@/types/feed";

/**
 * The staggered-reveal container variants (port-map §3.3). The prototype adds
 * `.in` to `.reveal` items at `120 + i*70`ms → framer `delayChildren: 0.12`,
 * `staggerChildren: 0.07`. Under reduced motion the container drops the stagger.
 */
const REVEAL_CONTAINER_VARIANTS = {
  hidden: {},
  in: {
    transition: { staggerChildren: 0.07, delayChildren: 0.12 },
  },
};

/**
 * The per-item reveal variants (port-map §3.3): rise 14px + fade in over 0.52s on
 * the lateral easing curve. Reduced motion is handled by swapping the `hidden`
 * variant to the resting state (see {@link buildItemVariants}).
 */
const REVEAL_ITEM_VARIANTS = {
  hidden: { opacity: 0, y: 14 },
  in: { opacity: 1, y: 0, transition: { duration: 0.52, ease: [0.22, 0.61, 0.36, 1] as const } },
};

/** Reduced-motion item variants: already at rest, no offset, no transition (snap). */
const REVEAL_ITEM_VARIANTS_REDUCED = {
  hidden: { opacity: 1, y: 0 },
  in: { opacity: 1, y: 0, transition: { duration: 0 } },
};

/**
 * A `--accent` CSS custom-property carrier (the repo idiom — see `ReelStory`'s
 * `AccentStyle`). Sets the per-story accent on the Detail root so the key-figure
 * card + segment label read `var(--accent)` (port-map §3.4).
 */
type AccentStyle = CSSProperties & { "--accent": string };

export interface StoryDetailProps {
  /**
   * The story the Detail layer is showing. Its `digest_id` carries the
   * `stories.story_id` slug passed to `fetchStoryDetail`.
   */
  story: Story;
  /**
   * A ref `LayerStack` attaches to this panel's scroll container so it can read
   * `scrollTop` inside its drag-to-close handler (the `scrollTop < 10` close
   * gate, port-map §3.2). Owned by `LayerStack`; populated here.
   */
  scrollContainerRef: RefObject<HTMLDivElement | null>;
}

/** Render the staggered reading panel for one story. */
export function StoryDetail({ story, scrollContainerRef }: StoryDetailProps) {
  const prefersReducedMotion = useReducedMotion();

  const [detail, setDetail] = useState<StoryDetailPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Q&A thread state (Phase 2b SP3): the ask → thinking → answer turns. Reset when
  // the active story changes so one story's questions never bleed into another's.
  const [qaTurns, setQaTurns] = useState<QaTurn[]>([]);
  const qaTurnIdRef = useRef<number>(0);
  const isAnswering = qaTurns.some((turn) => turn.phase === "thinking");

  // Reason: holds the CURRENTLY active story id so an in-flight answer from a
  // previous story (captured in its own closure) can detect it is now stale and
  // drop itself instead of writing into a different story's thread.
  const activeStoryIdRef = useRef<string>(story.digest_id);
  activeStoryIdRef.current = story.digest_id;

  // Reason: fetch the heavier Detail payload on open / when the active story
  // changes. A monotonic request token drops a stale resolution if the user
  // opened a different story before this fetch returned (no flash of the wrong
  // story's body).
  const requestTokenRef = useRef<number>(0);
  useEffect(() => {
    const requestToken = requestTokenRef.current + 1;
    requestTokenRef.current = requestToken;
    setDetail(null);
    setLoadError(null);
    // Reason: clear the prior story's Q&A turns on story change (per-story scope).
    setQaTurns([]);

    fetchStoryDetail(story.digest_id)
      .then((payload) => {
        if (requestTokenRef.current === requestToken) {
          setDetail(payload);
        }
      })
      .catch((error: unknown) => {
        if (requestTokenRef.current === requestToken) {
          setLoadError(error instanceof Error ? error.message : "Could not load this story's detail.");
        }
      });
  }, [story.digest_id]);

  // Reason: capture the story id at ask time so a story switch mid-flight drops a
  // stale answer (it belongs to the previous story's thread).
  const askStoryId = story.digest_id;
  const handleAsk = useCallback(
    (questionText: string): void => {
      const turnId = qaTurnIdRef.current + 1;
      qaTurnIdRef.current = turnId;
      setQaTurns((prev) => [
        ...prev,
        { turn_id: turnId, question_text: questionText, phase: "thinking", answer: null },
      ]);

      askQuestion(askStoryId, questionText).then((answer) => {
        // Drop the answer if the user switched stories before it resolved — it
        // belongs to the previous story's thread, not the one now showing.
        if (activeStoryIdRef.current !== askStoryId) {
          return;
        }
        setQaTurns((prev) =>
          prev.map((turn) => (turn.turn_id === turnId ? { ...turn, phase: "answered", answer } : turn)),
        );
      });
    },
    [askStoryId],
  );

  const itemVariants = prefersReducedMotion ? REVEAL_ITEM_VARIANTS_REDUCED : REVEAL_ITEM_VARIANTS;
  const rootStyle: AccentStyle = { "--accent": story.segment_accent_hex };

  return (
    <div className="flex h-full w-full flex-col bg-background" style={rootStyle}>
      <div ref={scrollContainerRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain pt-safe-t">
        <div className="mx-auto w-full max-w-[420px] px-6 pt-10 pb-16">
          {/* segment label + headline always render immediately from the in-memory
            story, so the panel is never blank while the body loads. */}
          <div className="font-mono text-[11px] uppercase tracking-[0.2em]" style={{ color: "var(--accent)" }}>
            {story.segment_label}
          </div>
          <h1 className="mt-2 font-serif text-[28px] font-bold leading-[1.12] text-white">{story.headline}</h1>

          {loadError !== null ? (
            <p className="mt-8 font-sans text-[14px] leading-relaxed text-white/55">
              Could not load this story&rsquo;s detail. Swipe back and try again.
            </p>
          ) : detail === null ? (
            <p className="mt-8 font-mono text-[12px] uppercase tracking-[0.2em] text-white/35">Loading…</p>
          ) : (
            <motion.div initial="hidden" animate="in" variants={REVEAL_CONTAINER_VARIANTS}>
              {/* (1) chunked Playfair reading body — in chunk_index order (already
                ordered by fetchStoryDetail; do NOT re-sort). */}
              {detail.detail_chunks.map((chunk) => (
                <motion.p
                  key={chunk.chunk_index}
                  data-chunk-index={chunk.chunk_index}
                  variants={itemVariants}
                  className="mt-5 font-serif text-[17px] leading-[1.6] text-white/85"
                >
                  {chunk.chunk_text}
                </motion.p>
              ))}

              {/* (2) key-figure card (renders nothing when null) */}
              <motion.div variants={itemVariants}>
                <KeyFigureCard keyFigure={detail.key_figure} />
              </motion.div>

              {/* (3) trust strip — SP3 stub, fed the already-fetched trust summary */}
              <motion.div variants={itemVariants}>
                <TrustStrip trustSummary={detail.trust_summary} />
              </motion.div>

              {/* (4) "how it developed" timeline — SP4 stub, fed the ordered events */}
              <motion.div variants={itemVariants}>
                <StoryTimelineDrawer timeline={detail.timeline} />
              </motion.div>

              {/* (5) grounded Q&A thread (Phase 2b SP3) — ask/answer turns render
                here in the scrolling body; the composer is pinned below. */}
              <QaThread turns={qaTurns} />
            </motion.div>
          )}
        </div>
      </div>

      {/* Pinned bottom Q&A bar (Phase 2b SP3) — suggested chips + composer over a
          fade-to-canvas gradient. Only shown once the detail (with its suggested
          questions) has loaded; never over the loading/error states. */}
      {detail !== null && loadError === null ? (
        <div className="bg-gradient-to-t from-background from-60% to-transparent px-4 pt-3 pb-safe-b">
          <div className="mx-auto w-full max-w-[420px]">
            <SuggestedQuestionChips
              suggestedQuestions={detail.suggested_questions}
              onAsk={handleAsk}
              disabled={isAnswering}
            />
            <QaComposer onAsk={handleAsk} disabled={isAnswering} />
          </div>
        </div>
      ) : null}
    </div>
  );
}
