"use client";

/**
 * AppShell — the signed-in home shell that owns the "library" navigation
 * (Today · Archive · Sources · Thirty · Settings) introduced by the "App Surfaces" design.
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
 *  - "thirty"   → "Build your 30" — organize how the 30-story briefing is filled.
 *  - "settings" → account / subscription / sign-out.
 *
 * Static-export safe: client-only, no `window` at module scope.
 *
 * @example
 * <PhoneShell><AppShell /></PhoneShell>
 */

import { useEffect, useRef, useState } from "react";
import { type LibraryTab, type SelectableTab, TabBar } from "@/components/app/TabBar";
import { ArchiveScreen } from "@/components/blip/library/ArchiveScreen";
import { SourcesScreen } from "@/components/blip/library/SourcesScreen";
import { BlipReel } from "@/components/blip/reel/BlipReel";
import { SettingsLayer } from "@/components/blip/reel/SettingsLayer";
import { BuildYour30 } from "@/components/onboarding/BuildYour30";
import { categoryBucketsFromInterestVector, type DesignBucketId, sourceBucketsFromFollows } from "@/lib/feedBuckets";
import { rollUpInterestVector } from "@/lib/interestVector";
import { logger } from "@/lib/logger";
import { getFollowedSources } from "@/lib/sources";
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
  // The user's REAL backing for the "Thirty" tab: the category buckets they follow an interest
  // in (rolled up from topic + entity follows) + the source buckets they follow a source on.
  // Loaded once the Thirty tab first opens so "Build your 30" seeds + offers ONLY backed blocks
  // (owner rule 2026-06-17 — no phantom category/source slots). null = not yet loaded.
  const [thirtyBackedBuckets, setThirtyBackedBuckets] = useState<{
    category: DesignBucketId[];
    source: DesignBucketId[];
  } | null>(null);
  const hasLoadedThirtyBucketsRef = useRef(false);

  // Load the user's backed buckets the first time the Thirty tab opens (a deliberate, owner-only
  // navigation — no need to read on every app open). A failure is NON-FATAL: fall back to an
  // empty signal so "Build your 30" stays usable on the full default seed (Rule 12 — logged).
  useEffect(() => {
    if (activeTab !== "thirty" || hasLoadedThirtyBucketsRef.current) {
      return;
    }
    hasLoadedThirtyBucketsRef.current = true;
    let isMounted = true;
    void (async () => {
      try {
        const [interestVector, followedSources] = await Promise.all([rollUpInterestVector(), getFollowedSources()]);
        if (!isMounted) {
          return;
        }
        setThirtyBackedBuckets({
          category: categoryBucketsFromInterestVector(interestVector),
          source: sourceBucketsFromFollows(followedSources),
        });
      } catch (error) {
        if (isMounted) {
          setThirtyBackedBuckets({ category: [], source: [] });
        }
        logger.warn("app_shell_thirty_backed_buckets_load_failed", {
          error_message: error instanceof Error ? error.message : "unknown",
          fix_suggestion:
            "Confirm the user is authed and interest/source reads are permitted; using the default 30 seed.",
        });
      }
    })();
    return () => {
      isMounted = false;
    };
  }, [activeTab]);

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
          {activeTab === "thirty" ? (
            // The "Thirty" tab hosts the allocation editor (moved out of Settings); saving
            // the order returns to Today so the user sees their rebuilt briefing. We mount
            // BuildYour30 only once the user's backed buckets have loaded so the seed + Add
            // sheet are gated to real follows from first paint (no phantom blocks).
            <div className="thirty-tab">
              {thirtyBackedBuckets ? (
                <BuildYour30
                  embedded
                  onDone={() => setActiveTab(null)}
                  selectedCategoryBuckets={thirtyBackedBuckets.category}
                  followedSourceBuckets={thirtyBackedBuckets.source}
                />
              ) : null}
            </div>
          ) : null}
          {activeTab === "settings" ? <SettingsLayer /> : null}
          <TabBar activeTab={activeTab} onSelectTab={handleSelectTab} />
        </div>
      ) : null}
    </div>
  );
}
