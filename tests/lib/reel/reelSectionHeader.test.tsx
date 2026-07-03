import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

/**
 * FSR slice #8 — `ReelSectionHeader` component rendering (the reel's section chip
 * + honesty line, drawn from the {@link SectionChipModel} that `computeSectionChips`
 * derived from `daily_feeds` row metadata).
 *
 * WHY (Rule 9): the chip is the user-visible proof of the interview's promise.
 * These tests fail if:
 *  - the header stops rendering the user's words verbatim (paraphrase/truncation
 *    in JS — only CSS may clip; the DOM text must stay the user's words), or
 *  - the honesty line disappears from a climbed slot (silent substitution — the
 *    owner's rejected failure mode), or
 *  - a direct slot grows a phantom fallback line.
 *
 * Rendering uses React 19 `createRoot` + `act` (no @testing-library — not a
 * project dependency), mirroring `tests/lib/reel/firstRunBanner.test.tsx`.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { ReelSectionHeader } from "@/components/blip/reel/ReelSectionHeader";

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

describe("ReelSectionHeader (FSR slice #8)", () => {
  it("renders the section chip text and no fallback line on a direct fill", () => {
    act(() => {
      root.render(<ReelSectionHeader chip={{ chip_label: "IPL — 4", fallback_label: null }} accentHex="#F59E0B" />);
    });

    const chip = container.querySelector(".seg-chip");
    expect(chip?.textContent).toContain("IPL — 4");
    expect(container.querySelector('[data-testid="section-fallback"]')).toBeNull();
  });

  it("renders the honesty line on a climbed slot", () => {
    act(() => {
      root.render(
        <ReelSectionHeader
          chip={{ chip_label: "IPL — 4", fallback_label: "Nothing new in IPL today — here's Cricket" }}
          accentHex="#F59E0B"
        />,
      );
    });

    const fallback = container.querySelector('[data-testid="section-fallback"]');
    expect(fallback?.textContent).toBe("Nothing new in IPL today — here's Cricket");
  });

  it("keeps a long user-vocabulary label verbatim in the DOM and clips only via CSS", () => {
    const longChip = "Formula 1 silly season — every driver-market rumor, all of it — 2";
    act(() => {
      root.render(<ReelSectionHeader chip={{ chip_label: longChip, fallback_label: null }} accentHex="#F59E0B" />);
    });

    const labelElement = container.querySelector<HTMLElement>('[data-testid="section-chip-label"]');
    // The user's words stay the user's words: full text present in the DOM …
    expect(labelElement?.textContent).toBe(longChip);
    // … and clipping is visual-only (ellipsis styles), never a rewrite.
    expect(labelElement?.style.textOverflow).toBe("ellipsis");
    expect(labelElement?.style.overflow).toBe("hidden");
    expect(labelElement?.style.whiteSpace).toBe("nowrap");
  });
});
