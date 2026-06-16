/**
 * Feed provider selector (Phase 4b SP1) — the single seam the reel imports.
 *
 * Chooses the reel's data source at load time, so the reel component never knows
 * which provider it got:
 *  - `NEXT_PUBLIC_FEED_SOURCE="fixtures"` → the bundled M0 fixtures (dev only).
 *  - an authed user WITH a `daily_feeds` row → their per-user personalized feed.
 *  - otherwise → the global seeded Supabase feed (the live `stories` table).
 *
 * The per-user path falls back to the global feed when the user has no
 * `daily_feeds` yet (e.g. before the daily cron has allocated them), so a signed
 * in-but-unallocated user still sees a non-empty reel. The fixture provider is
 * kept (behind the env flag) so a no-network dev loop still works (Rule 3 — don't
 * delete the fixture seam).
 */

import { getFeed as getFixtureFeed } from "@/lib/feed/fixtureFeed";
import { getDailyFeed, getFeed as getGlobalFeed } from "@/lib/feed/supabaseFeed";
import { logger } from "@/lib/logger";
import { getCurrentSession } from "@/lib/supabase/auth";
import type { Story } from "@/types/feed";

/** The client-local `feed_date` (ISO `YYYY-MM-DD`) for the per-user feed read. */
function todayFeedDate(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Resolve the reel feed from the best available source (see module docs).
 *
 * @param feedDate - Optional ISO `YYYY-MM-DD` to load a SPECIFIC day's briefing
 *   (the Archive "tap a day → replay" path). Omitted → today's briefing.
 * @returns The stories to play, as canonical {@link Story}[].
 *
 * @example
 * const stories = await getReelFeed();                 // today
 * const past = await getReelFeed("2026-06-14");        // a specific archived day
 */
export async function getReelFeed(feedDate?: string): Promise<Story[]> {
  if (process.env.NEXT_PUBLIC_FEED_SOURCE === "fixtures") {
    return getFixtureFeed();
  }

  const requestedDate = feedDate ?? todayFeedDate();
  try {
    const session = await getCurrentSession();
    if (session?.user?.id) {
      const personalized = await getDailyFeed(session.user.id, requestedDate);
      if (personalized.length > 0) {
        return personalized;
      }
      logger.info("reel_feed_fallback_global", {
        reason: "no_daily_feeds_for_user",
        feed_date: requestedDate,
        fix_suggestion: "The daily cron has not allocated this user/date yet; serving the global seed.",
      });
    }
  } catch (sessionError: unknown) {
    // Reason: a session/daily-feed read failure must not blank the reel — fall
    // back to the always-present global seeded feed (Rule 12: degrade, don't crash).
    logger.error("reel_feed_user_path_failed", {
      error_message: sessionError instanceof Error ? sessionError.message : "unknown",
      fix_suggestion: "Falling back to the global seeded feed.",
    });
  }

  return getGlobalFeed();
}
