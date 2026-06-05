import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Component tests for the recursive interest picker page (Phase 5 SP4) —
 * `OnboardingPicker` + `SelectionTray` rendered against a real selection store.
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (no @testing-library — not a project dependency; the scope lock forbids
 * adding one), mirroring `tests/lib/detail/trustStrip.test.tsx`. The entity registry
 * (`@/lib/entities`) is MOCKED (CLAUDE.md mocking rule) so Show-more/Add-your-own
 * never hit Supabase; this suite exercises seed-chip selection + tray + skip gate.
 *
 * Rule 9 — these encode WHY the SP4 page matters, each failing on a real regression:
 *   - The page must render ALL 8 categories from PICKER_TREE (Health absent) — the
 *     §10 DoD "all 8 categories render"; a dropped/duplicated category FAILS.
 *   - Selecting a seed chip must flow through the SHARED store to the tray COUNT — a
 *     per-render store (the classic bug) would reset on every keystroke and FAIL this.
 *   - The Continue affordance is SKIPPABLE: it reads "Skip" at zero follows and
 *     "Continue" at >0, and is never disabled — a hard "pick ≥1" gate FAILS this.
 *   - `onComplete` fires with the canonical selections (`store.all()`) — the exact
 *     handoff the flow persists; an empty/garbled payload FAILS.
 */

// Tell React this is an act() environment so state updates flush synchronously.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { OnboardingPicker } from "@/components/onboarding/OnboardingPicker";
import { listEntities, searchEntities } from "@/lib/entities";
import { PICKER_TREE } from "@/lib/followSets";
import type { FollowSelection } from "@/types/picker";

// Mock the registry boundary — the picker's FollowSets call these on Show-more/search.
vi.mock("@/lib/entities", () => ({
  listEntities: vi.fn(),
  searchEntities: vi.fn(),
}));

const mockListEntities = vi.mocked(listEntities);
const mockSearchEntities = vi.mocked(searchEntities);

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  mockListEntities.mockReset();
  mockSearchEntities.mockReset();
  mockListEntities.mockResolvedValue({ results: [], nextCursor: null });
  mockSearchEntities.mockResolvedValue([]);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

/** Render the picker with a captured `onComplete` spy and flush effects. */
function renderPicker(onComplete: (selections: FollowSelection[]) => void): void {
  act(() => {
    root.render(<OnboardingPicker onComplete={onComplete} />);
  });
}

/** Click a button by a `data-*` attribute selector, flushing effects. */
function click(selector: string): void {
  const element = container.querySelector<HTMLButtonElement>(selector);
  if (element === null) {
    throw new Error(`element ${selector} not found`);
  }
  act(() => {
    element.click();
  });
}

/** Read the live tray total (the `data-tray-count` text). */
function trayCount(): string {
  return container.querySelector<HTMLElement>("[data-tray-count]")?.textContent ?? "";
}

describe("OnboardingPicker — categories, tray, skip gate, completion (Rule 9)", () => {
  it("renders all 8 categories from PICKER_TREE (Health absent)", () => {
    // WHY: the §10 DoD requires all 8 categories to render and Health to be absent.
    // A missing/duplicated category section FAILS this exact count + label check.
    renderPicker(() => undefined);
    const sections = container.querySelectorAll("[data-picker-category]");
    expect(sections).toHaveLength(PICKER_TREE.length);
    expect(PICKER_TREE).toHaveLength(8);
    const labels = container.textContent ?? "";
    expect(labels).toContain("AI");
    expect(labels).toContain("Sport");
    expect(labels.toLowerCase()).not.toContain("health");
  });

  it("starts skippable: Continue button reads 'Skip' and tray count is 0 with zero follows", () => {
    // WHY: the picker is NOT a hard gate (spec §10/§11). With nothing picked the CTA
    // must read a Skip affordance (never disabled). A "pick ≥1" gate FAILS this.
    renderPicker(() => undefined);
    const cta = container.querySelector<HTMLButtonElement>("[data-picker-continue]");
    expect(cta).not.toBeNull();
    expect(cta?.disabled).toBe(false);
    expect(cta?.textContent?.toLowerCase()).toContain("skip");
    expect(trayCount()).toBe("0");
  });

  it("selecting a seed chip flows through the shared store to the tray count and flips the CTA to Continue", () => {
    // WHY: the store must be created ONCE and shared — selecting a chip updates the
    // SAME store the tray reads. A per-render store (the classic React bug) would
    // reset selections and leave the count at 0; this asserts the count becomes 1 and
    // the CTA switches from Skip → Continue (proving live store wiring end-to-end).
    renderPicker(() => undefined);

    // Open the first category → its first subcategory → reveals the first follow-set.
    const firstCategory = PICKER_TREE[0];
    const firstSub = firstCategory.subs[0];
    click(`[data-category-toggle="${firstCategory.id}"]`);
    click(`[data-subcategory-toggle="${firstSub.id}"]`);

    // Tap the first seed chip in the revealed set.
    const firstChip = container.querySelector<HTMLButtonElement>("[data-follow-chip]");
    expect(firstChip).not.toBeNull();
    act(() => {
      firstChip?.click();
    });

    expect(trayCount()).toBe("1");
    const cta = container.querySelector<HTMLButtonElement>("[data-picker-continue]");
    expect(cta?.textContent?.toLowerCase()).toContain("continue");
    // The category header also surfaces the live per-category count.
    expect(container.querySelector(`[data-category-count="${firstCategory.id}"]`)?.textContent).toBe("1");
  });

  it("onComplete fires with the canonical selections (store.all()) when Continue is tapped", () => {
    // WHY: the exact handoff the flow persists. Tapping the CTA must hand back the
    // selected follow as a §7-shaped FollowSelection; an empty/garbled payload FAILS.
    let captured: FollowSelection[] | null = null;
    renderPicker((selections) => {
      captured = selections;
    });

    const firstCategory = PICKER_TREE[0];
    const firstSub = firstCategory.subs[0];
    const firstSetItem = firstSub.sets[0].items[0];
    click(`[data-category-toggle="${firstCategory.id}"]`);
    click(`[data-subcategory-toggle="${firstSub.id}"]`);
    const firstChip = container.querySelector<HTMLButtonElement>("[data-follow-chip]");
    act(() => {
      firstChip?.click();
    });

    click("[data-picker-continue]");

    expect(captured).not.toBeNull();
    const selections = captured as unknown as FollowSelection[];
    expect(selections).toHaveLength(1);
    expect(selections[0].followId).toBe(firstSetItem.id);
    expect(selections[0].label).toBe(firstSetItem.label);
    // The first path segment is always the category label (the tray groups on it).
    expect(selections[0].path[0]).toBe(firstCategory.label);
  });

  it("onComplete fires with an EMPTY array on a pure skip (no selections, no error)", () => {
    // WHY: a zero-follow skip is valid (spec §11) — the CTA still completes, handing
    // back []. The flow persists nothing and routes to the breaking-news-only reel.
    let captured: FollowSelection[] | null = null;
    renderPicker((selections) => {
      captured = selections;
    });

    click("[data-picker-continue]");

    expect(captured).toEqual([]);
  });
});
