import { PhoneShell } from "@/components/PhoneShell";
import { Reel } from "@/components/reel/Reel";

/**
 * Home route — the audio-first karaoke reel is the app's home (port-map §1).
 *
 * Mounts the {@link Reel} inside the iPhone dev frame ({@link PhoneShell}, dropped
 * in the Capacitor build). Static-export friendly: the reel is a client component
 * (`"use client"`); this server-component page just composes the shell.
 */
export default function HomePage() {
  return (
    <PhoneShell>
      <Reel />
    </PhoneShell>
  );
}
