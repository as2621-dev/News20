"use client";

/**
 * FirstRunBanner — the day-one "past 24 hours" partial-feed notice (Phase 7b SP3).
 *
 * A brand-new user's niche interests may not yet have a full 30 stories in the
 * catalog, so onboarding assembles whatever IS available (e.g. 24/30). On that
 * first-run, partial feed the reel mounts this dismissible top banner so the
 * shorter feed reads as honest ("we built from the past 24 hours") rather than
 * broken. It NEVER renders on a full feed or a non-first-run feed — that gating
 * lives in {@link BlipReel}; this component only owns its own dismiss persistence.
 *
 * **Dismiss persistence.** Tapping the close control persists a per-date flag under
 * this component's OWN key (`blip:first-run-banner-dismissed:<feed_date>`) so the
 * banner shows ONCE and stays hidden across reloads that same day. It deliberately
 * does NOT touch SP2's `blip:first-run:*` flag (clearing that would lose the
 * first-run signal mid-day).
 *
 * Client-only: `localStorage` is `window`-guarded for SSR / static export.
 */
import { useEffect, useState } from "react";

/** The localStorage key prefix for this banner's per-date "dismissed" flag. */
const BANNER_DISMISSED_PREFIX = "blip:first-run-banner-dismissed:";

/**
 * Build the dismiss-flag key for a feed date.
 *
 * @param feedDate - The feed date the banner is shown for (`YYYY-MM-DD`).
 * @returns The namespaced key, e.g. `blip:first-run-banner-dismissed:2026-06-16`.
 */
export function firstRunBannerDismissedKey(feedDate: string): string {
  return `${BANNER_DISMISSED_PREFIX}${feedDate}`;
}

/** Read the persisted dismiss flag (client-only; missing/unreadable → not dismissed). */
function readDismissed(feedDate: string): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage.getItem(firstRunBannerDismissedKey(feedDate)) === "1";
  } catch {
    // Reason: private-mode/disabled storage must not crash the reel — treat as not dismissed.
    return false;
  }
}

/** Persist the dismiss flag (client-only; a storage failure is swallowed — harmless). */
function persistDismissed(feedDate: string): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(firstRunBannerDismissedKey(feedDate), "1");
  } catch {
    // Reason: storage unavailable (private mode/quota); the banner re-appears next
    // mount but that is harmless — better than crashing the reel.
  }
}

export interface FirstRunBannerProps {
  /** Stories actually resolved for the feed (the `{n}` shown to the user). */
  allocatedCount: number;
  /** The finite-briefing target (`FEED_TOTAL`) — the `/30` denominator. */
  feedTotal: number;
  /** The feed date this banner is for (`YYYY-MM-DD`) — keys the dismiss flag. */
  feedDate: string;
}

/**
 * Render the dismissible day-one partial-feed banner.
 *
 * Copy (exact): `Showing you the past 24 hours — {n}/30. Your full 30 land
 * tomorrow.` where `{n}` = {@link FirstRunBannerProps.allocatedCount} and `30` =
 * {@link FirstRunBannerProps.feedTotal} (rendered from the constant so it stays
 * correct if the cap ever changes). Returns `null` once dismissed for this date.
 */
export function FirstRunBanner({ allocatedCount, feedTotal, feedDate }: FirstRunBannerProps) {
  // Lazy-init from persisted state so a same-day reload after dismiss stays hidden.
  const [isDismissed, setIsDismissed] = useState<boolean>(() => readDismissed(feedDate));

  // Re-read the persisted flag if the feed date changes under the mounted banner.
  useEffect(() => {
    setIsDismissed(readDismissed(feedDate));
  }, [feedDate]);

  if (isDismissed) {
    return null;
  }

  /** Hide the banner and persist the dismissal so it never shows again this date. */
  function handleDismiss(): void {
    persistDismissed(feedDate);
    setIsDismissed(true);
  }

  return (
    <div
      role="status"
      className="pointer-events-auto absolute inset-x-3 top-3 z-40 flex items-center gap-3 rounded-2xl border border-white/10 bg-black/70 px-4 py-3 backdrop-blur-md"
    >
      <p className="flex-1 font-sans text-[13px] leading-snug text-white/80">
        Showing you the past 24 hours — {allocatedCount}/{feedTotal}. Your full {feedTotal} land tomorrow.
      </p>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss"
        className="shrink-0 rounded-full px-2 py-1 font-mono text-[11px] tracking-wide text-white/45 transition-transform active:scale-95"
      >
        &times;
      </button>
    </div>
  );
}
