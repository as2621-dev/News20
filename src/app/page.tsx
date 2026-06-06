import { BlipReel } from "@/components/blip/reel/BlipReel";
import { PhoneShell } from "@/components/PhoneShell";

/**
 * Home route — the Blip Flow Stage-4 audio-first karaoke reel is the app's home
 * (port-map §1; the route the onboarding flow lands on via `router.push("/")`).
 *
 * Composes, outermost-in: the iPhone dev frame ({@link PhoneShell}, dropped in the
 * Capacitor build) → {@link BlipReel}. BlipReel owns its own ask sheet + article
 * overlays as root singletons (the Stage-4 unified model), so the legacy
 * {@link LayerStack} Detail/Voice lateral-layer shell is no longer composed here.
 *
 * Static-export friendly: the reel is a client component (`"use client"`); this
 * server-component page just composes it. (Kept as `page.tsx` at `/` — a
 * `(reel)/page.tsx` route group would also resolve to `/` and collide with this
 * file, a build error documented in Phase 1e.)
 */
export default function HomePage() {
  return (
    <PhoneShell>
      <BlipReel />
    </PhoneShell>
  );
}
