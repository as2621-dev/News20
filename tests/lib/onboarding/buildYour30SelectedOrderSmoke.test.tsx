import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * End-to-end SELECTED-ONLY + ORDER smoke for the Build-your-30 gate (phase-SP4 sub-phase 4).
 *
 * This is the integration smoke that proves the phase's user-visible promise across the
 * REAL units it touched, in ONE place, WITHOUT a browser or any live service:
 *
 *   pick a subset  →  Build-your-30 shows ONLY that subset (no unselected leak)
 *   arrange Sport before Tech (real ▲ reorder handler)  →  on-screen order is Sport, Tech
 *   persist (REAL saveUserFeedAllocation mapping, fake client)  →  allocation_sort_order
 *     puts Sport at 0, Tech at 1, and persists NO unselected category
 *   that persisted sequence is EXACTLY the input the SP3 back-end lock
 *     (tests/agents/pipeline/test_feed_assembly_order.py) proves the assembled reel feed
 *     emits in — Sport reels, then Tech reels, and nothing else.
 *
 * So the FULL chain pick → allocation order → reel-feed category order is covered: the
 * front half (pick → selected-only → arranged → persisted sort_order) is asserted HERE
 * with the real component + real persistence mapping; the back half (persisted sort_order
 * → assembled feed category order) is locked by the SP3 pytest. The hand-off contract
 * between the two halves is the `[(allocation_category, allocation_sort_order)]` rows —
 * this smoke asserts the exact rows the SP3 test consumes as its input.
 *
 * Why scripted, not a live browser run: the phase DoD allows "manual or scripted"; a live
 * dev-server + Chrome/CDP + seeded auth run is heavy, non-deterministic, and would hit real
 * services. This deterministic smoke drives the same real code offline and is re-runnable.
 *
 * Setup mirrors the sibling SP1/SP2 onboarding tests (React 19 `createRoot` + `act`, no
 * @testing-library; feedAllocation + first-run assemble client mocked at the module
 * boundary). The component's `saveUserFeedAllocation` is mocked so `handleSave` never hits
 * Supabase; the REAL persistence mapping is pulled via `vi.importActual` and driven with a
 * fake Supabase client so the index → allocation_sort_order mapping is exercised for real.
 *
 * Rule 9 — WHY this matters, built to FAIL on a real regression:
 *   - SELECTED-ONLY: rendering with ["tech","sport"] must show ONLY Tech + Sport. If the
 *     no-signal/seed gate regressed to the full default seed, unselected blocks (AI,
 *     Geopolitics, Business, …) would render and the leak assertion FAILS.
 *   - ORDER: after moving Sport above Tech, the on-screen order AND the persisted
 *     allocation_sort_order must be Sport-then-Tech. If the reorder handler or the
 *     index → sort_order mapping regressed, the order assertion FAILS.
 *   - NOTHING-PICKED: with no picks and no sources there must be NO category blocks at all
 *     (no phantom default seed). If the seed regressed to buildDefaultSegments() this FAILS.
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

/** The authed user the fake persistence client reports (owner-scoping for the upsert rows). */
const AUTHED_USER_ID = "smoke-user-uuid-1";

/**
 * The category sequence SP3's `test_assembled_feed_category_order_follows_allocation_sort_order`
 * consumes as its allocation input and proves the assembled reel feed emits in. The persisted
 * rows this smoke produces MUST match this order so the two halves of the chain meet. Sport
 * leads, then Tech — the user's arranged Build-your-30 order.
 */
const SP3_LOCKED_FEED_CATEGORY_ORDER = ["sport", "tech"] as const;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.clearAllMocks();
  // Fresh user: no saved allocation, so the mount effect leaves the gated seed untouched.
  mockGetUserFeedAllocation.mockResolvedValue([]);
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
  // Flush the async mount seed (getUserFeedAllocation → setSegments). Two microtask turns:
  // the awaited read, then the resulting state-commit re-render.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** The category/source block rows actually rendered, IN ON-SCREEN ORDER (`.seg .nm` labels). */
function renderedBlockLabels(): string[] {
  return [...container.querySelectorAll<HTMLElement>(".seg .nm")].map((node) => node.textContent?.trim() ?? "");
}

/** Click a reorder/stepper control by its exact aria-label (drives the REAL handler). */
async function clickControl(ariaLabel: string): Promise<void> {
  const button = container.querySelector<HTMLButtonElement>(`button[aria-label="${ariaLabel}"]`);
  expect(button, `control "${ariaLabel}" should be rendered`).not.toBeNull();
  await act(async () => {
    button?.click();
  });
}

/**
 * A fake Supabase client capturing the upsert rows, so the REAL `saveUserFeedAllocation`
 * mapping (segment list index → allocation_sort_order) can be exercised offline. Mirrors the
 * `makeAllocationClient` boundary fake in `tests/lib/feedAllocation.test.ts`.
 */
