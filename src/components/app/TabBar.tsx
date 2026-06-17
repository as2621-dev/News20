"use client";

/**
 * TabBar — the persistent bottom navigation for the "library" surfaces
 * (Today · Archive · Sources · Thirty · Settings), ported from the "App Surfaces"
 * design board's `.tabbar`. It renders only on the library surfaces (never over the
 * immersive reel); selecting "Today" returns to the reel.
 *
 * The glyphs resolve from the shared {@link BlipIconDefs} sprite (`#i-today`,
 * `#i-archive`, `#i-sources`, `#i-doc`, `#i-settings`), so a `<BlipIconDefs/>` must be
 * mounted above this component (the {@link AppShell} mounts it via the reel).
 *
 * @example
 * <TabBar activeTab="settings" onSelectTab={(tab) => …} />
 */

/** The library surfaces that own a full screen behind the nav. */
export type LibraryTab = "archive" | "sources" | "thirty" | "settings";

/** A tab the user can select — the three library surfaces plus "today" (the reel). */
export type SelectableTab = LibraryTab | "today";

/** One tab's icon symbol id + visible label, in left-to-right design order. */
const TAB_ITEMS: ReadonlyArray<{ tab: SelectableTab; iconId: string; label: string }> = [
  { tab: "today", iconId: "i-today", label: "Today" },
  { tab: "archive", iconId: "i-archive", label: "Archive" },
  { tab: "sources", iconId: "i-sources", label: "Sources" },
  { tab: "thirty", iconId: "i-doc", label: "Thirty" },
  { tab: "settings", iconId: "i-settings", label: "Settings" },
];

export interface TabBarProps {
  /** Which library surface is currently shown (drives the `.on` highlight). */
  activeTab: LibraryTab;
  /** Select a tab — "today" returns to the reel, the rest swap the library surface. */
  onSelectTab: (tab: SelectableTab) => void;
}

/** Render the bottom tab bar with the active surface highlighted. */
export function TabBar({ activeTab, onSelectTab }: TabBarProps) {
  return (
    <nav className="tabbar" aria-label="Primary">
      {TAB_ITEMS.map((item) => {
        const isActive = item.tab === activeTab;
        return (
          <button
            key={item.tab}
            type="button"
            className={`tab${isActive ? " on" : ""}`}
            aria-current={isActive ? "page" : undefined}
            onClick={() => onSelectTab(item.tab)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <use href={`#${item.iconId}`} />
            </svg>
            <span className="tlabel">{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
