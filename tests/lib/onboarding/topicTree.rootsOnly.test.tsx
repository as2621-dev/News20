import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

/**
 * Render + contract tests for the **roots-only** onboarding interest picker
 * (`TopicTree`, FSR M5 SP1 + the SP4 lock-in checks).
 *
 * Rule 9 — these encode the PRODUCT RULE, not incidental render shape: M5 makes the
 * picker a single flat layer of the 8 canonical depth-0 category roots with NO
 * drill-down, so a fresh onboarding can only ever follow a ROOT (it never creates a
 * deep `user_interest_profile` row — that is what makes the M5 collapse a one-time
 * historical fixup, not a recurring need). Each assertion FAILS if drill-down /
 * deep follows are reintroduced:
 *   - exactly the 8 root labels render (a dropped/renamed root FAILS);
 *   - representative DEEP labels are absent (re-adding nesting FAILS);
 *   - no expand/caret control toggles children into view (a caret FAILS);
 *   - tapping a root then Done hands back exactly that root's follow — a `topic`
 *     follow keyed on the root slug, path `[RootLabel]` (SP4: roots-only IN means
 *     roots-only STORED — a sub-root follow shape FAILS).
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (no @testing-library — not a project dependency), mirroring
 * `tests/lib/onboarding/onboardingPicker.test.tsx`.
 */

// Tell React this is an act() environment so state updates flush synchronously.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { TopicTree } from "@/components/onboarding/TopicTree";
import { PICKER_TREE } from "@/lib/followSets";
import type { FollowSelection } from "@/types/picker";

/** The 8 canonical depth-0 category roots the picker may render — and ONLY these. */
const EXPECTED_ROOT_LABELS = ["AI", "Geopolitics", "Business", "Environment", "Politics", "Tech", "Sport", "Arts"];

/** Representative DEEP labels from inside the seed tree that must NEVER render now. */
const FORBIDDEN_DEEP_LABELS = ["Soccer", "Nvidia", "Foundation models & LLMs", "Premier League"];

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

/** Render the picker with a captured `onComplete` and flush effects. */
function renderTree(onComplete: (selections: FollowSelection[]) => void): void {
  act(() => {
    root.render(<TopicTree onComplete={onComplete} />);
  });
}

/** The text of every rendered topic-row label button (`.tlabel`). */
function rowLabels(): string[] {
  return [...container.querySelectorAll<HTMLElement>(".tlabel")].map((element) => element.textContent?.trim() ?? "");
}

