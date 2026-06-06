"use client";

/**
 * AllCaughtUp — the signature "You're all caught up" finish line (port-map §2
 * row 10; ports `showCaughtUp`).
 *
 * Reaching the end of the finite briefing is the reward, not an empty feed. This
 * screen shows the `FEED_TOTAL / FEED_TOTAL` finish counter (mono), the
 * Playfair headline, the "come back tomorrow — no infinite scroll" line, and a
 * single replay CTA that restarts the briefing from story 1.
 *
 * **Scope (M1).** The prototype's "while you were out / 1 followed story has an
 * update" card is deliberately OMITTED — it depends on the follow/timeline data
 * model that lands in M3 (out of scope here). A plain replay is the whole CTA.
 *
 * **Motion.** The fade-up entrance uses framer-motion so it is automatically
 * disabled under `prefers-reduced-motion` via {@link useReducedMotion} (the
 * prototype's `.fade-up` class isn't in SP1's globals.css, so we drive it here).
 */
import { motion, useReducedMotion } from "framer-motion";
import { FEED_TOTAL } from "@/lib/reel/feedBriefing";

export interface AllCaughtUpProps {
  /** Restart the briefing from the first story (scroll to 0 + replay digest-1). */
  onReplay: () => void;
}

/**
 * Render the all-caught-up finish line. Covers the reel surface; the only
 * interactive control is the replay CTA.
 */
export function AllCaughtUp({ onReplay }: AllCaughtUpProps) {
  const prefersReducedMotion = useReducedMotion();

  // Reason: a single fade-up variant reused for every line; reduced motion makes
  // it a no-op (no offset, instant) so the screen simply appears.
  const fadeUp = {
    hidden: prefersReducedMotion ? { opacity: 1, y: 0 } : { opacity: 0, y: 16 },
    shown: { opacity: 1, y: 0 },
  };

  return (
    <motion.div
      className="absolute inset-0 z-30 flex flex-col items-center justify-center bg-background px-8"
      initial="hidden"
      animate="shown"
      transition={{
        staggerChildren: prefersReducedMotion ? 0 : 0.08,
        delayChildren: prefersReducedMotion ? 0 : 0.05,
      }}
    >
      {/* faint accent wash behind the finish line, matching the prototype */}
      <div className="ambient" aria-hidden="true" style={{ opacity: 0.4 }} />
      <div className="reel-scrim" aria-hidden="true" />

      <div className="relative z-10 mx-auto w-full max-w-[300px] text-center">
        <motion.div variants={fadeUp} className="mb-6 font-mono text-[12px] uppercase tracking-[0.2em] text-white/45">
          {FEED_TOTAL} / {FEED_TOTAL} · DONE
        </motion.div>

        <motion.h1 variants={fadeUp} className="font-serif text-[40px] font-bold leading-[1.08] text-white">
          You&rsquo;re all
          <br />
          caught up.
        </motion.h1>

        <motion.p variants={fadeUp} className="mt-5 font-sans text-[15px] leading-relaxed text-white/55">
          That&rsquo;s the whole world today. No infinite scroll waiting &mdash; come back tomorrow.
        </motion.p>

        <motion.div variants={fadeUp} className="my-7 h-px w-12 bg-white/15" aria-hidden="true" />

        <motion.button
          variants={fadeUp}
          type="button"
          onClick={onReplay}
          className="font-mono text-[11px] tracking-wide text-white/40 transition-transform active:scale-95"
        >
          &#8635; REPLAY TODAY&rsquo;S BRIEFING
        </motion.button>
      </div>
    </motion.div>
  );
}
