import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Phase FSR-M6a SP3 — the no-dup grid + cluster cards UI.
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * (NO @testing-library — not a project dependency; mirrors `sourceCard.test.tsx`).
 * The phase file says "RTL tests" but the codebase convention (Rule 11) is this
 * harness; behavior coverage is identical.
 *
 * Rule 9 — these encode WHY the selection surface matters, not just WHAT renders:
 *   - recommended clusters render PRE-SELECTED (opt-out — the whole UX premise);
 *   - tapping a selected cluster DESELECTS it AND its member tiles reflect the flip
 *     (bulk select is the one-tap promise — User Story 11);
 *   - the grid NEVER renders a personality's bundled handle as a separate tile
 *     (no-dup, visible — it renders only the resolver's output, which suppressed them);
 *   - a zero-cluster category cell renders a graceful fallback (never randoms);
 *   - aria-pressed reflects selection for cluster + member controls.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { SourceClusterGrid } from "@/components/sources/SourceClusterGrid";
import type { ResolvedCluster, ResolvedClusterMember } from "@/lib/sourceClusters";

function srcMember(id: string, name: string): ResolvedClusterMember {
  return { kind: "source", followable_id: id, display_name: name, popularity_score: 50 };
}
function personMember(id: string, name: string): ResolvedClusterMember {
  return { kind: "personality", followable_id: id, display_name: name, popularity_score: 50 };
}
function cluster(slug: string, label: string, members: ResolvedClusterMember[]): ResolvedCluster {
  return { cluster_slug: slug, cluster_label: label, cluster_category: "ai", cluster_sort_order: 0, members };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function click(element: HTMLElement): void {
  act(() => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

function clusterCard(slug: string): HTMLButtonElement {
  const el = container.querySelector<HTMLButtonElement>(`[data-cluster-card="${slug}"]`);
  if (!el) {
    throw new Error(`cluster card ${slug} not rendered`);
  }
  return el;
}

function memberTile(id: string): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>(`[data-member-tile="${id}"]`);
}

describe("SourceClusterGrid — pre-selection, bulk toggle, no-dup, empty fallback", () => {
  it("renders recommended clusters PRE-SELECTED (opt-out) with aria-pressed=true", () => {
    const clusters = [cluster("ai-labs", "AI Labs", [srcMember("s-1", "DeepMind"), personMember("p-1", "Demis")])];
    act(() => {
      root.render(
        <SourceClusterGrid
          categories={[{ category: "ai", label: "AI", clusters }]}
          recommendedClusterSlugs={["ai-labs"]}
        />,
      );
    });

    expect(clusterCard("ai-labs").getAttribute("aria-pressed")).toBe("true");
    // Its member tiles render selected too.
    expect(memberTile("s-1")?.getAttribute("aria-pressed")).toBe("true");
    expect(memberTile("p-1")?.getAttribute("aria-pressed")).toBe("true");
  });

  it("tapping a selected cluster DESELECTS it and its member tiles reflect deselection", () => {
    const clusters = [cluster("ai-labs", "AI Labs", [srcMember("s-1", "DeepMind"), srcMember("s-2", "OpenAI")])];
    act(() => {
      root.render(
        <SourceClusterGrid
          categories={[{ category: "ai", label: "AI", clusters }]}
          recommendedClusterSlugs={["ai-labs"]}
        />,
      );
    });

    expect(clusterCard("ai-labs").getAttribute("aria-pressed")).toBe("true");
    click(clusterCard("ai-labs"));

    expect(clusterCard("ai-labs").getAttribute("aria-pressed")).toBe("false");
    expect(memberTile("s-1")?.getAttribute("aria-pressed")).toBe("false");
    expect(memberTile("s-2")?.getAttribute("aria-pressed")).toBe("false");
  });

  it("NEVER renders a personality's bundled handle as a separate tile (no-dup, visible)", () => {
    // WHY: the resolver suppressed the bundled handle rows; the grid renders only the
    // resolver output. A personality renders ONCE as a personality tile, and the raw
    // YouTube/X tiles for that person are absent. This FAILS if the grid re-derives
    // membership and re-introduces the suppressed rows.
    const clusters = [
      cluster("ai-people", "AI People", [personMember("p-lex", "Lex Fridman")]),
      cluster("ai-channels", "AI Channels", [srcMember("s-other", "Two Minute Papers")]),
    ];
    act(() => {
      root.render(
        <SourceClusterGrid
          categories={[{ category: "ai", label: "AI", clusters }]}
          recommendedClusterSlugs={["ai-people", "ai-channels"]}
        />,
      );
    });

    // The personality renders once.
    expect(memberTile("p-lex")).not.toBeNull();
    expect(memberTile("p-lex")?.getAttribute("data-member-kind")).toBe("personality");
    // The unrelated channel renders.
    expect(memberTile("s-other")).not.toBeNull();
    // No raw bundled-handle tiles exist for the personality (they were suppressed by
    // the resolver and are simply not in the grid's input).
    expect(memberTile("s-lex-yt")).toBeNull();
    expect(memberTile("s-lex-x")).toBeNull();
  });

  it("renders a graceful fallback for a zero-cluster category cell (never randoms)", () => {
    act(() => {
      root.render(
        <SourceClusterGrid
          categories={[{ category: "sport", label: "Sport", clusters: [] }]}
          recommendedClusterSlugs={[]}
        />,
      );
    });

    const section = container.querySelector<HTMLElement>('[data-category-section="sport"]');
    expect(section?.getAttribute("data-empty")).toBe("true");
    expect(container.querySelector("[data-empty-fallback]")?.textContent).toContain("Nothing curated here yet");
    // No member tiles / cluster cards in an empty cell.
    expect(container.querySelector("[data-member-tile]")).toBeNull();
    expect(container.querySelector("[data-cluster-card]")).toBeNull();
  });

  it("emits the new selection via onSelectionChange on a toggle (parent reads it on continue)", () => {
    const onSelectionChange = vi.fn();
    const clusters = [cluster("ai-labs", "AI Labs", [srcMember("s-1", "DeepMind")])];
    act(() => {
      root.render(
        <SourceClusterGrid
          categories={[{ category: "ai", label: "AI", clusters }]}
          recommendedClusterSlugs={[]}
          onSelectionChange={onSelectionChange}
        />,
      );
    });

    // Called once on mount (initial pre-selection emit) — here nothing recommended,
    // so the initial set is empty — then again on the toggle.
    const callsBefore = onSelectionChange.mock.calls.length;
    click(clusterCard("ai-labs"));
    expect(onSelectionChange.mock.calls.length).toBe(callsBefore + 1);
    const next = onSelectionChange.mock.calls.at(-1)?.[0];
    expect(next.selectedClusterSlugs.has("ai-labs")).toBe(true);
  });

  it("a single member tile can be deselected individually while its cluster stays selected", () => {
    const clusters = [cluster("ai-labs", "AI Labs", [srcMember("s-1", "DeepMind"), srcMember("s-2", "OpenAI")])];
    act(() => {
      root.render(
        <SourceClusterGrid
          categories={[{ category: "ai", label: "AI", clusters }]}
          recommendedClusterSlugs={["ai-labs"]}
        />,
      );
    });

    click(memberTile("s-2") as HTMLButtonElement);
    // Cluster still selected; s-1 still on; s-2 now off.
    expect(clusterCard("ai-labs").getAttribute("aria-pressed")).toBe("true");
    expect(memberTile("s-1")?.getAttribute("aria-pressed")).toBe("true");
    expect(memberTile("s-2")?.getAttribute("aria-pressed")).toBe("false");
  });
});
