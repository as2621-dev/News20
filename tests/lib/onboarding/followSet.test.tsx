import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Component tests for the recursive interest picker engine (Phase 5 SP3) —
 * `FollowSet` + `FollowChip` rendered against a real selection store.
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (no @testing-library — not a project dependency; the scope lock forbids
 * adding one), mirroring `tests/lib/detail/trustStrip.test.tsx`. The entity registry
 * (`@/lib/entities`) is MOCKED per the CLAUDE.md mocking rule — no Supabase is hit.
 *
 * Rule 9 — these encode WHY each behaviour matters, exercising the four §4 marquee
 * cases the product owner cares about as FIRST-CLASS tests, plus lazy-mount +
 * preserve-on-collapse, cross-path dedupe, and the Add-your-own free-text path:
 *   - Marquee nesting is the whole point of the recursive engine: selecting Earnings
 *     must REVEAL the Companies set with tickers; a non-recursive regression FAILS.
 *   - Preserve-on-collapse lives in the STORE: deselecting NFL hides its sets but a
 *     team selection survives, so re-selecting NFL restores it. A DOM-only impl FAILS.
 *   - Add-your-own free-text is a valid follow (spec §6): a no-match submit must store
 *     a `freetext` follow, not drop the input.
 */

// Tell React this is an act() environment so state updates flush synchronously
// inside act() and the "not configured to support act" warning is silenced.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { FollowSet } from "@/components/onboarding/FollowSet";
import type { EntityResult } from "@/lib/entities";
import { listEntities, searchEntities } from "@/lib/entities";
import { createSelectionStore, PICKER_TREE } from "@/lib/followSets";
import type { PickerFollowSet, SelectionStore } from "@/types/picker";

// Mock the registry boundary — Show-more (listEntities) + Add-your-own (searchEntities).
vi.mock("@/lib/entities", () => ({
  listEntities: vi.fn(),
  searchEntities: vi.fn(),
}));

const mockListEntities = vi.mocked(listEntities);
const mockSearchEntities = vi.mocked(searchEntities);

/** Resolve a lifted subcategory's first follow-set by category id + sub label. */
function setFor(categoryId: string, subLabel: string): { set: PickerFollowSet; path: string[] } {
  const category = PICKER_TREE.find((cat) => cat.id === categoryId);
  if (!category) {
    throw new Error(`category ${categoryId} not found`);
  }
  const sub = category.subs.find((candidate) => candidate.label === subLabel);
  if (!sub) {
    throw new Error(`sub ${subLabel} not found`);
  }
  return { set: sub.sets[0], path: [category.label, sub.label] };
}

let container: HTMLDivElement;
let root: Root;
let store: SelectionStore;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  store = createSelectionStore();
  mockListEntities.mockReset();
  mockSearchEntities.mockReset();
  // Default: no extra registry rows, no exhaust loop. Cases override as needed.
  mockListEntities.mockResolvedValue({ results: [], nextCursor: null });
  mockSearchEntities.mockResolvedValue([]);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

/** Render a follow-set into the container and flush effects. */
function renderSet(set: PickerFollowSet, path: string[]): void {
  act(() => {
    root.render(<FollowSet followSet={set} path={path} store={store} />);
  });
}

/** Find a chip button by its visible label (the first matching `data-follow-chip`). */
function chipByLabel(label: string): HTMLButtonElement {
  const chips = container.querySelectorAll<HTMLButtonElement>("[data-follow-chip]");
  const match = Array.from(chips).find((chip) => chip.textContent?.startsWith(label));
  if (!match) {
    throw new Error(
      `chip "${label}" not found; present: ${Array.from(chips)
        .map((c) => c.textContent)
        .join(" | ")}`,
    );
  }
  return match;
}

/** Click an element and flush effects. */
function click(element: HTMLElement): void {
  act(() => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

/** All currently-rendered set labels (eyebrow `data-set-label`). */
function renderedSetLabels(): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>("[data-set-label]")).map((el) => el.textContent ?? "");
}

