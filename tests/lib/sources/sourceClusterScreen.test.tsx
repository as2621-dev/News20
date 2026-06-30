import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Phase FSR-M6a SP4 — the source/cluster onboarding STEP container.
 *
 * `react-dom/client` + `act` (no @testing-library, per codebase convention). The
 * cluster READ + the COMMIT are mocked at the lib boundary (CLAUDE.md mocking rule) —
 * this suite exercises the container's wiring: load → render grid → commit the
 * resolved opt-out set on continue → advance.
 *
 * Rule 9 — WHY each behavior matters:
 *   - On continue it MUST commit the RESOLVED opt-out set (the pre-selected clusters'
 *     members, minus deselections) and THEN advance — the persistence is the point of
 *     the step. A continue that advances without committing FAILS.
 *   - A zero-cluster catalog renders the graceful fallback and continue still advances
 *     with an empty commit (User Story 21) — the user is never trapped.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { SourceClusterScreen } from "@/components/sources/SourceClusterScreen";
import type { ResolvedCluster } from "@/lib/sourceClusters";

const getClustersForCategories = vi.fn();
const commitClusterFollowSet = vi.fn();

vi.mock("@/lib/sourceClusters", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/sourceClusters")>();
  return {
    ...actual,
    getClustersForCategories: (...args: unknown[]) => getClustersForCategories(...args),
    commitClusterFollowSet: (...args: unknown[]) => commitClusterFollowSet(...args),
  };
});

function cluster(slug: string, members: ResolvedCluster["members"]): ResolvedCluster {
  return { cluster_slug: slug, cluster_label: slug, cluster_category: "ai", cluster_sort_order: 0, members };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getClustersForCategories.mockReset();
  commitClusterFollowSet.mockReset().mockResolvedValue({ sources_followed: 0, personalities_followed: 0 });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** Flush the load promise + effects. */
async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("SourceClusterScreen — load, render, commit-on-continue", () => {
  it("commits the resolved opt-out set on continue, THEN calls onDone", async () => {
    const clusters = [
      cluster("ai-labs", [
        { kind: "source", followable_id: "s-1", display_name: "DeepMind", popularity_score: 90 },
        { kind: "personality", followable_id: "p-1", display_name: "Demis", popularity_score: 80 },
      ]),
    ];
    getClustersForCategories.mockResolvedValue(new Map([["ai", clusters]]));
    const onDone = vi.fn();

    act(() => {
      root.render(<SourceClusterScreen categories={["ai"]} onDone={onDone} />);
    });
    await flush();

    // The pre-selected cluster's members are the resolved set; continue commits them.
    const continueBtn = container.querySelector<HTMLButtonElement>("[data-cluster-continue]");
    await act(async () => {
      continueBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(commitClusterFollowSet).toHaveBeenCalledTimes(1);
    const committedSet = commitClusterFollowSet.mock.calls[0][0];
    expect(committedSet.sources).toEqual(["s-1"]);
    expect(committedSet.personalities).toEqual(["p-1"]);
    expect(onDone).toHaveBeenCalledWith(committedSet);
  });

  it("a deselected cluster is NOT in the committed set", async () => {
    const clusters = [
      cluster("ai-labs", [{ kind: "source", followable_id: "s-1", display_name: "DeepMind", popularity_score: 90 }]),
    ];
    getClustersForCategories.mockResolvedValue(new Map([["ai", clusters]]));

    act(() => {
      root.render(<SourceClusterScreen categories={["ai"]} onDone={vi.fn()} />);
    });
    await flush();

    // Deselect the (pre-selected) cluster, then continue.
    const card = container.querySelector<HTMLButtonElement>('[data-cluster-card="ai-labs"]');
    act(() => card?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    const continueBtn = container.querySelector<HTMLButtonElement>("[data-cluster-continue]");
    await act(async () => {
      continueBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(commitClusterFollowSet.mock.calls[0][0]).toEqual({ sources: [], personalities: [] });
  });

  it("renders the graceful fallback for a zero-cluster catalog and continue still advances (empty commit)", async () => {
    getClustersForCategories.mockResolvedValue(new Map([["sport", []]]));
    const onDone = vi.fn();

    act(() => {
      root.render(<SourceClusterScreen categories={["sport"]} onDone={onDone} />);
    });
    await flush();

    expect(container.querySelector("[data-empty-fallback]")).not.toBeNull();

    const continueBtn = container.querySelector<HTMLButtonElement>("[data-cluster-continue]");
    await act(async () => {
      continueBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(commitClusterFollowSet).toHaveBeenCalledWith({ sources: [], personalities: [] });
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
