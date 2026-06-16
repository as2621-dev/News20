import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Component tests for BuildYour30's first-run feed wiring (Phase 7b SP2).
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (no @testing-library — not a project dependency), mirroring
 * `tests/lib/onboarding/onboardingFlowSessionSkip.test.tsx`. The allocation persist,
 * the assemble client, and the first-run flag persistence are MOCKED at the module
 * boundary (CLAUDE.md mocking rule); this suite exercises ONLY `handleSave`'s
 * orchestration.
 *
 * Rule 9 — WHY this behavior matters, each test failing on a real regression:
 *   - handleSave must call assembleFirstRunFeed with TODAY's UTC feed date AFTER the
 *     allocation persists — the first-run feed is what lets a new user land on a
 *     populated reel. A regression that dropped the call leaves day-one users empty.
 *   - A REJECTED assemble call must still call onDone() (route to the reel) — the
 *     phase's non-fatal contract: a worker outage must never block finishing
 *     onboarding (global-feed fallback).
 *   - The per-date first-run flag must be persisted ONLY on success — setting it
 *     after a failed assembly would show SP3's "past 24 hours" banner on a feed that
 *     was never built.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { BuildYour30 } from "@/components/onboarding/BuildYour30";
import { assembleFirstRunFeed, markFirstRunFeed, todayUtcFeedDate } from "@/lib/feed/assembleFirstRunFeed";
import { getUserFeedAllocation, saveUserFeedAllocation } from "@/lib/feedAllocation";

vi.mock("@/lib/feedAllocation", () => ({
  getUserFeedAllocation: vi.fn(),
  saveUserFeedAllocation: vi.fn(),
}));

vi.mock("@/lib/feed/assembleFirstRunFeed", () => ({
  assembleFirstRunFeed: vi.fn(),
  markFirstRunFeed: vi.fn(),
  // Reason: keep the real UTC date helper so the date-format assertion is meaningful.
  todayUtcFeedDate: vi.fn(() => "2026-06-16"),
}));

const mockGetUserFeedAllocation = vi.mocked(getUserFeedAllocation);
const mockSaveUserFeedAllocation = vi.mocked(saveUserFeedAllocation);
const mockAssembleFirstRunFeed = vi.mocked(assembleFirstRunFeed);
const mockMarkFirstRunFeed = vi.mocked(markFirstRunFeed);
const mockTodayUtcFeedDate = vi.mocked(todayUtcFeedDate);

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.clearAllMocks();
  // Returning-user seed read resolves to no saved allocation → default 30 seed (savable).
  mockGetUserFeedAllocation.mockResolvedValue([]);
  mockSaveUserFeedAllocation.mockResolvedValue({ persisted_count: 8, deferred_buckets: [] });
  mockTodayUtcFeedDate.mockReturnValue("2026-06-16");
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

/** Render BuildYour30 with a captured onDone, flushing the mount seed effect. */
async function renderBuild(onDone: (segments: unknown) => void): Promise<void> {
  await act(async () => {
    root.render(<BuildYour30 onDone={onDone as never} />);
  });
}

/** Click the "Save this order →" CTA (#cta) and flush the async handleSave. */
async function clickSave(): Promise<void> {
  const cta = container.querySelector<HTMLButtonElement>("#cta");
  if (!cta) {
    throw new Error("Save CTA (#cta) not found");
  }
  await act(async () => {
    cta.click();
  });
  // Flush the chained microtasks (save → assemble → onDone).
  await act(async () => {
    await Promise.resolve();
  });
}

describe("BuildYour30 — first-run feed wiring (Rule 9)", () => {
  it("calls assembleFirstRunFeed with today's UTC feed date after the allocation persists, then onDone", async () => {
    mockAssembleFirstRunFeed.mockResolvedValue({ allocated_count: 24 });
    const onDone = vi.fn();

    await renderBuild(onDone);
    await clickSave();

    // Persist runs first; assemble runs after it; both before onDone.
    expect(mockSaveUserFeedAllocation).toHaveBeenCalledTimes(1);
    expect(mockAssembleFirstRunFeed).toHaveBeenCalledTimes(1);
    expect(mockAssembleFirstRunFeed).toHaveBeenCalledWith("2026-06-16");
    expect(mockSaveUserFeedAllocation.mock.invocationCallOrder[0]).toBeLessThan(
      mockAssembleFirstRunFeed.mock.invocationCallOrder[0],
    );
    expect(mockMarkFirstRunFeed).toHaveBeenCalledWith("2026-06-16");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("still calls onDone when assembleFirstRunFeed rejects (non-fatal worker outage)", async () => {
    mockAssembleFirstRunFeed.mockRejectedValue(new Error("worker unreachable"));
    const onDone = vi.fn();

    await renderBuild(onDone);
    await clickSave();

    expect(mockAssembleFirstRunFeed).toHaveBeenCalledTimes(1);
    // The reel is still reached — onboarding completes despite the worker failure.
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("persists the first-run flag ONLY on success (never when assemble rejects)", async () => {
    mockAssembleFirstRunFeed.mockRejectedValue(new Error("worker unreachable"));
    const onDone = vi.fn();

    await renderBuild(onDone);
    await clickSave();

    expect(mockMarkFirstRunFeed).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
