import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

/**
 * Pinned-to-bottom scroll hook tests (issue #40).
 *
 * Rule 9 — WHY these matter:
 *   - A chat must FOLLOW new content (newest message visible) — but ONLY while
 *     the user is at the bottom. Yanking a user who scrolled up to reread makes
 *     long histories unusable (the exact acceptance edge case).
 *   - Returning to the bottom must re-arm following — otherwise one upward
 *     scroll permanently kills autoscroll for the session.
 *
 * jsdom has no layout, so scroll geometry (scrollHeight/clientHeight) is
 * stubbed per element; the REAL scroll-container behavior is proven in the
 * browser walkthrough (.agents/debug/issue-40/).
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { useBottomAnchoredScroll } from "@/lib/chat/useBottomAnchoredScroll";

/** Harness component: a scrollable list that grows by `item_count`. */
function ScrollHarness({ item_count }: { item_count: number }) {
  const { scrollContainerRef, handleScroll } = useBottomAnchoredScroll<HTMLDivElement>([item_count]);
  return (
    <div data-testid="scroller" ref={scrollContainerRef} onScroll={handleScroll}>
      {Array.from({ length: item_count }, (_, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: append-only harness list.
        <p key={index}>row {index}</p>
      ))}
    </div>
  );
}

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

/** Stub layout geometry on the scroller (jsdom reports 0 for both). */
function stubScrollGeometry(scroller: HTMLElement, scroll_height: number, client_height: number): void {
  Object.defineProperty(scroller, "scrollHeight", { value: scroll_height, configurable: true });
  Object.defineProperty(scroller, "clientHeight", { value: client_height, configurable: true });
}

async function renderHarness(item_count: number): Promise<HTMLElement> {
  await act(async () => {
    root.render(<ScrollHarness item_count={item_count} />);
  });
  const scroller = container.querySelector<HTMLElement>("[data-testid='scroller']");
  expect(scroller).not.toBeNull();
  return scroller as HTMLElement;
}

describe("useBottomAnchoredScroll", () => {
  it("follows new content while the user is at the bottom (happy path)", async () => {
    const scroller = await renderHarness(3);
    stubScrollGeometry(scroller, 1000, 200);

    await act(async () => {
      root.render(<ScrollHarness item_count={4} />);
    });

    expect(scroller.scrollTop).toBe(1000);
  });

  it("does NOT yank a user who scrolled up when new content arrives (edge case)", async () => {
    const scroller = await renderHarness(3);
    stubScrollGeometry(scroller, 1000, 200);

    // The user scrolls well above the bottom (distance from bottom = 700px).
    scroller.scrollTop = 100;
    await act(async () => {
      scroller.dispatchEvent(new Event("scroll", { bubbles: false }));
    });
    await act(async () => {
      root.render(<ScrollHarness item_count={4} />);
    });

    expect(scroller.scrollTop).toBe(100);
  });

  it("re-arms following when the user returns to the bottom", async () => {
    const scroller = await renderHarness(3);
    stubScrollGeometry(scroller, 1000, 200);

    // Scroll up (unpin), then back to the bottom (re-pin).
    scroller.scrollTop = 100;
    await act(async () => {
      scroller.dispatchEvent(new Event("scroll", { bubbles: false }));
    });
    scroller.scrollTop = 800; // scrollHeight - clientHeight → exactly at bottom
    await act(async () => {
      scroller.dispatchEvent(new Event("scroll", { bubbles: false }));
    });
    await act(async () => {
      root.render(<ScrollHarness item_count={4} />);
    });

    expect(scroller.scrollTop).toBe(1000);
  });

  it("survives a missing container without crashing (failure case)", async () => {
    // Render with zero items and never stub geometry — jsdom's 0/0 layout means
    // distance-from-bottom is 0 (pinned); the effect must not throw.
    const scroller = await renderHarness(0);
    await act(async () => {
      root.render(<ScrollHarness item_count={1} />);
    });
    expect(scroller.scrollTop).toBe(0);
  });
});
