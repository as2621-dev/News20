import { beforeEach, describe, expect, it, vi } from "vitest";
import { getReelFeed } from "@/lib/feed";
import { getDailyFeed, getLatestFeedDate } from "@/lib/feed/supabaseFeed";
import { getCurrentSession } from "@/lib/supabase/auth";
import type { Story } from "@/types/feed";

/**
 * getReelFeed latest-briefing fallback (bug 2026-07-07).
 *
 * Rule 9 — WHY this behavior matters, not just what it does:
 *   - Opening the app before today's assembly has run must NEVER dead-end on the
 *     "your briefing is being prepared" state when the user HAS an earlier
 *     briefing — stale-but-mine beats an unescapable empty reel (the founder was
 *     locked out of his own 30 with refresh doing nothing).
 *   - The fallback must serve the user's OWN prior day only — a brand-new user
 *     with no briefing at all keeps the honest "being prepared" state (the
 *     owner's no-global-fallback rule, 2026-06-30, stays intact).
 *   - Archive replay passes an EXPLICIT date; that path must stay exact-date —
 *     replaying "2026-06-14" must not silently serve some other day.
 */

vi.mock("@/lib/feed/supabaseFeed", () => ({
  getDailyFeed: vi.fn(),
  getFeed: vi.fn(),
  getLatestFeedDate: vi.fn(),
}));

vi.mock("@/lib/supabase/auth", () => ({
  getCurrentSession: vi.fn(),
}));

const mockGetDailyFeed = vi.mocked(getDailyFeed);
const mockGetLatestFeedDate = vi.mocked(getLatestFeedDate);
const mockGetCurrentSession = vi.mocked(getCurrentSession);

/** Build `count` placeholder stories — only the count matters here. */
function makeStories(count: number): Story[] {
  return Array.from({ length: count }, (_unused, index) => ({ digest_id: `s${index}` }) as Story);
}

beforeEach(() => {
  vi.clearAllMocks();
  delete process.env.NEXT_PUBLIC_FEED_SOURCE;
  mockGetCurrentSession.mockResolvedValue({ user: { id: "u1" }, access_token: "t" } as never);
});

describe("getReelFeed latest-briefing fallback", () => {
  it("happy path: today empty + a prior briefing exists → serves the prior briefing", async () => {
    const priorStories = makeStories(30);
    mockGetDailyFeed.mockImplementation(async (_userId, feedDate) => (feedDate === "2026-07-06" ? priorStories : []));
    mockGetLatestFeedDate.mockResolvedValue("2026-07-06");

    const { stories } = await getReelFeed();

    expect(stories).toHaveLength(30);
    expect(mockGetLatestFeedDate).toHaveBeenCalledWith("u1", expect.any(String));
    expect(mockGetDailyFeed).toHaveBeenCalledWith("u1", "2026-07-06");
  });

  it("failure case: no briefing on ANY day → empty feed (honest 'being prepared', no global pool)", async () => {
    mockGetDailyFeed.mockResolvedValue([]);
    mockGetLatestFeedDate.mockResolvedValue(null);

    const { stories, meta } = await getReelFeed();

    expect(stories).toHaveLength(0);
    expect(meta.allocated_count).toBe(0);
  });

  it("edge case: an EXPLICIT date (Archive replay) never falls back to another day", async () => {
    mockGetDailyFeed.mockResolvedValue([]);
    mockGetLatestFeedDate.mockResolvedValue("2026-07-06");

    const { stories } = await getReelFeed("2026-06-14");

    expect(stories).toHaveLength(0);
    expect(mockGetLatestFeedDate).not.toHaveBeenCalled();
  });

  it("edge case: prior date resolves but its rows are gone → still the honest empty state", async () => {
    mockGetDailyFeed.mockResolvedValue([]);
    mockGetLatestFeedDate.mockResolvedValue("2026-07-06");

    const { stories } = await getReelFeed();

    expect(stories).toHaveLength(0);
  });
});
