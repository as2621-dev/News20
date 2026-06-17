"use client";

/**
 * SourcesScreen — the "Sources · What you follow" library surface (App Surfaces
 * design). Renders, against real data:
 *  - platform summary cards (Topics + a card per source axis the user follows)
 *    that act as a tap-to-filter control (tap a card → that axis only; tap again
 *    to clear). `activeFilter` drives which sections render.
 *  - the user's interest chips with locked category colors ({@link getUserInterests}),
 *    plus a working "+ Add interest" that opens {@link AddInterestOverlay}.
 *  - a live search-and-add bar ({@link AddSourceSearch}) for the searchable axes.
 *  - the followed channels/people list with working active/paused toggles
 *    (`source_priority`: `off` = paused, anything else = active).
 *
 * Renders as a flex column (header + scroll) so it slots under {@link AppShell}'s
 * `.app-library` with the tab bar pinned below.
 */

import { type CSSProperties, useCallback, useEffect, useState } from "react";
import { AddInterestOverlay, AddSourceSearch } from "@/components/blip/library/SourcesAddControls";
import { formatSubscriberCount } from "@/components/blip/reel/SettingsLayer";
import { getUserInterests, type UserInterestChip } from "@/lib/interests";
import { logger } from "@/lib/logger";
import { type FollowedSourceWithPriority, getFollowedSourcesWithPriority, setSourcePriority } from "@/lib/sources";
import type { ContentSourceType } from "@/types/source";

/** Per-axis display: the sprite glyph id + short label + a card accent token. */
const AXIS_DISPLAY: Record<ContentSourceType, { glyph: string; label: string; accent: string }> = {
  youtube_channel: { glyph: "g-yt", label: "YouTube", accent: "var(--geo)" },
  podcast: { glyph: "g-pod", label: "Podcast", accent: "var(--sport)" },
  x_account: { glyph: "g-x", label: "X", accent: "rgba(255,255,255,0.6)" },
  personality: { glyph: "g-people", label: "Person", accent: "var(--mkt)" },
};

/** A `<use>` glyph sized for the avatar badge / platform card. */
function Glyph({ id }: { id: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <use href={`#${id}`} />
    </svg>
  );
}

/**
 * Which platform card is filtering the surface. `null` = show everything;
 * `"topics"` = interests only; a {@link ContentSourceType} = that source axis only.
 */
type SourcesFilter = "topics" | ContentSourceType | null;

