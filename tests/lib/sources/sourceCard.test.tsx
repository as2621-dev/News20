import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Component tests for the Phase 5c SP2 source UI — `SourceArtwork` (universal
 * avatar) + `SourceCard` (controlled selectable card) + the `portraitBg` helper.
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (NO @testing-library — not a project dependency; the scope lock forbids
 * adding one), mirroring `tests/lib/onboarding/followSet.test.tsx` and
 * `tests/lib/detail/trustStrip.test.tsx`. These are pure-prop components — no
 * Supabase / fetch is touched.
 *
 * Rule 9 — these encode WHY each behaviour matters, not just WHAT renders:
 *   - The avatar's whole reason to exist is RESILIENCE: catalog thumbnails come
 *     from arbitrary external CDNs that frequently 404 / reject hotlinks, so a
 *     broken URL MUST degrade to a legible initials tile, never a broken-image
 *     glyph. A test fires the real <img> onError and asserts the fallback replaces
 *     the <img>; an avatar that left the broken <img> in place FAILS.
 *   - Shape is a SEMANTIC signal (a person is a circle, a media channel is a
 *     square), so the user can tell "who" from "what" at a glance. A test asserts
 *     a personality renders circular and a channel/podcast square; a swapped/flat
 *     shape map FAILS.
 *   - The card is a CONTROLLED toggle: it must expose its follow state via
 *     aria-pressed (the only accessible signal of "am I following this?") and fire
 *     onToggle without persisting anything itself. A test asserts both, so a card
 *     that hid the state or self-persisted FAILS.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { SourceArtwork } from "@/components/sources/SourceArtwork";
import { SourceCard } from "@/components/sources/SourceCard";
import { initials, portraitGradient } from "@/lib/portraitBg";
import type { ContentSource } from "@/types/source";

