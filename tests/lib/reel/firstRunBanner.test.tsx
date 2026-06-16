import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Phase 7b SP3 — partial-feed metadata + the day-one "past 24 hours" banner.
 *
 * Two suites:
 *  1. `getReelFeed` meta derivation — a partial (24-row) feed yields
 *     `meta.is_partial === true`; a full (30-row) feed yields `false`. The supabase
 *     feed sources are MOCKED at the module boundary (CLAUDE.md mocking rule) so the
 *     test exercises ONLY the row-count → meta mapping, plus the per-date first-run
 *     flag read.
 *  2. `FirstRunBanner` component — renders the EXACT copy with `{n}/{feed_total}`,
 *     and dismiss hides it AND persists (a re-mount stays hidden).
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * (no @testing-library — not a project dependency), mirroring
 * `tests/lib/reel/allCaughtUp.test.tsx`.
 *
 * Rule 9 — WHY each test matters (fails on a real regression, not just text):
 *   - `is_partial` is the gate that decides whether a day-one user sees the honest
 *     "we built from the past 24 hours" banner vs a feed that silently reads as
 *     broken. A 24-row feed MUST be partial; a 30-row feed MUST NOT be — a flipped
 *     comparison fails here.
 *   - The banner copy is a product PROMISE (your full 30 land tomorrow). A stale or
 *     hardcoded denominator regression fails the `{n}/{feed_total}` assertion.
 *   - Dismiss MUST persist: if it didn't, the banner would re-nag on every reload
 *     the same day. The re-mount assertion fails if persistence breaks.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { FirstRunBanner, firstRunBannerDismissedKey } from "@/components/blip/reel/FirstRunBanner";
import { getReelFeed } from "@/lib/feed";
import { firstRunFlagKey } from "@/lib/feed/assembleFirstRunFeed";
import { getDailyFeed, getFeed as getGlobalFeed } from "@/lib/feed/supabaseFeed";
import { FEED_TOTAL } from "@/lib/reel/feedBriefing";
import { getCurrentSession } from "@/lib/supabase/auth";
import type { Story } from "@/types/feed";

vi.mock("@/lib/feed/supabaseFeed", () => ({
  getDailyFeed: vi.fn(),
  getFeed: vi.fn(),
}));

vi.mock("@/lib/supabase/auth", () => ({
  getCurrentSession: vi.fn(),
}));

const mockGetDailyFeed = vi.mocked(getDailyFeed);
const mockGetGlobalFeed = vi.mocked(getGlobalFeed);
const mockGetCurrentSession = vi.mocked(getCurrentSession);

/** A minimal in-memory localStorage stub (mirrors tests/lib/onboardingProfile.test.ts). */
function installLocalStorageStub(): void {
  const store = new Map<string, string>();
  const stub = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
  };
  Object.defineProperty(globalThis, "localStorage", { value: stub, configurable: true, writable: true });
}

/** Build `count` placeholder stories — only the count matters for the meta mapping. */
function makeStories(count: number): Story[] {
  return Array.from({ length: count }, (_unused, index) => ({ digest_id: `s${index}` }) as Story);
}

beforeEach(() => {
  installLocalStorageStub();
  vi.clearAllMocks();
  delete process.env.NEXT_PUBLIC_FEED_SOURCE;
});

describe("getReelFeed meta derivation (Phase 7b SP3)", () => {
  const FEED_DATE = "2026-06-16";

  it("a 24-row first-run feed → is_partial true, is_first_run true, allocated_count 24", async () => {
    window.localStorage.setItem(firstRunFlagKey(FEED_DATE), "1");
    mockGetCurrentSession.mockResolvedValue({ user: { id: "u1" }, access_token: "t" } as never);
    mockGetDailyFeed.mockResolvedValue(makeStories(24));

    const { stories, meta } = await getReelFeed(FEED_DATE);

    expect(stories).toHaveLength(24);
    expect(meta.allocated_count).toBe(24);
    expect(meta.feed_total).toBe(FEED_TOTAL);
    expect(meta.is_partial).toBe(true);
    expect(meta.is_first_run).toBe(true);
  });

  it("a 30-row feed → is_partial false (and no first-run flag → is_first_run false)", async () => {
    mockGetCurrentSession.mockResolvedValue({ user: { id: "u1" }, access_token: "t" } as never);
    mockGetDailyFeed.mockResolvedValue(makeStories(FEED_TOTAL));

    const { meta } = await getReelFeed(FEED_DATE);

    expect(meta.allocated_count).toBe(FEED_TOTAL);
    expect(meta.is_partial).toBe(false);
    expect(meta.is_first_run).toBe(false);
  });

  it("falls back to the global feed (no daily rows) and still reports partial meta", async () => {
    mockGetCurrentSession.mockResolvedValue({ user: { id: "u1" }, access_token: "t" } as never);
    mockGetDailyFeed.mockResolvedValue([]);
    mockGetGlobalFeed.mockResolvedValue(makeStories(5));

    const { stories, meta } = await getReelFeed(FEED_DATE);

    expect(stories).toHaveLength(5);
    expect(meta.is_partial).toBe(true);
  });
});

describe("FirstRunBanner (Phase 7b SP3)", () => {
  const FEED_DATE = "2026-06-16";
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  /** Collapse whitespace so JSX line breaks compare cleanly. */
  function normalizedText(): string {
    return (container.textContent ?? "").replace(/\s+/g, " ").trim();
  }

  it("renders the exact copy with {n}/{feed_total}", () => {
    act(() => {
      root.render(<FirstRunBanner allocatedCount={24} feedTotal={FEED_TOTAL} feedDate={FEED_DATE} />);
    });

    expect(normalizedText()).toContain("Showing you the past 24 hours — 24/30. Your full 30 land tomorrow.");
  });

  it("dismiss hides the banner AND persists across a re-mount", () => {
    act(() => {
      root.render(<FirstRunBanner allocatedCount={24} feedTotal={FEED_TOTAL} feedDate={FEED_DATE} />);
    });
    expect(normalizedText()).toContain("24/30");

    const dismissButton = container.querySelector<HTMLButtonElement>('button[aria-label="Dismiss"]');
    expect(dismissButton).not.toBeNull();
    act(() => {
      dismissButton?.click();
    });

    // Hidden immediately.
    expect(normalizedText()).toBe("");
    // Persisted under the banner's OWN key (not the first-run flag).
    expect(window.localStorage.getItem(firstRunBannerDismissedKey(FEED_DATE))).toBe("1");
    expect(window.localStorage.getItem(firstRunFlagKey(FEED_DATE))).toBeNull();

    // A fresh mount for the same date stays hidden.
    act(() => {
      root.render(<FirstRunBanner allocatedCount={24} feedTotal={FEED_TOTAL} feedDate={FEED_DATE} />);
    });
    expect(normalizedText()).toBe("");
  });
});