/** Render the Sources surface against the user's real follows + interests. */
export function SourcesScreen() {
  // null until the first load resolves (skeleton → list/empty swap).
  const [followed, setFollowed] = useState<FollowedSourceWithPriority[] | null>(null);
  const [interests, setInterests] = useState<UserInterestChip[]>([]);
  // Active platform-card filter (tap a card to set, tap it again to clear).
  const [activeFilter, setActiveFilter] = useState<SourcesFilter>(null);
  // When true, the interest picker overlay is open over the surface.
  const [isAddingInterest, setIsAddingInterest] = useState<boolean>(false);

  /** (Re-)read the user's followed sources; called on mount + after an add. */
  const loadFollowed = useCallback(async (): Promise<void> => {
    try {
      setFollowed(await getFollowedSourcesWithPriority());
    } catch (sourcesError: unknown) {
      logger.error("sources_screen_follows_read_failed", {
        error_message: sourcesError instanceof Error ? sourcesError.message : "unknown",
        fix_suggestion: "User may be signed out, or confirm migration 0009 + content_sources read access.",
      });
      setFollowed([]);
    }
  }, []);

  /** (Re-)read the user's selected interests; called on mount + after an add. */
  const loadInterests = useCallback(async (): Promise<void> => {
    setInterests(await getUserInterests());
  }, []);

  useEffect(() => {
    void loadFollowed();
    void loadInterests();
  }, [loadFollowed, loadInterests]);

  /** Toggle a follow between active (`everything`) and paused (`off`), optimistically. */
  const handleToggle = async (source: FollowedSourceWithPriority): Promise<void> => {
    const nextPriority = source.source_priority === "off" ? "everything" : "off";
    setFollowed((current) =>
      (current ?? []).map((item) =>
        item.source_id === source.source_id ? { ...item, source_priority: nextPriority } : item,
      ),
    );
    try {
      await setSourcePriority(source.source_id, nextPriority);
      logger.info("sources_screen_priority_set", { source_id: source.source_id, priority: nextPriority });
    } catch (toggleError: unknown) {
      logger.error("sources_screen_priority_set_failed", {
        source_id: source.source_id,
        error_message: toggleError instanceof Error ? toggleError.message : "unknown",
        fix_suggestion: "Confirm the user is signed in and user_content_sources allows the update.",
      });
      // Revert the optimistic flip on failure (Rule 12 — don't silently lie about the state).
      setFollowed((current) =>
        (current ?? []).map((item) =>
          item.source_id === source.source_id ? { ...item, source_priority: source.source_priority } : item,
        ),
      );
    }
  };

  const activeFollowed = (followed ?? []).filter((source) => source.source_priority !== "off");

  // Platform cards: Topics (interest count) + one card per axis the user actually follows.
  const axisCounts = new Map<ContentSourceType, number>();
  for (const source of activeFollowed) {
    axisCounts.set(source.content_source_type, (axisCounts.get(source.content_source_type) ?? 0) + 1);
  }

  // Filter wiring: a `null` filter shows both sections; "topics" → interests only;
  // an axis → the channels list narrowed to that axis only.
  const showInterests = activeFilter === null || activeFilter === "topics";
  const showChannels = activeFilter === null || activeFilter !== "topics";
  const visibleFollowed =
    activeFilter !== null && activeFilter !== "topics"
      ? (followed ?? []).filter((source) => source.content_source_type === activeFilter)
      : (followed ?? []);

  return (
    <>
      <div className="art-top" />
      <div className="art-scroll">
        <div className="kicker">Sources</div>
        <h1 className="htitle">What you follow</h1>
        <p className="hsub">The signal blip listens to so your briefing stays yours.</p>

        <div className="plat-row">
          <button
            type="button"
            className={`plat-card${activeFilter === "topics" ? " on" : ""}`}
            onClick={() => setActiveFilter((current) => (current === "topics" ? null : "topics"))}
          >
            <div className="pi" style={{ background: "color-mix(in oklab, var(--tech) 20%, transparent)" }}>
              <svg viewBox="0 0 24 24" style={{ color: "var(--tech)" }} aria-hidden="true">
                <use href="#i-spark" />
              </svg>
            </div>
            <div className="pl">Topics</div>
            <div className="pc">{interests.length} active</div>
          </button>
          {([...axisCounts.entries()] as [ContentSourceType, number][]).map(([axis, count]) => (
            <button
              type="button"
              className={`plat-card${activeFilter === axis ? " on" : ""}`}
              key={axis}
              onClick={() => setActiveFilter((current) => (current === axis ? null : axis))}
            >
              <div
                className="pi"
                style={{ background: `color-mix(in oklab, ${AXIS_DISPLAY[axis].accent} 20%, transparent)` }}
              >
                <span style={{ color: AXIS_DISPLAY[axis].accent } as CSSProperties}>
                  <Glyph id={AXIS_DISPLAY[axis].glyph} />
                </span>
              </div>
              <div className="pl">{AXIS_DISPLAY[axis].label}</div>
              <div className="pc">{count} active</div>
            </button>
          ))}
        </div>

        {/* Live search-and-add for the searchable axes (YouTube / Podcast / X). */}
        <AddSourceSearch onAdded={() => void loadFollowed()} />

        {showInterests ? (
          <>
            <div className="seclabel">Interests</div>
            <div className="chips">
              {interests.map((chip) => (
                <span className="chip" key={chip.interestId}>
                  <span className="cd" style={{ background: chip.accentHex ?? "rgba(255,255,255,0.4)" }} />
                  {chip.label}
                </span>
              ))}
              <button type="button" className="chip add" onClick={() => setIsAddingInterest(true)}>
                + Add interest
              </button>
            </div>
          </>
        ) : null}

        {showChannels ? (
          <>
            <div className="seclabel">Channels &amp; people</div>
            {followed === null ? (
              <p className="lib-empty">Loading your sources…</p>
            ) : visibleFollowed.length === 0 ? (
              <p className="lib-empty">
                {activeFilter !== null
                  ? `You're not following any ${AXIS_DISPLAY[activeFilter].label} sources yet.`
                  : "You're not following any channels or people yet. Add some to shape your briefing."}
              </p>
            ) : (
              visibleFollowed.map((source) => {
                const axis = AXIS_DISPLAY[source.content_source_type];
                const isActive = source.source_priority !== "off";
                const meta = [
                  source.content_source_type === "personality" ? "Person" : axis.label,
                  source.source_description,
                ]
                  .filter(Boolean)
                  .join(" · ");
                const subscriberLabel = formatSubscriberCount(source.subscriber_count);
                return (
                  <div className="follow-row" key={source.source_id}>
                    <div className="av sq">
                      {source.thumbnail_url ? (
                        // biome-ignore lint/performance/noImgElement: small remote avatar in a static export; next/image is inappropriate here.
                        <img src={source.thumbnail_url} alt="" />
                      ) : (
                        <span className="mono">{source.source_name.charAt(0).toUpperCase()}</span>
                      )}
                      <span className="pbadge">
                        <Glyph id={axis.glyph} />
                      </span>
                    </div>
                    <div className="ft">
                      <div className="ftn">{source.source_name}</div>
                      <div className="fts">
                        <span className={isActive ? "actv" : "paus"}>{isActive ? "Active" : "Paused"}</span>
                        {meta ? ` · ${meta}` : null}
                        {subscriberLabel ? ` · ${subscriberLabel}` : null}
                      </div>
                    </div>
                    <button
                      type="button"
                      className={`tg${isActive ? " on" : ""}`}
                      role="switch"
                      aria-checked={isActive}
                      aria-label={`${isActive ? "Pause" : "Activate"} ${source.source_name}`}
                      onClick={() => void handleToggle(source)}
                    >
                      <span className="knob" />
                    </button>
                  </div>
                );
              })
            )}
          </>
        ) : null}
      </div>

      {isAddingInterest ? (
        <AddInterestOverlay onClose={() => setIsAddingInterest(false)} onSaved={() => void loadInterests()} />
      ) : null}
    </>
  );
}
