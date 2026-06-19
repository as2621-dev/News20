import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Component tests for BuildYour30's SAVED-allocation gate (phase-SP4 sub-phase 2).
 *
 * Setup mirrors `buildYour30NoSignalGate.test.tsx` (SP1): React 19 `createRoot` + `act`
 * (no @testing-library), with the allocation read/persist + first-run assemble client
 * MOCKED at the module boundary. Unlike SP1, here `getUserFeedAllocation` resolves to a
 * NON-EMPTY saved allocation so the mount effect actually seeds — the gate under test is
 * the filter that drops a saved category the user no longer backs.
 *
 * Rule 9 — WHY this behavior matters, each test failing on a real regression:
 *   - A returning user whose SAVED 30 contains `sport`, but who now has NO `sport.*` interest
 *     and NO sport source, must NOT get the stale Sport block resurrected — it would overwrite
 *     SP1's gate and re-introduce the phantom block the whole phase removes. If the saved
 *     allocation is seeded UNFILTERED (the pre-SP2 `setSegments(saved)`), the Sport block
 *     renders and this test FAILS. The freed slots are NOT redistributed — the screen drops to
 *     a savable "Fill N more" state (owner decision 2026-06-18: no auto-rescale).
 *   - A saved allocation that is FULLY backed by the user's current signal must seed UNCHANGED
 *     (no regression): every saved block survives the filter and the budget stays full.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { BuildYour30 } from "@/components/onboarding/BuildYour30";
import { getUserFeedAllocation, saveUserFeedAllocation } from "@/lib/feedAllocation";
import type { AllocationSegment } from "@/lib/feedBuckets";

vi.mock("@/lib/feedAllocation", () => ({
  getUserFeedAllocation: vi.fn(),
  saveUserFeedAllocation: vi.fn(),
}));

vi.mock("@/lib/feed/assembleFirstRunFeed", () => ({
  assembleFirstRunFeed: vi.fn(),
  markFirstRunFeed: vi.fn(),
  todayUtcFeedDate: vi.fn(() => "2026-06-19"),
}));

const mockGetUserFeedAllocation = vi.mocked(getUserFeedAllocation);
const mockSaveUserFeedAllocation = vi.mocked(saveUserFeedAllocation);

/** A saved allocation totalling EXACTLY 30 whose blocks include a `sport` block. */
const SAVED_WITH_SPORT: AllocationSegment[] = [
  { bucketId: "ai", count: 6 },
  { bucketId: "tech", count: 6 },
  { bucketId: "geopolitics", count: 6 },
  { bucketId: "business", count: 6 },
  { bucketId: "sport", count: 6 },
];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.clearAllMocks();
  mockSaveUserFeedAllocation.mockResolvedValue({ persisted_count: 0, deferred_buckets: [] });
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

/** Render BuildYour30 with the given backing props, flushing the async mount seed effect. */
async function renderBuild(props: {
  selectedCategoryBuckets?: string[];
  followedSourceBuckets?: string[];
}): Promise<void> {
  await act(async () => {
    root.render(
      <BuildYour30
        onDone={vi.fn() as never}
        selectedCategoryBuckets={(props.selectedCategoryBuckets ?? []) as never}
        followedSourceBuckets={(props.followedSourceBuckets ?? []) as never}
      />,
    );
  });
  // Flush the async mount seed (getUserFeedAllocation → filter → setSegments). Two microtask
  // turns: one for the awaited read, one for the resulting state-commit re-render.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** The category/source block rows actually rendered in the seglist (`.seg .nm` labels). */
function renderedBlockLabels(): string[] {
  return [...container.querySelectorAll<HTMLElement>(".seg .nm")].map((node) => node.textContent?.trim() ?? "");
}

describe("BuildYour30 — saved-allocation gate against current backing (Rule 9)", () => {
  it("DROPS a saved Sport block when the user no longer backs sport (no sport interest, no sport source)", async () => {
    mockGetUserFeedAllocation.mockResolvedValue(SAVED_WITH_SPORT);
    // Current backing: ai/tech/geopolitics/business — but NOT sport.
    await renderBuild({ selectedCategoryBuckets: ["ai", "tech", "geopolitics", "business"], followedSourceBuckets: [] });

    const labels = renderedBlockLabels();
    // The stale Sport block is dropped — it can't resurrect (the bug SP2 fixes).
    expect(labels).not.toContain("Sport");
    // The still-backed blocks survive.
    expect(labels).toEqual(["AI", "Tech", "Geopolitics", "Business"]);
  });

  it("seeds a FULLY-backed saved allocation UNCHANGED (no regression, no drop)", async () => {
    mockGetUserFeedAllocation.mockResolvedValue(SAVED_WITH_SPORT);
    // Current backing covers every saved block, INCLUDING sport.
    await renderBuild({
      selectedCategoryBuckets: ["ai", "tech", "geopolitics", "business", "sport"],
      followedSourceBuckets: [],
    });

    // Every saved block survives the filter, in saved order.
    expect(renderedBlockLabels()).toEqual(["AI", "Tech", "Geopolitics", "Business", "Sport"]);
  });
});
