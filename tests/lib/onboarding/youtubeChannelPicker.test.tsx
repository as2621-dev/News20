import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Rendered-behavior tests for the in-chat YouTube grid (slice #20), driven through the REAL
 * component chain with a stubbed catalog loader (no network — proving the "no live calls during
 * onboarding" AC at the component seam). Rule 9 — each asserts an AC:
 *   - tiles render sorted by relevance to the interview picks;
 *   - the supply expectation recomputes on toggle with NO further catalog reads (loader once);
 *   - a null-thumbnail row renders the initials fallback and never blocks the grid;
 *   - zero picks is a valid confirm.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { YoutubeChannelPicker } from "@/components/onboarding/YoutubeChannelPicker";
import type { ContentSource } from "@/types/source";

function channel(overrides: Partial<ContentSource> & { source_id: string; source_name: string }): ContentSource {
  return {
    content_source_type: "youtube_channel",
    external_id: `ext-${overrides.source_id}`,
    source_description: null,
    thumbnail_url: null,
    subscriber_count: null,
    platform_metadata: null,
    personas: [],
    topic_tags: [],
    popularity_score: 50,
    is_curated: true,
    last_fetched_at: null,
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

/** Flush the loader promise + its state update. */
async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

const CHANNELS = [
  channel({ source_id: "biz", source_name: "Biz Desk", topic_tags: ["business"] }),
  channel({ source_id: "ai", source_name: "AI Desk", topic_tags: ["ai"] }),
  channel({ source_id: "sport", source_name: "Sport Desk", topic_tags: ["sport"] }),
];

describe("YoutubeChannelPicker (Rule 9)", () => {
  it("renders channel tiles sorted by relevance to the interview picks", async () => {
    const loadChannels = vi.fn(async () => CHANNELS);
    await act(async () => {
      root.render(
        <YoutubeChannelPicker orderedRoots={["ai", "sport"]} onConfirm={vi.fn()} loadChannels={loadChannels} />,
      );
    });
    await flush();

    // ai first (top pick), then sport, then the unmatched business channel trails.
    const order = Array.from(container.querySelectorAll<HTMLElement>("[data-testid='youtube-tile']")).map((tile) =>
      tile.textContent?.includes("AI Desk") ? "ai" : tile.textContent?.includes("Sport Desk") ? "sport" : "biz",
    );
    expect(order).toEqual(["ai", "sport", "biz"]);
  });

  it("recomputes the supply expectation on toggle with NO further catalog reads (no network)", async () => {
    const loadChannels = vi.fn(async () => [
      channel({ source_id: "a", source_name: "A", topic_tags: ["ai"], platform_metadata: { videos_per_week: 7 } }),
    ]);
    await act(async () => {
      root.render(<YoutubeChannelPicker orderedRoots={["ai"]} onConfirm={vi.fn()} loadChannels={loadChannels} />);
    });
    await flush();

    const supplyBefore = container.querySelector("[data-testid='supply-expectation']")?.textContent;
    expect(supplyBefore).toContain("Pick a few channels");

    const tile = container.querySelector<HTMLButtonElement>("[data-testid='youtube-tile']");
    await act(async () => {
      tile?.click();
    });

    const supplyAfter = container.querySelector("[data-testid='supply-expectation']")?.textContent;
    // 1 channel × 7/week ÷ 7 = 1/day — recomputed purely from the in-memory selection.
    expect(supplyAfter).toBe("these 1 channel ≈ ~1 long-form video/day");
    // The catalog was read exactly ONCE (on mount) — toggling never hits the network.
    expect(loadChannels).toHaveBeenCalledTimes(1);
  });

  it("renders a null-thumbnail row via the initials fallback (never blocks the grid) — AC6", async () => {
    const loadChannels = vi.fn(async () => [
      channel({ source_id: "n", source_name: "No Avatar Channel", topic_tags: ["ai"], thumbnail_url: null }),
    ]);
    await act(async () => {
      root.render(<YoutubeChannelPicker orderedRoots={["ai"]} onConfirm={vi.fn()} loadChannels={loadChannels} />);
    });
    await flush();

    const tile = container.querySelector<HTMLElement>("[data-testid='youtube-tile']");
    expect(tile).not.toBeNull();
    // No <img> is rendered when the thumbnail is null — the initials-gradient fallback shows instead.
    expect(tile?.querySelector("img")).toBeNull();
    // The tile still carries its name (the grid rendered, not blocked).
    expect(tile?.textContent).toContain("No Avatar Channel");
  });

  it("confirms zero picks as a valid selection (those slots default to news) — AC3", async () => {
    const onConfirm = vi.fn();
    const loadChannels = vi.fn(async () => CHANNELS);
    await act(async () => {
      root.render(<YoutubeChannelPicker orderedRoots={["ai"]} onConfirm={onConfirm} loadChannels={loadChannels} />);
    });
    await flush();

    const confirm = container.querySelector<HTMLButtonElement>("[data-testid='youtube-confirm']");
    expect(confirm?.textContent).toContain("Skip for now");
    await act(async () => {
      confirm?.click();
    });
    expect(onConfirm).toHaveBeenCalledWith([]);
  });
});