describe("TopicTree — roots-only onboarding picker (FSR M5 SP1)", () => {
  it("renders exactly the 8 canonical category roots and nothing else", () => {
    // WHY: the picker shows the 8 depth-0 roots only. A 9th row, a dropped root, or a
    // legacy root label (world/climate/entertainment) leaking in FAILS this.
    renderTree(() => undefined);
    expect(rowLabels()).toEqual(EXPECTED_ROOT_LABELS);
  });

  it("renders NO descendant labels (no drill-down depth)", () => {
    // WHY: roots-only means a deep label can never appear. If nesting is reintroduced,
    // a representative deep label re-enters the DOM and this FAILS.
    renderTree(() => undefined);
    const text = container.textContent ?? "";
    for (const deepLabel of FORBIDDEN_DEEP_LABELS) {
      expect(text).not.toContain(deepLabel);
    }
  });

  it("exposes NO expand/caret control that toggles children into view", () => {
    // WHY: a roots-only picker has no drill-down affordance. The recursive picker used
    // interactive `.caret` buttons (aria-label Expand/Collapse) to open children;
    // none may exist now. A re-added caret button FAILS this.
    renderTree(() => undefined);
    const expanders = [...container.querySelectorAll<HTMLElement>(".caret")].filter(
      (element) => element.tagName === "BUTTON",
    );
    expect(expanders).toHaveLength(0);
    expect(container.querySelector("[aria-label='Expand']")).toBeNull();
    expect(container.querySelector("[aria-label='Collapse']")).toBeNull();
  });

  it("tapping a root then Done hands back exactly that root's follow (roots-only stored)", () => {
    // WHY (SP4 lock): roots-only IN must mean roots-only STORED. Selecting the Sport
    // root and tapping Done must hand back ONE `topic` follow keyed on the root slug
    // with path [RootLabel] — never a deep node. A sub-root follow shape FAILS.
    let captured: FollowSelection[] | null = null;
    renderTree((selections) => {
      captured = selections;
    });

    const sportLabelButton = [...container.querySelectorAll<HTMLButtonElement>(".tlabel")].find(
      (element) => element.textContent?.trim() === "Sport",
    );
    expect(sportLabelButton).not.toBeUndefined();
    act(() => {
      sportLabelButton?.click();
    });

    const done = container.querySelector<HTMLButtonElement>(".tcta");
    expect(done).not.toBeNull();
    act(() => {
      done?.click();
    });

    expect(captured).not.toBeNull();
    const selections = captured as unknown as FollowSelection[];
    expect(selections).toHaveLength(1);
    expect(selections[0].followId).toBe("sport");
    expect(selections[0].label).toBe("Sport");
    expect(selections[0].type).toBe("topic");
    expect(selections[0].path).toEqual(["Sport"]);
  });

  it("Done with zero selections is a valid skip (hands back [])", () => {
    // WHY: zero-selection completion stays skippable (unchanged from the picker it
    // replaces). Done must fire with [] — a hard "pick ≥1" gate FAILS this.
    let captured: FollowSelection[] | null = null;
    renderTree((selections) => {
      captured = selections;
    });
    const done = container.querySelector<HTMLButtonElement>(".tcta");
    act(() => {
      done?.click();
    });
    expect(captured).toEqual([]);
  });
});

describe("TopicTree — roots-only render is exhaustive over PICKER_TREE roots (SP4 parity)", () => {
  it("the rendered roots are exactly the PICKER_TREE depth-0 categories", () => {
    // WHY: the picker must render every canonical root and no extras — sourced from
    // PICKER_TREE itself, so adding/removing a root in the seed is reflected here and a
    // hardcoded drift between the seed and the render FAILS.
    renderTree(() => undefined);
    const seedRootLabels = PICKER_TREE.map((category) => category.label);
    expect(seedRootLabels).toEqual(EXPECTED_ROOT_LABELS);
    expect(rowLabels()).toEqual(seedRootLabels);
  });

  it("selecting every root emits ONLY root follows — never a deep node (SP4 lock)", () => {
    // WHY (the M5 contract this sub-phase LOCKS): roots-only IN must mean roots-only
    // STORED, so a fresh onboarding never creates a deep `user_interest_profile` row
    // (the collapse stays a one-time historical fixup). Select ALL 8 roots and assert
    // every handed-back follow is a depth-0 root: `type: 'topic'`, `followId` == the
    // root slug, and `path` of length 1 (just the root label — no ancestry). A future
    // change that lets the picker emit a sub-root follow (deeper path / non-root id /
    // entity type) FAILS here.
    let captured: FollowSelection[] | null = null;
    renderTree((selections) => {
      captured = selections;
    });

    for (const labelButton of container.querySelectorAll<HTMLButtonElement>(".tlabel")) {
      act(() => {
        labelButton.click();
      });
    }
    const done = container.querySelector<HTMLButtonElement>(".tcta");
    act(() => {
      done?.click();
    });

    const selections = captured as unknown as FollowSelection[];
    expect(selections).toHaveLength(EXPECTED_ROOT_LABELS.length);
    const rootSlugs = PICKER_TREE.map((category) => category.id);
    for (const selection of selections) {
      expect(selection.type).toBe("topic");
      expect(selection.path).toHaveLength(1);
      expect(rootSlugs).toContain(selection.followId);
    }
    // The set of emitted follow ids is exactly the 8 root slugs (no extras, no dupes).
    expect(new Set(selections.map((selection) => selection.followId))).toEqual(new Set(rootSlugs));
  });
});
