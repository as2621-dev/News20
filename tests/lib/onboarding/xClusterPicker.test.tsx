import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Rendered-behavior tests for the in-chat X CLUSTERS picker (slice #20), through the REAL
 * component chain with a stubbed cluster loader (no live X call). Rule 9 — each asserts an AC:
 *   - clusters group under the user's categories + a "beyond your picks" section is present;
 *   - the expandable handle list renders the cluster's FULL membership;
 *   - selecting clusters and confirming hands up pre-expanded picks (cluster ref + members).
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { XClusterPicker } from "@/components/onboarding/XClusterPicker";
import type { ResolvedCluster } from "@/lib/sourceClusters";

function member(id: string, handle: string): ResolvedCluster["members"][number] {
  return { kind: "source", followable_id: id, display_name: handle, popularity_score: 50 };
}

function cluster(
  overrides: Partial<ResolvedCluster> & { cluster_slug: string; cluster_category: string },
): ResolvedCluster {
  return {
    cluster_id: `id-${overrides.cluster_slug}`,
    cluster_label: overrides.cluster_slug,
    cluster_sort_order: 0,
    members: [],
    ...overrides,
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** ai (a user pick) + business (NOT picked → beyond your picks). */
function loaderStub(): () => Promise<Map<string, ResolvedCluster[]>> {
  return vi.fn(
    async () =>
      new Map<string, ResolvedCluster[]>([
        [
          "ai",
          [
            cluster({
              cluster_slug: "ai-labs",
              cluster_category: "ai",
              cluster_description: "Frontier lab leaders",
              members: [
                member("s1", "@a"),
                member("s2", "@b"),
                member("s3", "@c"),
                member("s4", "@d"),
                member("s5", "@e"),
              ],
            }),
          ],
        ],
        ["business", [cluster({ cluster_slug: "vc", cluster_category: "business", members: [member("v1", "@vc")] })]],
      ]),
  );
}

describe("XClusterPicker (Rule 9)", () => {
  it("groups clusters under the user's categories and shows a beyond-your-picks section — AC2", async () => {
    await act(async () => {
      root.render(<XClusterPicker orderedRoots={["ai"]} onConfirm={vi.fn()} loadClusters={loaderStub()} />);
    });
    await flush();

    expect(container.querySelector("[data-testid='beyond-your-picks']")).not.toBeNull();
    // Two cards: the picked ai cluster + the beyond-picks business cluster.
    expect(container.querySelectorAll("[data-testid='cluster-card']")).toHaveLength(2);
  });

  it("expands to the cluster's FULL membership on '+N MORE' — AC5", async () => {
    await act(async () => {
      root.render(<XClusterPicker orderedRoots={["ai"]} onConfirm={vi.fn()} loadClusters={loaderStub()} />);
    });
    await flush();

    // The ai-labs card samples 3 handles; the 5-member cluster hides 2 behind the expander.
    const aiCard = container.querySelector<HTMLElement>("[data-testid='cluster-card']");
    const handlesBefore = aiCard?.querySelectorAll("[data-testid='cluster-handles'] span");
    expect(handlesBefore).toHaveLength(3);

    const more = aiCard?.querySelector<HTMLButtonElement>("[data-testid='cluster-more']");
    expect(more?.textContent).toContain("+2 MORE");
    await act(async () => {
      more?.click();
    });

    const handlesAfter = aiCard?.querySelectorAll("[data-testid='cluster-handles'] span");
    expect(handlesAfter).toHaveLength(5);
    expect(aiCard?.textContent).toContain("@e");
  });

  it("confirms selected clusters as pre-expanded picks (cluster ref + members)", async () => {
    const onConfirm = vi.fn();
    await act(async () => {
      root.render(<XClusterPicker orderedRoots={["ai"]} onConfirm={onConfirm} loadClusters={loaderStub()} />);
    });
    await flush();

    const toggle = container.querySelector<HTMLButtonElement>("[data-testid='cluster-toggle']");
    await act(async () => {
      toggle?.click();
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>("[data-testid='x-cluster-confirm']")?.click();
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith([
      {
        cluster_id: "id-ai-labs",
        cluster_slug: "ai-labs",
        member_source_ids: ["s1", "s2", "s3", "s4", "s5"],
        member_personality_ids: [],
      },
    ]);
  });

  it("confirms zero picks as valid (those X slots default to news) — AC3", async () => {
    const onConfirm = vi.fn();
    await act(async () => {
      root.render(<XClusterPicker orderedRoots={["ai"]} onConfirm={onConfirm} loadClusters={loaderStub()} />);
    });
    await flush();

    const confirm = container.querySelector<HTMLButtonElement>("[data-testid='x-cluster-confirm']");
    expect(confirm?.textContent).toContain("Skip for now");
    await act(async () => {
      confirm?.click();
    });
    expect(onConfirm).toHaveBeenCalledWith([]);
  });
});
