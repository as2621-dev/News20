"use client";

/**
 * ArchiveScreen — the "Archive · Past briefings" library surface. Lists the user's
 * past daily briefings (each day = that day's ~30-story reel, accumulating in
 * `daily_feeds`), newest first. Tapping a day re-points the reel at that date.
 *
 * Kept deliberately simple (owner decision 2026-06-16): NO listened%/progress bar
 * and NO mini-player — just the day, its lead headline, source count and length.
 *
 * Renders as a flex column (header + scroll) so it slots under {@link AppShell}'s
 * `.app-library` with the tab bar pinned below.
 */

import { type CSSProperties, useEffect, useState } from "react";
import { type BriefingDay, listUserBriefings } from "@/lib/archive/listBriefings";

export interface ArchiveScreenProps {
  /** Replay a past day: hand its ISO `YYYY-MM-DD` back to the shell to re-point the reel. */
  onOpenDay: (feedDate: string) => void;
}

/** A weekday-short formatter reused across rows (Mon/Tue/…); locale-default. */
const WEEKDAY_FORMAT = new Intl.DateTimeFormat(undefined, { weekday: "short" });
/** "May 29"-style date formatter for the row meta line. */
const MONTH_DAY_FORMAT = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

/**
 * Relative day label: "Today" / "Yesterday" / weekday (within a week) / "May 27".
 * Compares calendar days in local time so a same-day briefing reads "Today".
 */
function dayLabel(feedDate: string, todayIso: string): string {
  if (feedDate === todayIso) {
    return "Today";
  }
  const parsed = new Date(`${feedDate}T00:00:00`);
  const today = new Date(`${todayIso}T00:00:00`);
  const daysAgo = Math.round((today.getTime() - parsed.getTime()) / 86_400_000);
  if (daysAgo === 1) {
    return "Yesterday";
  }
  if (daysAgo >= 2 && daysAgo <= 6) {
    return WEEKDAY_FORMAT.format(parsed);
  }
  return MONTH_DAY_FORMAT.format(parsed);
}

/** Format a duration (ms) as the design's "29m 40s". */
function formatDuration(totalMs: number): string {
  const totalSeconds = Math.round(totalMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

/** Today's `feed_date` (ISO) computed once per render — drives the relative labels. */
function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Render the archive list against the user's real past briefings. */
export function ArchiveScreen({ onOpenDay }: ArchiveScreenProps) {
  // null until the first load resolves (loading → list/empty swap).
  const [briefings, setBriefings] = useState<BriefingDay[] | null>(null);

  useEffect(() => {
    let isMounted = true;
    listUserBriefings().then((days) => {
      if (isMounted) {
        setBriefings(days);
      }
    });
    return () => {
      isMounted = false;
    };
  }, []);

  const today = todayIso();

  return (
    <>
      <div className="art-top" />
      <div className="art-scroll">
        <div className="kicker">Archive</div>
        <h1 className="htitle">Past briefings</h1>
        <p className="hsub">Every morning you&apos;ve caught up. Tap to listen again.</p>

        {briefings === null ? (
          <p className="lib-empty">Loading your past briefings…</p>
        ) : briefings.length === 0 ? (
          <p className="lib-empty">Your past briefings will appear here as they accumulate.</p>
        ) : (
          briefings.map((day, index) => (
            <div key={day.feedDate}>
              <div className="day-group" style={{ "--accent": day.accentHex ?? "#94a3b8" } as CSSProperties}>
                <div className="day-head">
                  <span className="dn">
                    <span className="sd" />
                    {dayLabel(day.feedDate, today)}
                  </span>
                  <span className="dur">{formatDuration(day.totalDurationMs)}</span>
                </div>
                <button type="button" className="brief" onClick={() => onOpenDay(day.feedDate)}>
                  <div className="bh">{day.leadHeadline}</div>
                  <div className="bm">
                    <span className="bmeta">
                      {MONTH_DAY_FORMAT.format(new Date(`${day.feedDate}T00:00:00`))} · {day.storyCount} stories
                    </span>
                  </div>
                </button>
              </div>
              {index < briefings.length - 1 ? <div className="day-div" /> : null}
            </div>
          ))
        )}
      </div>
    </>
  );
}
