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

import { firstRunFlagKey } from "@/lib/feed/assembleFirstRunFeed";
import { getFeed as getFixtureFeed } from "@/lib/feed/fixtureFeed";
import { getDailyFeed, getFeed as getGlobalFeed } from "@/lib/feed/supabaseFeed";
import { logger } from "@/lib/logger";
import { FEED_TOTAL } from "@/lib/reel/feedBriefing";
import { getCurrentSession } from "@/lib/supabase/auth";
import type { ReelFeedResult, Story } from "@/types/feed";

/** The client-local `feed_date` (ISO `YYYY-MM-DD`) for the per-user feed read. */
function todayFeedDate(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Read SP2's per-date first-run flag for `feedDate` (`blip:first-run:<date>` === `"1"`).
 *
 * Client-only: `localStorage` is unavailable during SSR/static export, so a missing
 * `window` (or any read failure) yields `false` — the banner simply won't gate on.
 *
 * @param feedDate - The feed date whose flag to read (`YYYY-MM-DD`).
 * @returns True only when the flag is present for `feedDate` in this browser.
 */
function readIsFirstRun(feedDate: string): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage.getItem(firstRunFlagKey(feedDate)) === "1";
  } catch {
    // Reason: private-mode / disabled storage must not blank the reel — treat an
    // unreadable flag as "not first-run" (the banner is a nice-to-have, not load-bearing).
    return false;
  }
}

/**
 * Build the {@link ReelFeedResult} from the resolved rows for `feedDate`.
 *
 * `allocated_count` is the LIVE row count (not a stored value); `is_partial` is
 * `allocated_count < FEED_TOTAL`; `is_first_run` reads SP2's per-date flag.
 */
function toReelFeedResult(stories: Story[], feedDate: string): ReelFeedResult {
  const allocatedCount = stories.length;
  return {
    stories,
    meta: {
      allocated_count: allocatedCount,
      feed_total: FEED_TOTAL,
      is_partial: allocatedCount < FEED_TOTAL,
      is_first_run: readIsFirstRun(feedDate),
    },
  };
}

/**
 * Resolve the reel feed from the best available source (see module docs).
 *
 * @param feedDate - Optional ISO `YYYY-MM-DD` to load a SPECIFIC day's briefing
 *   (the Archive "tap a day → replay" path). Omitted → today's briefing.
 * @returns The stories to play plus their {@link ReelFeedResult.meta} (partial /
 *   first-run signals for the day-one banner).
 *
 * @example
 * const { stories, meta } = await getReelFeed();         // today
 * if (meta.is_first_run && meta.is_partial) { ... }      // show the past-24h banner
 * const past = await getReelFeed("2026-06-14");          // a specific archived day
 */
export async function getReelFeed(feedDate?: string): Promise<ReelFeedResult> {
  const requestedDate = feedDate ?? todayFeedDate();

  if (process.env.NEXT_PUBLIC_FEED_SOURCE === "fixtures") {
    return toReelFeedResult(await getFixtureFeed(), requestedDate);
  }

  try {
    const session = await getCurrentSession();
    if (session?.user?.id) {
      const personalized = await getDailyFeed(session.user.id, requestedDate);
      if (personalized.length > 0) {
        return toReelFeedResult(personalized, requestedDate);
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

  return toReelFeedResult(await getGlobalFeed(), requestedDate);
}