/** Build a catalog row for tests, overriding only the fields a case cares about. */
function makeSource(overrides: Partial<ContentSource> = {}): ContentSource {
  return {
    source_id: "src-1",
    content_source_type: "youtube_channel",
    external_id: "UC123",
    source_name: "Two Minute Papers",
    source_description: "Bite-sized AI research breakdowns.",
    thumbnail_url: "https://i.ytimg.com/example.jpg",
    subscriber_count: 1_500_000,
    platform_metadata: null,
    personas: ["ai-frontier-tech"],
    topic_tags: ["ai"],
    popularity_score: 90,
    is_curated: true,
    last_fetched_at: null,
    ...overrides,
  };
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

/** Click an element and flush effects. */
function click(element: HTMLElement): void {
  act(() => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

/** The single rendered artwork tile. */
function artworkTile(): HTMLElement {
  const tile = container.querySelector<HTMLElement>("[data-source-artwork]");
  if (!tile) {
    throw new Error("source artwork tile not rendered");
  }
  return tile;
}

describe("portraitBg helper — stable, palette-constrained, sensible initials", () => {
  it("returns the SAME gradient for the same seed (no flicker) and a News20 palette stop", () => {
    // WHY: avatars must not re-roll their color between renders, so the gradient
    // must be a pure function of the seed.
    expect(portraitGradient("Acquired")).toBe(portraitGradient("Acquired"));
    // And it must stay inside the News20 palette — never a TL;DW amber like #ff8a3d.
    const gradient = portraitGradient("Acquired");
    expect(gradient).toMatch(/^linear-gradient\(135deg, #[0-9A-F]{6}, #[0-9A-F]{6}\)$/);
    expect(gradient.toLowerCase()).not.toContain("#ff8a3d");
  });

  it("derives 1-2 initials and falls back to '?' on a blank name", () => {
    expect(initials("Lex Fridman")).toBe("LF");
    expect(initials("Stratechery")).toBe("S");
    expect(initials("   ")).toBe("?");
  });
});

describe("SourceArtwork — thumbnail, broken-image fallback, and shape", () => {
  it("renders an <img> thumbnail with no-referrer when a URL is supplied", () => {
    act(() => {
      root.render(<SourceArtwork source_name="Two Minute Papers" image_url="https://x/y.jpg" kind="youtube_channel" />);
    });
    const img = artworkTile().querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe("https://x/y.jpg");
    expect(img?.getAttribute("referrerpolicy")).toBe("no-referrer");
  });

  it("FALLS BACK to the initials-gradient tile when the image URL 404s (onError)", () => {
    act(() => {
      root.render(<SourceArtwork source_name="Lex Fridman" image_url="https://broken/404.jpg" kind="personality" />);
    });
    const img = artworkTile().querySelector("img");
    expect(img).not.toBeNull();

    // Simulate the browser firing onError on a 404'd image.
    act(() => {
      img?.dispatchEvent(new Event("error", { bubbles: false }));
    });

    // The <img> is now GONE (single-shot fallback) and the initials tile is shown.
    expect(artworkTile().querySelector("img")).toBeNull();
    const fallback = container.querySelector<HTMLElement>("[data-source-artwork-fallback]");
    expect(fallback).not.toBeNull();
    expect(fallback?.textContent).toBe("LF");
    // The accessible name survives onto the fallback (role=img + aria-label).
    expect(fallback?.getAttribute("role")).toBe("img");
    expect(fallback?.getAttribute("aria-label")).toBe("Lex Fridman");
  });

  it("renders the initials fallback immediately when no URL is supplied", () => {
    act(() => {
      root.render(<SourceArtwork source_name="The Daily" image_url={null} kind="podcast" />);
    });
    expect(artworkTile().querySelector("img")).toBeNull();
    expect(container.querySelector("[data-source-artwork-fallback]")?.textContent).toBe("TD");
  });

  it("renders a PERSON as a circle and a CHANNEL/PODCAST as a rounded square", () => {
    // Person (personality) → circle: radius = size/2.
    act(() => {
      root.render(<SourceArtwork source_name="Lex Fridman" image_url={null} kind="personality" size={64} />);
    });
    expect(artworkTile().style.borderRadius).toBe("32px"); // 64 / 2

    // Channel → rounded square (control radius, NOT a half-circle).
    act(() => {
      root.render(<SourceArtwork source_name="Two Minute Papers" image_url={null} kind="youtube_channel" size={64} />);
    });
    expect(artworkTile().style.borderRadius).toBe("16px");

    // Podcast → also a rounded square.
    act(() => {
      root.render(<SourceArtwork source_name="The Daily" image_url={null} kind="podcast" size={64} />);
    });
    expect(artworkTile().style.borderRadius).toBe("16px");

    // x_account is a person axis → circle.
    act(() => {
      root.render(<SourceArtwork source_name="Paul Graham" image_url={null} kind="x_account" size={64} />);
    });
    expect(artworkTile().style.borderRadius).toBe("32px");
  });
});

describe("SourceCard — controlled selectable toggle with aria-pressed", () => {
  it("renders name + description + avatar and reflects selected via aria-pressed", () => {
    const source = makeSource();
    act(() => {
      root.render(<SourceCard source={source} selected={false} onToggle={() => {}} />);
    });

    const card = container.querySelector<HTMLButtonElement>("[data-source-card]");
    expect(card).not.toBeNull();
    expect(card?.tagName).toBe("BUTTON");
    // Unselected card announces "Follow" and aria-pressed=false.
    expect(card?.getAttribute("aria-pressed")).toBe("false");
    expect(card?.querySelector("[data-follow-toggle]")?.textContent).toBe("Follow");
    expect(card?.textContent).toContain("Two Minute Papers");
    expect(card?.querySelector("[data-source-description]")?.textContent).toContain("Bite-sized AI research");
    // The avatar is embedded.
    expect(card?.querySelector("[data-source-artwork]")).not.toBeNull();
  });

  it("a SELECTED card exposes aria-pressed=true and the 'Following' label", () => {
    act(() => {
      root.render(<SourceCard source={makeSource()} selected onToggle={() => {}} />);
    });
    const card = container.querySelector<HTMLButtonElement>("[data-source-card]");
    expect(card?.getAttribute("aria-pressed")).toBe("true");
    expect(card?.querySelector("[data-follow-toggle]")?.textContent).toBe("Following");
  });

  it("fires onToggle on click WITHOUT persisting anything itself (controlled)", () => {
    const onToggle = vi.fn();
    act(() => {
      root.render(<SourceCard source={makeSource()} selected={false} onToggle={onToggle} />);
    });
    const card = container.querySelector<HTMLButtonElement>("[data-source-card]");
    click(card as HTMLButtonElement);
    expect(onToggle).toHaveBeenCalledTimes(1);
    // The card does NOT flip its own aria-pressed — it's controlled; the parent
    // owns `selected`. (Re-render with selected={true} is the parent's job.)
    expect(card?.getAttribute("aria-pressed")).toBe("false");
  });
});
