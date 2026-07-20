"use client";

/**
 * XClusterPicker — the in-chat X CLUSTERS phase (slice #20). Curated handle clusters
 * grouped under the user's interview categories (in pick order), plus a trailing "beyond
 * your picks" group for clusters in categories they didn't pick. Each cluster card shows
 * its name + gloss + a sample of member handles with an expandable FULL membership list,
 * and is multi-selectable at the cluster level.
 *
 * Selection is local UI state; nothing persists here. On confirm it hands the selected
 * resolved clusters (each carrying its cluster ref id + expanded members) up to
 * {@link InterviewChat}, which folds them into the terminal payload persisted at "Build my
 * 30" (terminal-only invariant). Zero picks is a valid confirm (those X slots default to news).
 *
 * Static-export safe: client-only, no `window` at module scope; the catalog read is an
 * injectable loader (tests pass a stub — no live X call during onboarding).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { CATEGORY_ROOT_IDS, DESIGN_BUCKETS, type DesignBucketId } from "@/lib/feedBuckets";
import { logger } from "@/lib/logger";
import { type ClusterPickerGroup, groupClustersForPicker, resolveClusterPicks } from "@/lib/onboardingSourcePicks";
import { toggleInSet } from "@/lib/setUtils";
import { getClustersForCategories, type ResolvedCluster } from "@/lib/sourceClusters";
import type { InterviewClusterPick } from "@/types/interview";

/** How many member handles show before the "+N MORE" expand. */
const SAMPLE_HANDLE_COUNT = 3;

/** Load the resolved clusters keyed by root — the injectable catalog read. */
export type ClusterLoader = (roots: string[]) => Promise<Map<string, ResolvedCluster[]>>;

const defaultClusterLoader: ClusterLoader = (roots) => getClustersForCategories(roots);

export interface XClusterPickerProps {
  /** The user's interview roots, most-wanted first — drives grouping + beyond-your-picks. */
  orderedRoots: string[];
  /** Confirm the picks: the selected clusters, pre-expanded for terminal persistence. */
  onConfirm: (clusterPicks: InterviewClusterPick[]) => void;
  /** Injectable catalog loader (tests pass a stub; defaults to the live cluster read). */
  loadClusters?: ClusterLoader;
}

/**
 * Render the in-chat X cluster checklist.
 */
