"use client";

/**
 * YoutubeChannelPicker — the in-chat YOUTUBE phase (slice #20). A tile grid of catalog
 * YouTube channels sorted by relevance to the interview picks (root `topic_tags` in pick
 * order), multi-selectable, with a live supply-expectation line ("these 12 channels ≈ ~5
 * long-form videos/day") that recomputes from CATALOG DATA ONLY as tiles toggle — never a
 * live YouTube call during onboarding.
 *
 * Selection is local UI state; nothing persists here. On confirm it hands the selected
 * `content_sources.source_id`s up to {@link InterviewChat}, which folds them into the
 * terminal payload persisted at "Build my 30" (terminal-only invariant). Zero picks is a
 * valid confirm (those YouTube slots default to news).
 *
 * Static-export safe: client-only, no `window` at module scope; the catalog read is an
 * injectable loader (tests pass a stub — no network in the test/verify path).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { SourceArtwork } from "@/components/sources/SourceArtwork";
import { CATEGORY_ROOT_IDS, DESIGN_BUCKETS, type DesignBucketId } from "@/lib/feedBuckets";
import { logger } from "@/lib/logger";
import { formatSupplyExpectation, sortChannelsByRelevance } from "@/lib/onboardingSourcePicks";
import { toggleInSet } from "@/lib/setUtils";
import { listSourcesByCategory } from "@/lib/sources";
import type { ContentSource } from "@/types/source";

/** How many channels the grid pulls (the PRD's "~30 tiles"). */
const CHANNEL_GRID_LIMIT = 40;

/** Load the candidate youtube channels for a set of roots — the injectable catalog read. */
export type ChannelLoader = (roots: string[]) => Promise<ContentSource[]>;

const defaultChannelLoader: ChannelLoader = (roots) =>
  listSourcesByCategory(roots, "youtube_channel", CHANNEL_GRID_LIMIT);

export interface YoutubeChannelPickerProps {
  /** The user's interview roots, most-wanted first — drives the relevance sort + load pool. */
  orderedRoots: string[];
  /** Confirm the picks: the selected channel source ids (empty is valid). */
  onConfirm: (youtubeSourceIds: string[]) => void;
  /** Injectable catalog loader (tests pass a stub; defaults to the live catalog read). */
  loadChannels?: ChannelLoader;
}

/**
 * Render the in-chat YouTube channel grid.
 */
export function YoutubeChannelPicker({
  orderedRoots,
  onConfirm,
  loadChannels = defaultChannelLoader,
}: YoutubeChannelPickerProps) {
  const [channels, setChannels] = useState<ContentSource[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  // Load the FULL category catalog ONCE on mount (every root, not just the picked ones) so the
  // grid IS the whole catalog — a high-popularity channel in an unpicked category still appears,
  // demoted by the relevance sort (the design shows the full grid, picks first). `orderedRoots`
  // drives the SORT, not the fetch. A read failure never blocks the picker (Rule 12: skippable).
  useEffect(() => {
    let isMounted = true;
    setStatus("loading");
    loadChannels([...CATEGORY_ROOT_IDS])
      .then((rows) => {
        if (!isMounted) {
          return;
        }
        setChannels(sortChannelsByRelevance(rows, orderedRoots));
        setStatus("ready");
      })
      .catch((error: unknown) => {
        if (!isMounted) {
          return;
        }
        logger.error("youtube_picker_load_failed", {
          error_message: error instanceof Error ? error.message : "unknown",
          fix_suggestion:
            "Confirm content_sources is readable; the picker stays skippable so onboarding never dead-ends.",
        });
        setStatus("error");
      });
    return () => {
      isMounted = false;
    };
  }, [orderedRoots, loadChannels]);

  const toggle = useCallback((sourceId: string) => {
    setSelectedIds((current) => toggleInSet(current, sourceId));
  }, []);

  // Supply expectation recomputes PURELY from the in-memory selection — no network on toggle.
  const selectedChannels = useMemo(
    () => channels.filter((channel) => selectedIds.has(channel.source_id)),
    [channels, selectedIds],
  );
  const supplyLine = formatSupplyExpectation(selectedChannels);

  const handleConfirm = useCallback(() => {
    onConfirm([...selectedIds]);
  }, [onConfirm, selectedIds]);

  return (
    <section data-testid="youtube-picker" className="flex flex-col gap-4">
      <span className="font-mono text-[11px] tracking-wide text-text-secondary">THE VIDEO SIDE</span>
      <h2 className="font-sans text-[17px] font-semibold leading-snug text-text-primary">
        Channels people with your profile follow.
      </h2>
      <p className="font-sans text-[13px] leading-relaxed text-text-secondary">
        Pick the ones you&apos;d want your daily video stories from. You can change this any time.
      </p>

      {status === "loading" ? (
        <div
          data-testid="youtube-loading"
          role="status"
          className="flex items-center gap-1.5 px-1 py-2"
          aria-label="Loading channels"
        >
          <span className="h-1.5 w-1.5 animate-pulse rounded-pill bg-white/40" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-pill bg-white/40" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-pill bg-white/40" />
        </div>
      ) : null}

      {status === "error" ? (
        <p role="alert" className="font-sans text-[13px] leading-relaxed text-text-secondary">
          We couldn&apos;t load channels right now — you can skip this and add channels later.
        </p>
      ) : null}

      {status === "ready" ? (
        <div data-testid="youtube-grid" className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
          {channels.map((channel) => {
            const isSelected = selectedIds.has(channel.source_id);
            const root = channel.topic_tags[0];
            const rootLabel = root && root in DESIGN_BUCKETS ? DESIGN_BUCKETS[root as DesignBucketId].name : "";
            return (
              <button
                key={channel.source_id}
                type="button"
                data-testid="youtube-tile"
                aria-pressed={isSelected}
                onClick={() => toggle(channel.source_id)}
                className={
                  isSelected
                    ? "flex flex-col items-center gap-1.5 rounded-control border border-primary bg-primary/10 px-2 py-3 text-center transition-colors"
                    : "flex flex-col items-center gap-1.5 rounded-control border border-white/12 bg-white/5 px-2 py-3 text-center transition-colors active:bg-white/10"
                }
              >
                <SourceArtwork
                  source_name={channel.source_name}
                  image_url={channel.thumbnail_url}
                  kind="youtube_channel"
                  size={44}
                />
                <span className="line-clamp-2 font-sans text-[12px] font-medium leading-tight text-text-primary">
                  {channel.source_name}
                </span>
                {rootLabel ? (
                  <span className="font-mono text-[9px] tracking-wide text-text-secondary">{rootLabel}</span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}

      <p data-testid="supply-expectation" className="font-mono text-[11px] tracking-wide text-primary">
        {supplyLine}
      </p>

      <button
        type="button"
        data-testid="youtube-confirm"
        onClick={handleConfirm}
        className="mt-1 w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-70"
      >
        {selectedIds.size > 0 ? `Add ${selectedIds.size} →` : "Skip for now →"}
      </button>
    </section>
  );
}
