import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { StoryTimelineDrawer } from "@/components/detail/StoryTimelineDrawer";
import type { TimelineEvent } from "@/types/detail";

/**
 * Component tests for the Phase 2 SP4 "HOW IT DEVELOPED" timeline drawer.
 *
 * Rule 9 — these encode WHY the behaviour matters, not just WHAT renders:
 *   - The drawer's whole job is to keep a chronological development narrative
 *     COLLAPSED until asked for, then reveal it IN ORDER. So the tests assert:
 *       (1) it starts collapsed (no event text in the DOM),
 *       (2) the toggle is NOT a no-op — tapping expands it,
 *       (3) the events render in the EXACT received order (the test reads the
 *           rendered `timeline_when_label`s and compares to the input order, so a
 *           re-sort or a reversed render FAILS),
 *       (4) tapping again collapses it back.
 *   - The empty-timeline guard: a story with no events renders NOTHING (no empty
 *     header onto an empty drawer).
 *
 * Rendering uses React 19's `react-dom/client` + `react`'s `act` directly (no
 * @testing-library — not a project dependency; scope lock forbids adding one),
 * matching the SP2 `storyDetail.test.tsx` / SP3 `trustStrip.test.tsx` idiom.
 * StoryTimelineDrawer is pure-prop — no Supabase / fetch is touched.
 */

/**
 * Three development events in `timeline_event_index` order, exactly as
 * `fetchStoryDetail` hands them (already ordered — the drawer must NOT re-sort).
 * The `when` labels are deliberately distinct so an order assertion has teeth.
 */
const ORDERED_TIMELINE: TimelineEvent[] = [
  { timeline_event_index: 0, timeline_when_label: "08:10", timeline_what_text: "First reports surface." },
  { timeline_event_index: 1, timeline_when_label: "11:42", timeline_what_text: "Officials confirm the incident." },
  { timeline_event_index: 2, timeline_when_label: "16:05", timeline_what_text: "Markets react sharply." },
];

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

/** Render any element into the test container and flush effects. */
function render(node: React.ReactElement): void {
  act(() => {
    root.render(node);
  });
}

/** Click the timeline toggle header (the expand/collapse control). */
function clickToggle(): void {
  const toggle = container.querySelector<HTMLButtonElement>("[data-timeline-toggle]");
  if (toggle === null) {
    throw new Error("timeline toggle button not rendered");
  }
  act(() => {
    toggle.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

/**
 * Read the rendered events in DOM order: each `[data-timeline-event-index]` row's
 * `timeline_event_index` plus its `when` label (the first mono span). Returns the
 * actual render order so a re-sort/reverse is detectable.
 */
function readRenderedEvents(): { eventIndex: number; whenLabel: string }[] {
  const rows = container.querySelectorAll<HTMLElement>("[data-timeline-event-index]");
  return Array.from(rows).map((row) => ({
    eventIndex: Number(row.dataset.timelineEventIndex),
    whenLabel: row.querySelector("span.font-mono")?.textContent ?? "",
  }));
}

describe("StoryTimelineDrawer — collapsed-by-default + toggle + ordering (Rule 9)", () => {
  it("starts COLLAPSED: the header shows but no event text is in the DOM", () => {
    render(<StoryTimelineDrawer timeline={ORDERED_TIMELINE} />);
    // WHY: the drawer must default closed — the development narrative is opt-in.
    const toggle = container.querySelector<HTMLButtonElement>("[data-timeline-toggle]");
    expect(toggle).not.toBeNull();
    expect(toggle?.dataset.timelineToggle).toBe("collapsed");
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    // No event rows, and none of the event text leaks while collapsed.
    expect(container.querySelectorAll("[data-timeline-event-index]").length).toBe(0);
    expect(container.textContent).not.toContain("First reports surface.");
  });

  it("expands on tap and renders ALL events in the received index order", () => {
    render(<StoryTimelineDrawer timeline={ORDERED_TIMELINE} />);
    clickToggle();

    // WHY: the toggle must NOT be a no-op — after one tap the drawer is open.
    const toggle = container.querySelector<HTMLButtonElement>("[data-timeline-toggle]");
    expect(toggle?.dataset.timelineToggle).toBe("expanded");
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");

    // WHY: every event renders, IN the received order. Comparing the rendered
    // sequence to the input sequence makes a re-sort / reversed render FAIL.
    const rendered = readRenderedEvents();
    expect(rendered.map((event) => event.eventIndex)).toEqual([0, 1, 2]);
    expect(rendered.map((event) => event.whenLabel)).toEqual(
      ORDERED_TIMELINE.map((event) => event.timeline_when_label),
    );
    // And the what-text is present once expanded.
    expect(container.textContent).toContain("First reports surface.");
    expect(container.textContent).toContain("Markets react sharply.");
  });

  it("preserves a NON-sorted input order verbatim (does not re-sort by index)", () => {
    // WHY: the array arrives pre-ordered; the drawer renders it as-received. Feed
    // it in a deliberately scrambled order and assert that exact order survives —
    // a defensive `.sort()` inside the component would FAIL this.
    const scrambled: TimelineEvent[] = [ORDERED_TIMELINE[2], ORDERED_TIMELINE[0], ORDERED_TIMELINE[1]];
    render(<StoryTimelineDrawer timeline={scrambled} />);
    clickToggle();

    const rendered = readRenderedEvents();
    expect(rendered.map((event) => event.eventIndex)).toEqual([2, 0, 1]);
    expect(rendered.map((event) => event.whenLabel)).toEqual(["16:05", "08:10", "11:42"]);
  });

  it("collapses again on a second tap (toggle round-trips)", () => {
    render(<StoryTimelineDrawer timeline={ORDERED_TIMELINE} />);

    clickToggle(); // open
    const afterOpen = container.querySelector<HTMLButtonElement>("[data-timeline-toggle]");
    expect(afterOpen?.dataset.timelineToggle).toBe("expanded");
    expect(afterOpen?.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelectorAll("[data-timeline-event-index]").length).toBe(ORDERED_TIMELINE.length);

    clickToggle(); // close
    // WHY: a second tap must collapse — the state is a true two-way toggle, not a
    // one-way open. The toggle attribute + aria-expanded are the authoritative
    // expand/collapse state (framer-motion's AnimatePresence keeps the exiting
    // body mounted while the close height-animation runs — which never settles
    // under jsdom's no-op rAF — so the state attribute, not transient DOM
    // presence, is what proves the toggle round-tripped). A no-op toggle would
    // leave this "expanded" and FAIL.
    const afterClose = container.querySelector<HTMLButtonElement>("[data-timeline-toggle]");
    expect(afterClose?.dataset.timelineToggle).toBe("collapsed");
    expect(afterClose?.getAttribute("aria-expanded")).toBe("false");
  });
});

describe("StoryTimelineDrawer — empty-timeline guard (Rule 12)", () => {
  it("renders NOTHING for an empty timeline (no header onto an empty drawer)", () => {
    render(<StoryTimelineDrawer timeline={[]} />);
    // WHY: a story with no development events must not show an openable-but-empty
    // drawer — the component returns null.
    expect(container.querySelector("[data-timeline-toggle]")).toBeNull();
    expect(container.querySelector("section")).toBeNull();
    expect(container.textContent).not.toContain("HOW IT DEVELOPED");
  });
});
