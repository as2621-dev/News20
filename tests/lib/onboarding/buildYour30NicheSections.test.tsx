import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Component test for BuildYour30's read-only NICHE SECTION rendering (slice #12).
 *
 * Setup mirrors `buildYour30SavedGate.test.tsx`: React 19 `createRoot` + `act`, with the
 * allocation read/persist + first-run assemble clients MOCKED at the module boundary. Here
 * `getUserFeedAllocation` resolves to a saved allocation that MIXES coarse "Build your 30"
 * blocks with interview-built niche + "Beyond your bubble" SECTION rows (migration 0026).
 *
 * Rule 9 — WHY this behavior matters (each test fails on a real regression):
 *   - Coarse-only editing (founder 2026-07-05): a returning deep-profile user must SEE their
 *     interview sections in their own vocabulary ("IPL — 4"), but read-only — no stepper /
 *     reorder / remove. If the loader dropped the niche columns (the pre-#12 bug) the sections
 *     collapse into anonymous coarse `sport` blocks and these labels never render.
 *   - The read-only sections must NOT leak into the editable seglist (they'd become editable and
 *     a save would flatten them). They render in a separate `#nicheSections` group with no stepper.
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
  todayUtcFeedDate: vi.fn(() => "2026-07-05"),
}));

const mockGetUserFeedAllocation = vi.mocked(getUserFeedAllocation);
const mockSaveUserFeedAllocation = vi.mocked(saveUserFeedAllocation);

/** A deep-profile saved allocation: two niche sport sections + a beyond-bubble reserve + coarse blocks. */
const SAVED_WITH_NICHE: AllocationSegment[] = [
  { bucketId: "sport", count: 4, interestId: "int-ipl", sectionLabel: "IPL" },
  { bucketId: "sport", count: 3, interestId: "int-ti", sectionLabel: "Team India" },
  { bucketId: "arts", count: 1, interestId: null, sectionLabel: "Beyond your bubble" },
  { bucketId: "ai", count: 12 },
  { bucketId: "tech", count: 10 },
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
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("BuildYour30 — read-only niche section rendering (Rule 9)", () => {
  it("renders interview sections as named, read-only blocks in the user's vocabulary", async () => {
    mockGetUserFeedAllocation.mockResolvedValue(SAVED_WITH_NICHE);
    await renderBuild({ selectedCategoryBuckets: ["ai", "tech", "sport", "arts"], followedSourceBuckets: [] });

    const nicheGroup = container.querySelector<HTMLElement>("#nicheSections");
    expect(nicheGroup).not.toBeNull();
    const nicheLabels = [...(nicheGroup?.querySelectorAll<HTMLElement>(".seg .nm") ?? [])].map((n) =>
      n.textContent?.trim(),
    );
    expect(nicheLabels).toEqual(["IPL", "Team India", "Beyond your bubble"]);
    // The user-vocabulary counts show ("IPL — 4", "Team India — 3", "Beyond your bubble — 1").
    const nicheCounts = [...(nicheGroup?.querySelectorAll<HTMLElement>(".seg .nm + span") ?? [])].map((n) =>
      n.textContent?.trim(),
    );
    expect(nicheCounts).toEqual(["4", "3", "1"]);
  });

  it("keeps the section blocks READ-ONLY (no stepper) and out of the editable seglist", async () => {
    mockGetUserFeedAllocation.mockResolvedValue(SAVED_WITH_NICHE);
    await renderBuild({ selectedCategoryBuckets: ["ai", "tech", "sport", "arts"], followedSourceBuckets: [] });

    // Read-only: the niche group has NO stepper controls.
    const nicheGroup = container.querySelector<HTMLElement>("#nicheSections");
    expect(nicheGroup?.querySelectorAll(".stepper").length).toBe(0);
    expect(nicheGroup?.querySelectorAll(".rm").length).toBe(0);

    // The niche labels do NOT appear in the editable seglist (only coarse blocks do there).
    const seglist = container.querySelector<HTMLElement>("#seglist");
    const editableLabels = [...(seglist?.querySelectorAll<HTMLElement>(".seg .nm") ?? [])].map((n) =>
      n.textContent?.trim(),
    );
    expect(editableLabels).not.toContain("IPL");
    expect(editableLabels).not.toContain("Team India");
  });

  it("folds the section slots into the 30-budget so a full allocation reads 30/30 (no over-allocation)", async () => {
    // WHY (Rule 9): the anti-over-allocation guard. The read-only sections consume 8 slots
    // (IPL 4 + Team India 3 + Beyond-your-bubble 1); the coarse blocks fill the remaining 22
    // (ai 12 + tech 10). If the budget counted coarse slots only, the footer would read 22/30
    // and Save would still fire — then persist 22 coarse rows ON TOP of the 8 section rows. With
    // the fold, the footer reads 30/30 and Save is enabled at EXACTLY coarse + sections = 30, so
    // the persisted total can never exceed 30.
    mockGetUserFeedAllocation.mockResolvedValue(SAVED_WITH_NICHE);
    await renderBuild({ selectedCategoryBuckets: ["ai", "tech", "sport", "arts"], followedSourceBuckets: [] });

    const budgetLabel = container.querySelector<HTMLElement>("#blbl")?.textContent ?? "";
    expect(budgetLabel).toContain("30/30");
    expect(budgetLabel).toContain("full");
    const cta = container.querySelector<HTMLButtonElement>("#cta");
    expect(cta?.disabled).toBe(false); // full budget → Save enabled (coarse + sections = 30)
  });

  it("renders MULTIPLE 'Beyond your bubble' reserve rows without a duplicate React key", async () => {
    // WHY (Rule 9): the backend allocator emits 3–5 beyond-bubble rows, ALL sharing the label
    // "Beyond your bubble" and distinguished only by category. A label-only key collides across
    // them → React duplicate-key warning + unstable reconciliation. The key must disambiguate by
    // bucketId. We assert both rows render AND that React logged no duplicate-key error.
    const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const twoBeyond: AllocationSegment[] = [
      { bucketId: "ai", count: 14 },
      { bucketId: "tech", count: 14 },
      { bucketId: "environment", count: 1, interestId: null, sectionLabel: "Beyond your bubble" },
      { bucketId: "politics", count: 1, interestId: null, sectionLabel: "Beyond your bubble" },
    ];
    mockGetUserFeedAllocation.mockResolvedValue(twoBeyond);
    await renderBuild({
      selectedCategoryBuckets: ["ai", "tech", "environment", "politics"],
      followedSourceBuckets: [],
    });

    const nicheGroup = container.querySelector<HTMLElement>("#nicheSections");
    const labels = [...(nicheGroup?.querySelectorAll<HTMLElement>(".seg .nm") ?? [])].map((n) => n.textContent?.trim());
    expect(labels).toEqual(["Beyond your bubble", "Beyond your bubble"]);
    const duplicateKeyWarned = consoleErrorSpy.mock.calls.some((call) =>
      String(call[0]).toLowerCase().includes("same key"),
    );
    expect(duplicateKeyWarned).toBe(false);
    consoleErrorSpy.mockRestore();
  });
});