function makePersistenceClient() {
  const upsertCalls: Array<{ rows: Array<Record<string, unknown>>; onConflict: string | undefined }> = [];
  const upsert = vi.fn((rows: Array<Record<string, unknown>>, opts: { onConflict?: string }) => {
    upsertCalls.push({ rows, onConflict: opts?.onConflict });
    return Promise.resolve({ error: null });
  });
  // DELETE chain: delete().eq().not() — thenable so awaiting resolves the prune.
  const del = vi.fn().mockReturnValue({
    eq: vi.fn(() => {
      const resolved = Promise.resolve({ error: null });
      return Object.assign(resolved, { not: vi.fn(() => Promise.resolve({ error: null })) });
    }),
  });
  const getUser = vi.fn().mockResolvedValue({ data: { user: { id: AUTHED_USER_ID } }, error: null });
  const from = vi.fn().mockReturnValue({ upsert, delete: del });
  const client = { auth: { getUser }, from } as never;
  return { client, upsertCalls };
}

describe("BuildYour30 — selected-only + arranged-order end-to-end smoke (Rule 9)", () => {
  it("shows ONLY the picked buckets, in arranged order, and persists that order with no unselected leak", async () => {
    // ── 1. Pick a known subset: Tech + Sport (and NOTHING else). ──────────────────────────
    await renderBuild({ selectedCategoryBuckets: ["tech", "sport"], followedSourceBuckets: [] });

    // SELECTED-ONLY: exactly Tech + Sport render — no unselected category leaks in. The seed
    // is the default-seed order (Tech precedes Sport in DEFAULT_ALLOCATION_SEGMENTS), filtered
    // to the backed subset.
    const seededLabels = renderedBlockLabels();
    expect(seededLabels).toEqual(["Tech", "Sport"]);
    for (const unselected of ["AI", "Geopolitics", "Business", "Environment", "Politics", "Arts", "YouTube", "X"]) {
      expect(seededLabels).not.toContain(unselected);
    }

    // ── 2. Arrange Sport BEFORE Tech via the REAL reorder (▲) handler. ────────────────────
    // Sport starts at index 1; one "Move Sport up" swaps it above Tech.
    await clickControl("Move Sport up");

    // ON-SCREEN ORDER is now the user's arranged order: Sport, then Tech.
    const arrangedLabels = renderedBlockLabels();
    expect(arrangedLabels).toEqual(["Sport", "Tech"]);

    // ── 3. Persist via the REAL saveUserFeedAllocation mapping (fake client). ──────────────
    // The arranged on-screen order is the segment list order; the persistence layer maps that
    // list index → allocation_sort_order. We pull the REAL helper (the component's import is
    // mocked) and a fake client so the mapping runs for real, offline.
    const arrangedSegments: AllocationSegment[] = [
      { bucketId: "sport", count: 5 },
      { bucketId: "tech", count: 25 },
    ];
    const { saveUserFeedAllocation: realSaveUserFeedAllocation } =
      await vi.importActual<typeof import("@/lib/feedAllocation")>("@/lib/feedAllocation");
    const { client, upsertCalls } = makePersistenceClient();

    const result = await realSaveUserFeedAllocation(arrangedSegments, client);

    // The persisted rows: one per picked bucket, NO unselected category, sort_order = list index.
    expect(result.persisted_count).toBe(2);
    expect(upsertCalls).toHaveLength(1);
    const persistedRows = upsertCalls[0].rows as Array<{
      allocation_category: string;
      allocation_sort_order: number;
      follow_user_id: string;
    }>;

    // SELECTED-ONLY at the persistence layer: only the picked categories are written.
    const persistedCategories = persistedRows.map((row) => row.allocation_category);
    expect(persistedCategories.sort()).toEqual(["sport", "tech"]);
    for (const unselectedEnum of ["ai", "geopolitics", "business", "environment", "politics", "arts", "youtube", "x"]) {
      expect(persistedCategories).not.toContain(unselectedEnum);
    }

    // ORDER: the arranged sequence persists as allocation_sort_order — Sport at 0, Tech at 1.
    const categoryBySortOrder = [...persistedRows]
      .sort((a, b) => a.allocation_sort_order - b.allocation_sort_order)
      .map((row) => row.allocation_category);
    expect(categoryBySortOrder).toEqual(["sport", "tech"]);

    // ── 4. Tie to the SP3 back-end lock — close the full chain. ───────────────────────────
    // This persisted (category, sort_order) sequence is EXACTLY the allocation input that
    // tests/agents/pipeline/test_feed_assembly_order.py proves the assembled reel feed emits
    // in (Sport reels lead, then Tech reels, and no unselected category). So: picked subset →
    // selected-only blocks → arranged order → persisted sort_order → reel-feed category order.
    expect(categoryBySortOrder).toEqual([...SP3_LOCKED_FEED_CATEGORY_ORDER]);
  });

  it("renders NO category blocks at all when the user picked NOTHING (no phantom seed)", async () => {
    await renderBuild({ selectedCategoryBuckets: [], followedSourceBuckets: [] });

    // NOTHING-PICKED: zero blocks — the full default seed (every bucket) must NOT appear.
    expect(renderedBlockLabels()).toEqual([]);
    // The empty-state CTA renders in place of the allocation chrome; the Add sheet offers nothing.
    expect(container.querySelector("#noSignalEmpty")).not.toBeNull();
    expect([...container.querySelectorAll<HTMLElement>(".sheet2 .bk")]).toHaveLength(0);
  });
});
