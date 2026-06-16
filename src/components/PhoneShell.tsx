/**
 * PhoneShell — a 393×852 iPhone frame for BROWSER DEV ONLY.
 *
 * This reproduces the prototype `.device` chrome (rounded frame, Dynamic
 * Island, status bar, home indicator) so the app renders like a phone in a
 * desktop browser. Per port-map §1 the real Capacitor build DROPS this entirely
 * — the device + OS status bar replace it. It is deliberately isolated in this
 * one component so it is trivially removable later: render `children` directly
 * and delete this file.
 *
 * @example
 *   <PhoneShell>
 *     <ReelRoot />
 *   </PhoneShell>
 */

import { Capacitor } from "@capacitor/core";

export interface PhoneShellProps {
  /** Status-bar clock text (dev cosmetic only). */
  status_bar_time?: string;
  /** The app content rendered inside the simulated screen. */
  children: React.ReactNode;
}

/**
 * Wraps app content in a simulated iPhone frame with safe-area insets.
 *
 * Safe-area note (port-map §6): the content area pads by
 * `env(safe-area-inset-top, 59px)` / `env(safe-area-inset-bottom, 34px)` so it
 * adapts on a real device but falls back to the prototype's Dynamic Island
 * (59px) and home-indicator (34px) values in a plain browser.
 */
export function PhoneShell({ status_bar_time = "7:18", children }: PhoneShellProps) {
  // Per port-map §1 the native Capacitor build drops the simulated frame entirely:
  // the real device + OS status bar replace it. Content fills the screen, padded
  // by the REAL safe-area insets (viewport-fit=cover is set in layout.tsx).
  if (Capacitor.isNativePlatform()) {
    // Edge-to-edge: the reel paints full-bleed (incl. notch + home-indicator) and
    // owns its own safe-area offsets via env() in blip-flow.css (.top / .r3-bottom).
    return <div className="h-dvh w-full bg-background">{children}</div>;
  }

  return (
    <div className="grid min-h-screen place-items-center bg-background p-6">
      <div className="device">
        <div className="screen">
          {/* Simulated iOS status bar */}
          <div className="statusbar">
            <span>{status_bar_time}</span>
            <span className="sb-right">
              {/* cellular */}
              <svg width="17" height="12" viewBox="0 0 17 12" aria-hidden="true">
                <rect x="0" y="7" width="3" height="5" rx="1" fill="#fff" />
                <rect x="4.5" y="5" width="3" height="7" rx="1" fill="#fff" />
                <rect x="9" y="2.5" width="3" height="9.5" rx="1" fill="#fff" />
                <rect x="13.5" y="0" width="3" height="12" rx="1" fill="#fff" opacity="0.4" />
              </svg>
              {/* wifi */}
              <svg width="16" height="12" viewBox="0 0 16 12" aria-hidden="true">
                <path
                  d="M8 2.5C5.5 2.5 3.3 3.5 1.7 5.1l1.1 1.1C4.1 4.9 5.9 4 8 4s3.9.9 5.2 2.2l1.1-1.1C12.7 3.5 10.5 2.5 8 2.5Z"
                  fill="#fff"
                />
                <path
                  d="M8 6c-1.2 0-2.3.5-3.1 1.3l1.1 1.1C6.5 7.9 7.2 7.5 8 7.5s1.5.4 2 .9l1.1-1.1C10.3 6.5 9.2 6 8 6Z"
                  fill="#fff"
                />
                <circle cx="8" cy="10" r="1.4" fill="#fff" />
              </svg>
              {/* battery */}
              <svg width="26" height="13" viewBox="0 0 26 13" aria-hidden="true">
                <rect x="0.5" y="0.5" width="21" height="12" rx="3" fill="none" stroke="#fff" opacity="0.5" />
                <rect x="2" y="2" width="16" height="9" rx="1.5" fill="#fff" />
                <rect x="23" y="4" width="2" height="5" rx="1" fill="#fff" opacity="0.5" />
              </svg>
            </span>
          </div>

          {/* App content, edge-to-edge — the reel owns its own safe-area offsets
              (env() in blip-flow.css) so its accent tint bleeds into the island +
              home-indicator bands. */}
          <div className="absolute inset-0">{children}</div>

          <div className="island" />
          <div className="home-indicator" />
        </div>
      </div>
    </div>
  );
}