export function XClusterPicker({ orderedRoots, onConfirm, loadClusters = defaultClusterLoader }: XClusterPickerProps) {
  const [groups, setGroups] = useState<ClusterPickerGroup[]>([]);
  const [selectedSlugs, setSelectedSlugs] = useState<Set<string>>(new Set());
  const [expandedSlugs, setExpandedSlugs] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  // Load every root's clusters ONCE on mount (so "beyond your picks" is complete), then group.
  // A read failure never blocks the picker (Rule 12: surfaced + skippable).
  useEffect(() => {
    let isMounted = true;
    setStatus("loading");
    loadClusters([...CATEGORY_ROOT_IDS])
      .then((byRoot) => {
        if (!isMounted) {
          return;
        }
        setGroups(groupClustersForPicker(byRoot, orderedRoots));
        setStatus("ready");
      })
      .catch((error: unknown) => {
        if (!isMounted) {
          return;
        }
        logger.error("x_cluster_picker_load_failed", {
          error_message: error instanceof Error ? error.message : "unknown",
          fix_suggestion:
            "Confirm source_clusters is readable; the picker stays skippable so onboarding never dead-ends.",
        });
        setStatus("error");
      });
    return () => {
      isMounted = false;
    };
  }, [orderedRoots, loadClusters]);

  /** Every rendered cluster, keyed by slug — resolves the selection to payload picks on confirm. */
  const clustersBySlug = useMemo(() => {
    const map = new Map<string, ResolvedCluster>();
    for (const group of groups) {
      for (const cluster of group.clusters) {
        map.set(cluster.cluster_slug, cluster);
      }
    }
    return map;
  }, [groups]);

  const toggleCluster = useCallback((slug: string) => {
    setSelectedSlugs((current) => toggleInSet(current, slug));
  }, []);

  const toggleExpand = useCallback((slug: string) => {
    setExpandedSlugs((current) => toggleInSet(current, slug));
  }, []);

  const handleConfirm = useCallback(() => {
    const selected = [...selectedSlugs]
      .map((slug) => clustersBySlug.get(slug))
      .filter((cluster): cluster is ResolvedCluster => cluster !== undefined);
    onConfirm(resolveClusterPicks(selected));
  }, [clustersBySlug, onConfirm, selectedSlugs]);

  return (
    <section data-testid="x-cluster-picker" className="flex flex-col gap-4">
      <span className="font-mono text-[11px] tracking-wide text-text-secondary">THE VOICES ON X</span>
      <h2 className="font-sans text-[17px] font-semibold leading-snug text-text-primary">
        On X, the good stuff lives in clusters.
      </h2>
      <p className="font-sans text-[13px] leading-relaxed text-text-secondary">
        Curated sets of handles that know their beat. Follow the ones that fit — expand any to see who&apos;s inside.
      </p>

      {status === "loading" ? (
        <div
          data-testid="x-cluster-loading"
          role="status"
          className="flex items-center gap-1.5 px-1 py-2"
          aria-label="Loading clusters"
        >
          <span className="h-1.5 w-1.5 animate-pulse rounded-pill bg-white/40" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-pill bg-white/40" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-pill bg-white/40" />
        </div>
      ) : null}

      {status === "error" ? (
        <p role="alert" className="font-sans text-[13px] leading-relaxed text-text-secondary">
          We couldn&apos;t load clusters right now — you can skip this and add voices later.
        </p>
      ) : null}

      {status === "ready" ? (
        <div className="flex flex-col gap-4">
          {groups.map((group) => {
            const rootLabel =
              group.root && group.root in DESIGN_BUCKETS ? DESIGN_BUCKETS[group.root as DesignBucketId].name : "";
            const headerText = group.is_beyond
              ? "BEYOND YOUR PICKS"
              : `${rootLabel} · ${group.clusters.length} CLUSTER${group.clusters.length > 1 ? "S" : ""}`;
            return (
              <div key={group.is_beyond ? "beyond" : (group.root ?? "unknown")} className="flex flex-col gap-2">
                <span
                  data-testid={group.is_beyond ? "beyond-your-picks" : "cluster-group-header"}
                  className="font-mono text-[10px] tracking-wide text-text-secondary"
                >
                  {headerText}
                </span>
                {group.clusters.map((cluster) => {
                  const isSelected = selectedSlugs.has(cluster.cluster_slug);
                  const isExpanded = expandedSlugs.has(cluster.cluster_slug);
                  const shownMembers = isExpanded ? cluster.members : cluster.members.slice(0, SAMPLE_HANDLE_COUNT);
                  const hiddenCount = cluster.members.length - SAMPLE_HANDLE_COUNT;
                  return (
                    <div
                      key={cluster.cluster_slug}
                      data-testid="cluster-card"
                      className={
                        isSelected
                          ? "flex flex-col gap-2 rounded-control border border-primary bg-primary/10 px-4 py-3"
                          : "flex flex-col gap-2 rounded-control border border-white/12 bg-white/5 px-4 py-3"
                      }
                    >
                      <button
                        type="button"
                        data-testid="cluster-toggle"
                        aria-pressed={isSelected}
                        onClick={() => toggleCluster(cluster.cluster_slug)}
                        className="flex flex-col gap-1 text-left"
                      >
                        <span className="font-sans text-[14px] font-semibold text-text-primary">
                          {cluster.cluster_label}
                        </span>
                        {cluster.cluster_description ? (
                          <span className="font-sans text-[12px] leading-snug text-text-secondary">
                            {cluster.cluster_description}
                          </span>
                        ) : null}
                      </button>
                      <div data-testid="cluster-handles" className="flex flex-wrap gap-1.5">
                        {shownMembers.map((member) => (
                          <span
                            key={member.followable_id}
                            className="rounded-pill border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[11px] text-text-secondary"
                          >
                            {member.display_name}
                          </span>
                        ))}
                      </div>
                      {hiddenCount > 0 ? (
                        <button
                          type="button"
                          data-testid="cluster-more"
                          onClick={() => toggleExpand(cluster.cluster_slug)}
                          className="self-start font-mono text-[10px] tracking-wide text-text-secondary transition-opacity active:opacity-60"
                        >
                          {isExpanded ? "SHOW LESS ‹" : `+${hiddenCount} MORE ›`}
                        </button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      ) : null}

      <button
        type="button"
        data-testid="x-cluster-confirm"
        onClick={handleConfirm}
        className="mt-1 w-full rounded-pill bg-white px-4 py-3 font-sans text-[15px] font-semibold text-background transition-opacity active:opacity-70"
      >
        {selectedSlugs.size > 0 ? `Follow ${selectedSlugs.size} →` : "Skip for now →"}
      </button>
    </section>
  );
}
