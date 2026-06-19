import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Component tests for BuildYour30's no-signal gate (phase-SP4 sub-phase 1).
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (no @testing-library — not a project dependency), mirroring
 * `tests/lib/onboarding/buildYour30FirstRun.test.tsx`. The allocation read/persist and
 * the first-run assemble client are MOCKED at the module boundary (CLAUDE.md mocking
 * rule); the saved-allocation read resolves to `[]` so the mount effect never seeds.
 *
 * Rule 9 — WHY this behavior matters, each test failing on a real regression:
 *   - With ZERO picker follows AND ZERO sources there is nothing the user backs, so the
 *     screen must render NO category blocks and the Add sheet must offer NOTHING. The
 *     old behavior here seeded the FULL default allocation (every bucket) — phantom
 *     Sport/Culture the user never chose. A regression that reverts to `buildDefaultSegments()`
 *     here, or restores the `DESIGN_BUCKET_IDS` Add-sheet fallback, FAILS these assertions.
 *   - With `["sport"]` follows ONLY the Sport block seeds — proving the gate filters to
 *     real backing, not all buckets.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { BuildYour30 } from "@/components/onboarding/BuildYour30";
import { getUserFeedAllocation, saveUserFeedAllocation } from "@/lib/feedAllocation";

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

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.clearAllMocks();
  // No saved allocation → the mount effect leaves the initial (gated) seed untouched.
  mockGetUserFeedAllocation.mockResolvedValue([]);
  mockSaveUserFeedAllocation.mockResolvedValue({ persisted_count: 0, deferred_buckets: [] });
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

/** Render BuildYour30 with the given backing props, flushing the mount seed effect. */
async function renderBuild(props: {
  selectedCategoryBuckets?: string[];
  followedSourceBuckets?: string[];
  onSkip?: () => void;
}): Promise<void> {
  await act(async () => {
    root.render(
      <BuildYour30
        onDone={vi.fn() as never}
        onSkip={props.onSkip}
        selectedCategoryBuckets={(props.selectedCategoryBuckets ?? []) as never}
        followedSourceBuckets={(props.followedSourceBuckets ?? []) as never}
      />,
    );
  });
  // Flush the async mount seed (getUserFeedAllocation → setSegments).
  await act(async () => {
    await Promise.resolve();
  });
}

/** The category/source block rows actually rendered in the seglist (`.seg .nm` labels). */
function renderedBlockLabels(): string[] {
  return [...container.querySelectorAll<HTMLElement>(".seg .nm")].map((node) => node.textContent?.trim() ?? "");
}

/** The Add-sheet chip labels actually offered (`.sheet2 .bk`). */
function renderedAddChipLabels(): string[] {
  return [...container.querySelectorAll<HTMLElement>(".sheet2 .bk")].map((node) => node.textContent?.trim() ?? "");
}

describe("BuildYour30 — no-signal selected-only gate (Rule 9)", () => {
  it("renders NO category blocks and offers NOTHING in the Add sheet when there is no signal", async () => {
    await renderBuild({ selectedCategoryBuckets: [], followedSourceBuckets: [] });

    // No allocation blocks seeded — the old default seed (every bucket) is gone.
    expect(renderedBlockLabels()).toEqual([]);
    // The Add sheet offers nothing — the DESIGN_BUCKET_IDS fallback is gone.
    expect(renderedAddChipLabels()).toEqual([]);
    // The empty-state CTA region renders instead of the allocation chrome.
    expect(container.querySelector("#noSignalEmpty")).not.toBeNull();
  });

  it("wires the empty-state CTA to onSkip (a real route, not a dead button)", async () => {
    const onSkip = vi.fn();
    await renderBuild({ selectedCategoryBuckets: [], followedSourceBuckets: [], onSkip });

    const cta = container.querySelector<HTMLButtonElement>("#pickInterestsCta");
    expect(cta).not.toBeNull();
    await act(async () => {
      cta?.click();
    });
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it("renders ONLY the Sport block when the user backs ['sport']", async () => {
    await renderBuild({ selectedCategoryBuckets: ["sport"], followedSourceBuckets: [] });

    // Exactly one block, and it is Sport — no phantom Tech/AI/etc.
    expect(renderedBlockLabels()).toEqual(["Sport"]);
    // No empty state once there is a real signal.
    expect(container.querySelector("#noSignalEmpty")).toBeNull();
  });
});
