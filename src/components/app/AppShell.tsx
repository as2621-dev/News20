"use client";

/**
 * AppShell — the signed-in home shell that owns the 4-tab "library" navigation
 * (Today · Archive · Sources · Settings) introduced by the "App Surfaces" design.
 *
 * The reel ({@link BlipReel}) is the immersive "Today" surface and stays mounted
 * underneath; the library surfaces (Archive / Sources / Settings) render as a
 * full-screen overlay ABOVE it with the persistent {@link TabBar} at the bottom.
 * The reel is never destroyed when the library opens (so returning to Today keeps
 * its loaded feed + audio-unlock state) — it is told `isLibraryOpen` so it pauses
 * narration while a library surface covers it.
 *
 * Tab semantics:
 *  - "today"    → close the library (back to the reel).
 *  - "archive"  → past briefings; tapping a day re-points the reel at that date.
 *  - "sources"  → "what you follow".
 *  - "settings" → account / subscription / sign-out.
 *
 * Static-export safe: client-only, no `window` at module scope.
 *
 * @example
 * <PhoneShell><AppShell /></PhoneShell>
 */

import { useState } from "react";
import { type LibraryTab, type SelectableTab, TabBar } from "@/components/app/TabBar";
import { ArchiveScreen } from "@/components/blip/library/ArchiveScreen";
import { SourcesScreen } from "@/components/blip/library/SourcesScreen";
import { BlipReel } from "@/components/blip/reel/BlipReel";
import { SettingsLayer } from "@/components/blip/reel/SettingsLayer";
import "@/styles/blip-flow.css";
import "@/styles/blip-library.css";

/** Today's `feed_date` (ISO `YYYY-MM-DD`) — the reel's default day. */
function todayFeedDate(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Render the reel + the library overlay, switching surfaces from the tab bar. */
export function AppShell() {
  // Which library surface is open over the reel; null = the reel itself (Today).
  const [activeTab, setActiveTab] = useState<LibraryTab | null>(null);
  // Which day's briefing the reel is showing — moved by tapping an Archive day.
  const [reelDate, setReelDate] = useState<string>(todayFeedDate());

  /** Tab selection: "today" closes the library; the rest swap the open surface. */
  const handleSelectTab = (tab: SelectableTab): void => {
    setActiveTab(tab === "today" ? null : tab);
  };

  /** Archive → "replay this day": point the reel at the date and close the library. */
  const handleOpenDay = (feedDate: string): void => {
    setReelDate(feedDate);
    setActiveTab(null);
  };

  return (
    <div className="relative h-full w-full overflow-hidden bg-background">
      <BlipReel feedDate={reelDate} isLibraryOpen={activeTab !== null} onOpenLibrary={(tab) => setActiveTab(tab)} />

      {activeTab !== null ? (
        <div className="app-library">
          {activeTab === "archive" ? <ArchiveScreen onOpenDay={handleOpenDay} /> : null}
          {activeTab === "sources" ? <SourcesScreen /> : null}
          {activeTab === "settings" ? <SettingsLayer /> : null}
          <TabBar activeTab={activeTab} onSelectTab={handleSelectTab} />
        </div>
      ) : null}
    </div>
  );
}
