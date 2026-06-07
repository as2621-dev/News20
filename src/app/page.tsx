import { AppRouter } from "@/components/AppRouter";

/**
 * Home route (`/`) — the app's gated entry (Phase 4b SP2).
 *
 * Delegates to {@link AppRouter}, which resolves the auth/onboarding gate before
 * mounting anything: a signed-in onboarded user gets the Blip Flow Stage-4 reel
 * (wrapped in the {@link PhoneShell} dev frame, dropped in the Capacitor build);
 * everyone else is routed to `/onboarding`. The reel is never mounted until the
 * gate clears, so there is no flash of the reel for a signed-out / un-onboarded
 * visitor.
 *
 * Static-export friendly: this server-component page just composes the client
 * {@link AppRouter}; the gate itself resolves in the browser. (Kept as `page.tsx`
 * at `/` — a `(reel)/page.tsx` route group would also resolve to `/` and collide
 * with this file, a build error documented in Phase 1e.)
 */
export default function HomePage() {
  return <AppRouter />;
}
