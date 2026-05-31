import { PhoneShell } from "@/components/PhoneShell";
import { Reel } from "@/components/reel/Reel";
import { LayerStack } from "@/components/shell/LayerStack";

/**
 * Home route — the audio-first karaoke reel is the app's home (port-map §1).
 *
 * Composes, outermost-in: the iPhone dev frame ({@link PhoneShell}, dropped in
 * the Capacitor build) → the {@link LayerStack} lateral-layer shell (owns the
 * reel dim/scale-back + the Detail layer state) → the {@link Reel} as the base
 * layer. Detail/Voice are lateral layers OVER the reel, not separate routes, so
 * opening a story never unmounts the reel or kills its audio.
 *
 * Static-export friendly: the reel + shell are client components (`"use
 * client"`); this server-component page just composes them. (Kept as `page.tsx`
 * at `/` — a `(reel)/page.tsx` route group would also resolve to `/` and collide
 * with this file, a build error documented in Phase 1e.)
 */
export default function HomePage() {
  return (
    <PhoneShell>
      <LayerStack>
        <Reel />
      </LayerStack>
    </PhoneShell>
  );
}
