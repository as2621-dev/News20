/**
 * Archive data-access — list the authed user's PAST daily briefings for the
 * "Archive · Past briefings" surface. Each day's briefing is the ~30-story window
 * the daily pipeline allocated into `daily_feeds`; days accumulate as the cron
 * runs, and the Archive lists them newest-first.
 *
 * Kept deliberately simple (owner decision 2026-06-16): NO listened%/progress and
 * NO playback state — just the day, its lead headline, story count and total
 * length. Tapping a day re-points the reel at that `feed_date` (see AppShell).
 *
 * Owner-scoped under the `daily_feeds_owner_select` RLS (the browser client
 * carries the session). A signed-out read returns `[]` so the surface still
 * paints an honest empty state rather than throwing (Rule 12).
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** One past day's briefing, summarized for the Archive list. */
export interface BriefingDay {
  /** The `feed_date` (ISO `YYYY-MM-DD`) — passed back to re-point the reel. */
  feedDate: string;
  /** The lead (position-1) story's headline — the day's one-line summary. */
  leadHeadline: string;
  /** The lead story's segment accent hex — colors the day-group dot. */
  accentHex: string | null;
  /** How many stories that day's briefing held. */
  storyCount: number;
  /** Total narration length across the day's stories (ms). */
  totalDurationMs: number;
}

/** PostgREST embed shapes for the daily_feeds ⋈ stories ⋈ segments ⋈ digests read. */
interface BriefingFeedRow {
  feed_date: string;
  feed_position: number;
  stories: {
    story_headline: string;
    segments: { segment_accent_hex: string } | { segment_accent_hex: string }[] | null;
    digests: { digest_duration_ms: number; digest_is_current: boolean }[];
  } | null;
}

/** A `segments` embed as PostgREST returns it (object) or the test mock supplies it (array). */
type SegmentEmbed = { segment_accent_hex: string } | { segment_accent_hex: string }[] | null;

/** Pull the single related segment row out of PostgREST's object-or-array embed. */
function segmentAccentOf(segments: SegmentEmbed): string | null {
  if (!segments) {
    return null;
  }
  const row = Array.isArray(segments) ? segments[0] : segments;
  return row?.segment_accent_hex ?? null;
}

/** The current digest's duration (ms), or 0 when none is marked current. */
function durationOf(digests: { digest_duration_ms: number; digest_is_current: boolean }[]): number {
  const current = digests.find((digest) => digest.digest_is_current) ?? digests[0];
  return current?.digest_duration_ms ?? 0;
}

/**
 * List the authed user's past briefings, newest day first.
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns One {@link BriefingDay} per day the user has a `daily_feeds` window for.
 *
 * @example
 * const days = await listUserBriefings();
 * days[0].feedDate; // the most recent day
 */
export async function listUserBriefings(client: SupabaseClient = getSupabaseBrowserClient()): Promise<BriefingDay[]> {
  const { data: authData, error: authError } = await client.auth.getUser();
  if (authError || !authData.user) {
    logger.info("list_user_briefings_signed_out", { reason: authError?.message ?? "no_session" });
    return [];
  }

  const { data, error } = await client
    .from("daily_feeds")
    .select(
      "feed_date,feed_position,stories!inner(story_headline,segments(segment_accent_hex)," +
        "digests!inner(digest_duration_ms,digest_is_current))",
    )
    .eq("feed_user_id", authData.user.id)
    .order("feed_date", { ascending: false })
    .order("feed_position", { ascending: true })
    .returns<BriefingFeedRow[]>();

  if (error) {
    logger.error("list_user_briefings_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm migration 0003 applied and daily_feeds↔stories is readable under the owner RLS.",
    });
    return [];
  }

  // Group the flat (date, position) rows into one summary per day. Rows arrive
  // date-desc then position-asc, so the first row seen for a date is its lead.
  const byDate = new Map<string, BriefingDay>();
  for (const row of data ?? []) {
    if (!row.stories) {
      continue;
    }
    const existing = byDate.get(row.feed_date);
    if (existing) {
      existing.storyCount += 1;
      existing.totalDurationMs += durationOf(row.stories.digests);
      continue;
    }
    byDate.set(row.feed_date, {
      feedDate: row.feed_date,
      leadHeadline: row.stories.story_headline,
      accentHex: segmentAccentOf(row.stories.segments),
      storyCount: 1,
      totalDurationMs: durationOf(row.stories.digests),
    });
  }

  const briefings = [...byDate.values()];
  logger.info("list_user_briefings_completed", { day_count: briefings.length });
  return briefings;
}
