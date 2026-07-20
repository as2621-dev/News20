import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

/**
 * Rendered-behavior tests for `SourceAvatarImage` — the library follow-row avatar
 * tile used by `SourcesScreen` (followed list) and `SourcesAddControls` (search
 * results), per the #21 no-avatar rescope (founder decision 2026-07-05).
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (NO @testing-library — not a project dependency), mirroring
 * `tests/lib/sources/sourceCard.test.tsx`.
 *
 * Rule 9 — these encode WHY the behavior matters, not just WHAT renders. The whole
 * point of the founder no-avatar rescope is that a source ALWAYS resolves to a
 * legible name-derived initial and NEVER depends on, or gets stuck showing a
 * broken/empty tile from, a remote avatar load. The prod catalog DOES carry
 * non-null `thumbnail_url`s (yt-dlp CDN URLs seeded for YouTube channels) that can
 * 404 / reject hotlinks, so:
 *   - a null URL must render the initial with NO <img> (no network dependency);
 *   - a populated URL that fires onError must DEGRADE to the initial, dropping the
 *     <img> (no broken/empty tile left behind).
 * A tile that left a broken <img> in place, or blanked instead of showing the
 * initial, FAILS.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { SourceAvatarImage } from "@/components/blip/library/SourceAvatarImage";

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

describe("SourceAvatarImage — names/handles-only resilient render (#21 no-avatar rescope)", () => {
  it("renders the name-derived initial with NO <img> when thumbnail_url is null", () => {
    // WHY: names/handles are the canonical render — a null thumbnail must never
    // pull in a remote image or leave an empty tile.
    act(() => {
      root.render(<SourceAvatarImage thumbnail_url={null} source_name="Lex Fridman" />);
    });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("span.mono")?.textContent).toBe("L");
  });

  it("falls back to '?' for a blank name with no thumbnail", () => {
    act(() => {
      root.render(<SourceAvatarImage thumbnail_url="" source_name="" />);
    });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("span.mono")?.textContent).toBe("?");
  });

  it("renders the remote <img> as a progressive nicety when a URL is supplied and loads", () => {
    act(() => {
      root.render(<SourceAvatarImage thumbnail_url="https://i.ytimg.com/a.jpg" source_name="Two Minute Papers" />);
    });
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe("https://i.ytimg.com/a.jpg");
    // No initial span while the image is live.
    expect(container.querySelector("span.mono")).toBeNull();
  });

  it("DEGRADES to the initial (dropping the <img>) when the thumbnail 404s / rejects the hotlink", () => {
    act(() => {
      root.render(<SourceAvatarImage thumbnail_url="https://broken/404.jpg" source_name="Two Minute Papers" />);
    });
    const img = container.querySelector("img");
    expect(img).not.toBeNull();

    // Simulate the browser firing onError on a 404'd / hotlink-rejected image.
    act(() => {
      img?.dispatchEvent(new Event("error", { bubbles: false }));
    });

    // The <img> is GONE (single-shot) and the legible initial is shown instead —
    // no broken/empty tile is left behind.
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("span.mono")?.textContent).toBe("T");
  });
});
