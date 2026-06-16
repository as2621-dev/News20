import { describe, expect, it, vi } from "vitest";
import { listUserBriefings } from "@/lib/archive/listBriefings";

/**
 * listUserBriefings — the read backing the Archive "Past briefings" list.
 *
 * WHY these tests (Rule 9): the Archive's contract is "one row per past day, the
 * lead story summarizes it, newest first". The flat (date, position) rows MUST
 * collapse to one summary per day, the FIRST row of a date (position 1) must win
 * as the lead headline + day accent, and counts/durations must aggregate across
 * the day. A grouping regression would either duplicate days or attribute the
 * wrong headline/color to a day. Signed-out / error reads degrade to [] so the
 * surface shows an honest empty state (Rule 12).
 *
 * Mocks Supabase at the client boundary (CLAUDE.md mocking strategy).
 */

/** One daily_feeds ⋈ stories row as the embedded select returns it. */
interface Row {
  feed_date: string;
  feed_position: number;
  stories: {
    story_headline: string;
    segments: { segment_accent_hex: string };
    digests: { digest_duration_ms: number; digest_is_current: boolean }[];
  } | null;
}

/** A chainable fake of the single read listUserBriefings performs, plus auth.getUser. */
function makeClient(options: { user: { id: string } | null; rows?: Row[]; error?: { message: string } }) {
  const builder = {
    select: () => builder,
    eq: () => builder,
    order: () => builder,
    returns: vi.fn().mockResolvedValue({ data: options.rows ?? [], error: options.error ?? null }),
  };
  return {
    auth: { getUser: vi.fn().mockResolvedValue({ data: { user: options.user }, error: null }) },
    from: () => builder,
  } as never;
}

/** Build a row quickly (current digest, fixed accent unless overridden). */
function row(date: string, position: number, headline: string, durationMs: number, accent = "#EF4444"): Row {
  return {
    feed_date: date,
    feed_position: position,
    stories: {
      story_headline: headline,
      segments: { segment_accent_hex: accent },
      digests: [{ digest_duration_ms: durationMs, digest_is_current: true }],
    },
  };
}

describe("listUserBriefings (daily_feeds → per-day summaries)", () => {
  it("groups flat rows into one summary per day, lead headline + accent from position 1", async () => {
    // Rows arrive date-desc, position-asc (as ordered by the query).
    const client = makeClient({
      user: { id: "user-1" },
      rows: [
        row("2026-06-16", 1, "Lead of today", 600_000, "#22C55E"), // green lead → day accent
        row("2026-06-16", 2, "Second of today", 400_000, "#EF4444"),
        row("2026-06-15", 1, "Lead of yesterday", 500_000, "#22D3EE"),
      ],
    });

    const days = await listUserBriefings(client);

    expect(days).toHaveLength(2);
    expect(days[0]).toEqual({
      feedDate: "2026-06-16",
      leadHeadline: "Lead of today",
      accentHex: "#22C55E",
      storyCount: 2,
      totalDurationMs: 1_000_000,
    });
    expect(days[1].feedDate).toBe("2026-06-15");
    expect(days[1].storyCount).toBe(1);
    expect(days[1].leadHeadline).toBe("Lead of yesterday");
  });

  it("returns [] for a user with no past briefings (honest empty state)", async () => {
    const client = makeClient({ user: { id: "user-1" }, rows: [] });
    await expect(listUserBriefings(client)).resolves.toEqual([]);
  });

  it("returns [] when signed out, without reading daily_feeds", async () => {
    const client = makeClient({ user: null });
    await expect(listUserBriefings(client)).resolves.toEqual([]);
  });

  it("returns [] (never throws) when the read errors", async () => {
    const client = makeClient({ user: { id: "user-1" }, error: { message: "rls denied" } });
    await expect(listUserBriefings(client)).resolves.toEqual([]);
  });
});
