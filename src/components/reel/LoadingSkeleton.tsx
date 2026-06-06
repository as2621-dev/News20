"use client";

/**
 * LoadingSkeleton — the digest-buffering state shown the instant the reel mounts,
 * before the feed resolves (port-map §2 row 4; ports `enterReelWithLoading`).
 *
 * Mirrors the prototype's loading overlay: the finite segmented top bar (with the
 * already-consumed segments marked `done`), the `blip` wordmark, then poster +
 * caption skeleton blocks low in the frame and the `BUFFERING TODAY'S DIGEST…`
 * mono label. It is a calm placeholder, not an interactive surface.
 *
 * **Shimmer / reduced motion.** SP1's `globals.css` does not carry the
 * prototype's `.sk` shimmer class (it was never ported), so the skeleton blocks
 * use Tailwind's `animate-pulse` instead — equivalent "loading" affordance — with
 * `motion-reduce:animate-none` so the pulse stops under `prefers-reduced-motion`
 * (matching the prototype, which disabled the shimmer under that query).
 */
import { BlipLogo } from "@/components/BlipLogo";
import { FEED_START_INDEX, FEED_TOTAL } from "@/lib/reel/feedBriefing";

/** One pulsing skeleton block (poster/caption stand-in). */
function SkeletonBlock({ className }: { className: string }) {
  return (
    <div
      // motion-reduce:animate-none — kill the pulse under prefers-reduced-motion
      // (the prototype disabled its `.sk` shimmer the same way).
      className={`animate-pulse rounded-[2px] bg-white/5 motion-reduce:animate-none ${className}`}
      aria-hidden="true"
    />
  );
}

/**
 * Render the buffering skeleton. Fills the reel surface; purely presentational
 * (no audio, no gestures) — the reel swaps it for the live stories once `getFeed`
 * resolves.
 */
export function LoadingSkeleton() {
  return (
    <div
      // role="status" is the buffering semantic and natively supports aria-busy +
      // aria-label; the visual skeleton blocks are decorative (aria-hidden).
      role="status"
      aria-busy="true"
      aria-label="Loading today's briefing"
      className="absolute inset-0 z-40 flex flex-col bg-background"
    >
      {/* top chrome: finite bar (consumed segments done) + wordmark */}
      <div className="px-4 pt-safe-t">
        <div className="mb-3 flex gap-[3px]">
          {Array.from({ length: FEED_TOTAL }, (_unused, segmentIndex) => (
            <div
              // biome-ignore lint/suspicious/noArrayIndexKey: fixed-length positional bar; segment index IS the identity.
              key={segmentIndex}
              className={`finite-seg${segmentIndex < FEED_START_INDEX ? " done" : ""}`}
            />
          ))}
        </div>
        <BlipLogo size={20} />
      </div>

      {/* poster + caption skeleton blocks, anchored low like the real reel */}
      <div className="flex flex-1 flex-col justify-end px-5 pb-[120px] pb-safe-b">
        <SkeletonBlock className="mb-4 h-3 w-24" />
        <SkeletonBlock className="mb-2 h-7 w-full" />
        <SkeletonBlock className="mb-8 h-7 w-3/4" />
        <SkeletonBlock className="mb-2 h-9 w-full" />
        <SkeletonBlock className="h-9 w-5/6" />
        <div className="mt-8 text-center font-mono text-[10px] tracking-wide text-white/35">
          BUFFERING TODAY&rsquo;S DIGEST&hellip;
        </div>
      </div>
    </div>
  );
}
