import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TabBar } from "@/components/app/TabBar";

/**
 * Component tests for the 4-tab library nav (App Surfaces design).
 *
 * Rendering uses React 19's `react-dom/client` + `act` directly (no
 * @testing-library — not a project dependency), mirroring
 * tests/lib/sources/sourceCard.test.tsx.
 *
 * Rule 9 — these encode WHY the bar matters, not just that it renders:
 *   - It is the ONLY way to move between the library surfaces, so all four tabs
 *     (Today/Archive/Sources/Settings) must be present in design order.
 *   - The active surface must read as active (`.on` + aria-current) — a nav that
 *     never highlights the current tab strands the user.
 *   - "Today" must report a distinct selection so the shell can close the library
 *     and return to the reel (it is NOT a library surface).
 */

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

describe("TabBar", () => {
  it("renders all four tabs in design order (Today · Archive · Sources · Settings)", () => {
    act(() => root.render(<TabBar activeTab="settings" onSelectTab={vi.fn()} />));
    const labels = [...container.querySelectorAll(".tab .tlabel")].map((el) => el.textContent);
    expect(labels).toEqual(["Today", "Archive", "Sources", "Settings"]);
  });

  it("marks the active surface with .on + aria-current and no other tab", () => {
    act(() => root.render(<TabBar activeTab="archive" onSelectTab={vi.fn()} />));
    const onTabs = container.querySelectorAll(".tab.on");
    expect(onTabs).toHaveLength(1);
    expect(onTabs[0].querySelector(".tlabel")?.textContent).toBe("Archive");
    expect(onTabs[0].getAttribute("aria-current")).toBe("page");
  });

  it("reports 'today' as its own selection so the shell can return to the reel", () => {
    const onSelectTab = vi.fn();
    act(() => root.render(<TabBar activeTab="settings" onSelectTab={onSelectTab} />));
    const todayTab = [...container.querySelectorAll<HTMLButtonElement>(".tab")].find(
      (tab) => tab.querySelector(".tlabel")?.textContent === "Today",
    );
    act(() => todayTab?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(onSelectTab).toHaveBeenCalledWith("today");
  });

  it("reports a library surface selection when its tab is tapped", () => {
    const onSelectTab = vi.fn();
    act(() => root.render(<TabBar activeTab="settings" onSelectTab={onSelectTab} />));
    const sourcesTab = [...container.querySelectorAll<HTMLButtonElement>(".tab")].find(
      (tab) => tab.querySelector(".tlabel")?.textContent === "Sources",
    );
    act(() => sourcesTab?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(onSelectTab).toHaveBeenCalledWith("sources");
  });
});