describe("Marquee case 1 — Earnings → Companies-to-track (tickers, Select all, Show more)", () => {
  it("reveals the Companies set with tickers when Earnings is selected", () => {
    const { set, path } = setFor("business", "Corporate news");
    renderSet(set, path);
    // The nested Companies set is NOT shown until Earnings is selected (lazy mount).
    expect(renderedSetLabels()).not.toContain("Companies to track");

    click(chipByLabel("Earnings"));

    expect(renderedSetLabels()).toContain("Companies to track");
    // WHY: companies must render their ticker in the rust accent (spec §8).
    const nvidiaChip = chipByLabel("Nvidia");
    expect(nvidiaChip.dataset.ticker).toBe("NVDA");
    expect(nvidiaChip.querySelector("[data-ticker-label]")?.textContent).toBe("NVDA");
  });

  it("Show more appends a registry page with NO overlap against seed chips", async () => {
    // The Companies set's parent. Seed already shows Apple..Eli Lilly; the mocked page
    // returns one row already shown (Apple) + one new (Salesforce) → only the new one appends.
    const page: EntityResult[] = [
      {
        id: "business/corporate-news/what-to-track/earnings/companies-to-track/apple",
        label: "Apple",
        kind: "company",
      },
      {
        id: "business/corporate-news/what-to-track/earnings/companies-to-track/salesforce",
        label: "Salesforce",
        ticker: "CRM",
        kind: "company",
      },
    ];
    mockListEntities.mockResolvedValue({ results: page, nextCursor: null });

    const { set, path } = setFor("business", "Corporate news");
    renderSet(set, path);
    click(chipByLabel("Earnings"));

    const showMore = container.querySelector<HTMLButtonElement>("[data-show-more]");
    expect(showMore).not.toBeNull();
    await act(async () => {
      showMore?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Salesforce appended; Apple NOT duplicated (only one Apple chip remains).
    const appleChips = Array.from(container.querySelectorAll<HTMLButtonElement>("[data-follow-chip]")).filter((chip) =>
      chip.textContent?.startsWith("Apple"),
    );
    expect(appleChips).toHaveLength(1);
    expect(chipByLabel("Salesforce").dataset.ticker).toBe("CRM");
    // nextCursor null → Show-more control hidden (terminates).
    expect(container.querySelector("[data-show-more]")).toBeNull();
  });

  it("Select all turns every mounted chip on, then off", () => {
    const { set, path } = setFor("business", "Corporate news");
    renderSet(set, path);
    const selectAll = container.querySelector<HTMLButtonElement>("[data-select-all]");
    expect(selectAll).not.toBeNull();

    click(selectAll as HTMLButtonElement);
    // Every seed chip in this set is now selected (Earnings, M&A, Leadership, IPOs).
    for (const node of set.items) {
      expect(store.has(node.id)).toBe(true);
    }

    click(selectAll as HTMLButtonElement);
    for (const node of set.items) {
      expect(store.has(node.id)).toBe(false);
    }
  });
});

describe("Marquee case 2 — Energy & commodities → Oil & gas → three nested sets", () => {
  it("reveals exactly the three sub-sets when Oil & gas is selected", () => {
    const { set, path } = setFor("business", "Energy & commodities");
    renderSet(set, path);
    expect(renderedSetLabels()).not.toContain("Majors");

    click(chipByLabel("Oil & gas"));

    const labels = renderedSetLabels();
    expect(labels).toContain("Majors");
    expect(labels).toContain("Midstream & pipelines");
    expect(labels).toContain("Equipment, turbines & services");
  });
});

describe("Marquee case 3 — American football → NFL (Teams + People); College football independent", () => {
  it("NFL reveals BOTH a Teams set (with Show more) and a People set", () => {
    const { set, path } = setFor("sport", "American football");
    renderSet(set, path);

    click(chipByLabel("NFL"));

    const labels = renderedSetLabels();
    expect(labels).toContain("Teams you follow");
    expect(labels).toContain("People to follow");
    // Teams is an entity set with moreSeeds → a Show-more control is present.
    expect(container.querySelector("[data-show-more]")).not.toBeNull();
  });

  it("College football reveals its own sets INDEPENDENTLY of NFL", () => {
    const { set, path } = setFor("sport", "American football");
    renderSet(set, path);

    // Selecting College football (NOT NFL) reveals college sets; NFL stays collapsed.
    click(chipByLabel("College football"));
    expect(renderedSetLabels()).toContain("Teams you follow");
    // A college team selection is independent of any NFL state.
    click(chipByLabel("Georgia"));
    expect(store.all().some((sel) => sel.label === "Georgia")).toBe(true);
  });
});

describe("Marquee case 4 — Music → genre → Artists; multi-genre multi-select", () => {
  it("unfolds a genre's artists and supports selecting across MULTIPLE genres", () => {
    const { set, path } = setFor("arts", "Music");
    renderSet(set, path);

    click(chipByLabel("Pop"));
    expect(renderedSetLabels()).toContain("Artists & bands");
    click(chipByLabel("Taylor Swift"));

    // A second genre unfolds independently; its artist selects too.
    click(chipByLabel("Country"));
    click(chipByLabel("Zach Bryan"));

    const labels = store.all().map((sel) => sel.label);
    expect(labels).toContain("Taylor Swift");
    expect(labels).toContain("Zach Bryan");
    // Both genre chips themselves are also followed (they were tapped).
    expect(labels).toContain("Pop");
    expect(labels).toContain("Country");
  });
});

describe("Lazy-mount + preserve-on-collapse (spec §11) — state in the STORE, not the DOM", () => {
  it("select NFL → select a team → deselect NFL (collapse) → re-select NFL → team STILL selected", () => {
    const { set, path } = setFor("sport", "American football");
    renderSet(set, path);

    click(chipByLabel("NFL"));
    click(chipByLabel("Kansas City Chiefs"));
    expect(store.all().some((sel) => sel.label === "Kansas City Chiefs")).toBe(true);

    // Deselect NFL → the nested Teams/People sets COLLAPSE (unmount from the DOM)...
    click(chipByLabel("NFL"));
    expect(renderedSetLabels()).not.toContain("Teams you follow");
    // ...but the team selection is PRESERVED in the store (survives unmount).
    expect(store.all().some((sel) => sel.label === "Kansas City Chiefs")).toBe(true);

    // Re-select NFL → the sets remount and the Chiefs chip shows as selected again.
    click(chipByLabel("NFL"));
    expect(renderedSetLabels()).toContain("Teams you follow");
    expect(chipByLabel("Kansas City Chiefs").dataset.selected).toBe("true");
  });
});

describe("Cross-path dedupe at the component level (spec §11)", () => {
  it("selecting Nvidia under AI-hardware AND under Earnings yields ONE follow with both paths", () => {
    // Render the AI-hardware set and select Nvidia.
    const aiHardware = setFor("ai", "AI hardware & compute");
    renderSet(aiHardware.set, aiHardware.path);
    click(chipByLabel("Nvidia"));
    expect(store.count()).toBe(1);

    // Now render Business→Corporate news, open Earnings, select Nvidia again.
    const corp = setFor("business", "Corporate news");
    act(() => {
      root.render(<FollowSet followSet={corp.set} path={corp.path} store={store} />);
    });
    click(chipByLabel("Earnings")); // Earnings becomes its OWN follow
    click(chipByLabel("Nvidia")); // Nvidia via the second path → deduped, NOT a new entry

    // Nvidia is STILL exactly ONE canonical follow, now carrying BOTH paths — the
    // only other follow is Earnings itself (the chip we opened the nested set with).
    const nvidiaEntries = store.all().filter((sel) => sel.label === "Nvidia");
    expect(nvidiaEntries).toHaveLength(1);
    const nvidia = nvidiaEntries[0];
    const paths = [nvidia.path, ...(nvidia.extraPaths ?? [])];
    expect(paths.some((p) => p[0] === "AI")).toBe(true);
    expect(paths.some((p) => p.includes("Earnings"))).toBe(true);
    // And no Nvidia double-count: total follows = Earnings + the single Nvidia.
    expect(store.count()).toBe(2);
  });
});

describe("Add-your-own (spec §6) — resolved match vs free-text fallback", () => {
  it("a no-match submit stores the typed value as a FREE-TEXT custom follow", () => {
    mockSearchEntities.mockResolvedValue([]); // no registry match
    const { set, path } = setFor("ai", "AI hardware & compute");
    renderSet(set, path);

    const input = container.querySelector<HTMLInputElement>("[data-add-input]");
    if (!input) {
      throw new Error("add-your-own input not rendered");
    }
    act(() => {
      // React tracks the input value via its own setter — set then dispatch input.
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "Cerebras");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const submit = container.querySelector<HTMLButtonElement>("[data-add-submit]");
    click(submit as HTMLButtonElement);

    const custom = store.all().find((sel) => sel.label === "Cerebras");
    expect(custom).toBeDefined();
    expect(custom?.source).toBe("custom");
    expect(custom?.kind).toBe("freetext");
  });

  it("picking a resolved search suggestion stores it as a custom-resolved entity", async () => {
    const hit: EntityResult = { id: "ai/.../groq", label: "Groq", kind: "company" };
    mockSearchEntities.mockResolvedValue([hit]);
    const { set, path } = setFor("ai", "AI hardware & compute");
    renderSet(set, path);

    const input = container.querySelector<HTMLInputElement>("[data-add-input]");
    if (!input) {
      throw new Error("add-your-own input not rendered");
    }
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "Groq");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      // Wait out the debounce so searchEntities resolves and suggestions render.
      await new Promise((resolve) => setTimeout(resolve, 300));
    });

    const suggestions = container.querySelector("[data-search-suggestions]");
    expect(suggestions).not.toBeNull();
    click(chipByLabel("Groq"));

    const stored = store.all().find((sel) => sel.label === "Groq");
    expect(stored?.source).toBe("custom");
    expect(stored?.kind).toBe("company");
  });
});

describe("FollowChip accessibility — real buttons with aria-pressed + ≥44px target", () => {
  it("renders chips as <button> with aria-pressed reflecting selection and a 44px min target", () => {
    const { set, path } = setFor("business", "Macroeconomy");
    renderSet(set, path);
    const chip = chipByLabel("Inflation");
    expect(chip.tagName).toBe("BUTTON");
    expect(chip.getAttribute("aria-pressed")).toBe("false");
    expect(chip.style.minHeight).toBe("44px");

    click(chip);
    expect(chip.getAttribute("aria-pressed")).toBe("true");
  });
});
