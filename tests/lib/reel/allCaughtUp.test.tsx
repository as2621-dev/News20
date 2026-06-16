import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Component test for the reel finish line `AllCaughtUp` (Phase 7b SP4).
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (no @testing-library — not a project dependency; the scope lock forbids
 * adding one), mirroring `tests/lib/onboarding/followSet.test.tsx`.
 *
 * Rule 9 — these encode WHY the copy matters, not just that text renders:
 *   - The finish-line body copy is the product PROMISE: it tells the user there is
 *     no infinite scroll and exactly what returns tomorrow ("your 30 stories, 30
 *     reels"). SP4 replaces the vague "That's the whole world today…" line, so a
 *     test asserts the NEW promise is present AND the OLD copy is gone — a stale
 *     string regression FAILS.
 *   - The mono finish counter `{n} / {n} · DONE` is the "reached the end" signal;
 *     SP4 must not touch it, so a test asserts it still renders for the real count.
 */

// Tell React this is an act() environment so state updates flush synchronously.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { AllCaughtUp } from "@/components/reel/AllCaughtUp";

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

/** Collapse all whitespace so JSX line breaks / entities compare cleanly. */
function normalizedText(): string {
  return (container.textContent ?? "").replace(/\s+/g, " ").trim();
}

describe("AllCaughtUp", () => {
  it("renders the new tomorrow-promise body copy and not the old 'whole world today' line", () => {
    act(() => {
      root.render(<AllCaughtUp onReplay={vi.fn()} storyCount={30} />);
    });

    const text = normalizedText();
    // New copy (curly apostrophes are how the &rsquo; entities render in textContent).
    expect(text).toContain("You’re all caught up. We’ll see you tomorrow with your 30 stories, 30 reels.");
    // Old copy must be gone.
    expect(text).not.toContain("That’s the whole world today");
    expect(text).not.toContain("the whole world today");
  });

  it("still renders the {n} / {n} · DONE finish counter for the real story count", () => {
    act(() => {
      root.render(<AllCaughtUp onReplay={vi.fn()} storyCount={17} />);
    });

    expect(normalizedText()).toContain("17 / 17 · DONE");
  });
});
