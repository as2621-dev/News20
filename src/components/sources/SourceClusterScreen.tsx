"use client";

/**
 * SourceClusterScreen — the M6 source/cluster onboarding step container (Phase
 * FSR-M6a SP4). It sits AFTER the category picker: it loads the chosen categories'
 * resolved clusters ({@link getClustersForCategories} — SP1, no-dup applied), renders
 * the opt-out {@link SourceClusterGrid} (SP3), and on continue commits the resolved
 * follow set ({@link commitClusterFollowSet} — SP4) to `user_content_sources` /
 * `user_personalities`, then calls `onDone`.
 *
 * Recommended clusters are PRE-SELECTED (opt-out — User Story 12). Which clusters are
 * "recommended" is M1's editorial signal; until M1 pins an `is_recommended` flag
 * (Open Q2 in the phase file), this pre-selects EVERY resolved cluster per category
 * (the opt-out default — the user deselects what they don't want). When M1 exposes
 * the flag, swap {@link recommendedSlugsFor} to read it.
 *
 * Offline-safe: an un-seeded catalog yields no clusters → every category renders the
 * graceful empty fallback and continue still advances (a zero-follow commit is a
 * no-op — User Story 21). A LOAD failure is surfaced inline (Rule 12), with a continue
 * that still lets the user proceed (the feed works via shared-backbone news).
 *
 * Client-only (client Supabase reads/writes; no server runtime on device).
 */

import { type CSSProperties, useEffect, useMemo, useState } from "react";
import { SourceClusterGrid } from "@/components/sources/SourceClusterGrid";
import type { ClusterSelection, ResolvedFollowSet } from "@/lib/clusterSelection";
import { resolveFollowSet } from "@/lib/clusterSelection";
import { DESIGN_BUCKETS, type DesignBucketId } from "@/lib/feedBuckets";
import { logger } from "@/lib/logger";
import { commitClusterFollowSet, getClustersForCategories, type ResolvedCluster } from "@/lib/sourceClusters";

const TOKENS = {
  primary: "#3B82F6",
  bg: "#020617",
  textPrimary: "#FFFFFF",
  textSecondary: "#A1A1AA",
} as const;

const DISPLAY_FONT_STACK = "Inter, system-ui, sans-serif";
const MONO_FONT_STACK = '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace';

export interface SourceClusterScreenProps {
  /** The user's chosen top-level category slugs (the 8 roots — from the picker). */
  categories: readonly string[];
  /** Called once the resolved follow set is committed (or a zero-follow skip advances). */
  onDone: (followSet: ResolvedFollowSet) => void;
}

/** A loaded per-category cell the grid renders. */
interface CategoryCell {
  category: string;
  label: string;
  clusters: ResolvedCluster[];
}

type LoadPhase = "loading" | "error" | "ready";

/** Human label for a category slug (from the shared design buckets; fallback to the slug). */
function labelFor(category: string): string {
  return DESIGN_BUCKETS[category as DesignBucketId]?.name ?? category;
}

/**
 * The recommended (pre-selected) cluster slugs across all cells. Until M1 pins an
 * editorial `is_recommended` flag (phase Open Q2), EVERY resolved cluster is
 * recommended — opt-out default. Swap the body to filter on the flag when it lands.
 */
function recommendedSlugsFor(cells: CategoryCell[]): string[] {
  return cells.flatMap((cell) => cell.clusters.map((cluster) => cluster.cluster_slug));
}

/**
 * Render the M6 source/cluster onboarding step.
 *
 * @param props - {@link SourceClusterScreenProps}.
 *
 * @example
 * <SourceClusterScreen categories={["ai", "tech"]} onDone={(set) => advance(set)} />
 */
export function SourceClusterScreen({ categories, onDone }: SourceClusterScreenProps) {
  const [loadPhase, setLoadPhase] = useState<LoadPhase>("loading");
  const [cells, setCells] = useState<CategoryCell[]>([]);
  const [selection, setSelection] = useState<ClusterSelection | null>(null);
  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);

  // Load the resolved clusters for the chosen categories (no-dup applied in SP1).
  useEffect(() => {
    let isMounted = true;
    setLoadPhase("loading");
    void getClustersForCategories([...categories])
      .then((byCategory) => {
        if (!isMounted) {
          return;
        }
        const loaded: CategoryCell[] = categories.map((category) => ({
          category,
          label: labelFor(category),
          clusters: byCategory.get(category) ?? [],
        }));
        setCells(loaded);
        setLoadPhase("ready");
      })
      .catch((error: unknown) => {
        if (!isMounted) {
          return;
        }
        logger.error("source_cluster_screen_load_failed", {
          error_message: error instanceof Error ? error.message : "unknown",
          fix_suggestion: "Confirm migrations 0009/0022 applied and the cluster seed ran; continue still advances.",
        });
        setLoadPhase("error");
      });
    return () => {
      isMounted = false;
    };
  }, [categories]);

  const recommendedSlugs = useMemo(() => recommendedSlugsFor(cells), [cells]);

  const handleContinue = async () => {
    // Resolve the opt-out follow set from the live selection (or empty when the user
    // touched nothing / there were no clusters).
    const followSet: ResolvedFollowSet = selection ? resolveFollowSet(selection) : { sources: [], personalities: [] };

    setCommitting(true);
    setCommitError(null);
    try {
      await commitClusterFollowSet(followSet);
      logger.info("source_cluster_screen_committed", {
        sources: followSet.sources.length,
        personalities: followSet.personalities.length,
      });
      onDone(followSet);
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "Couldn't save your follows.";
      logger.error("source_cluster_screen_commit_failed", {
        error_message: message,
        fix_suggestion: "Retry; if it persists confirm user_content_sources/user_personalities RLS permits the write.",
      });
      setCommitError(message);
      setCommitting(false);
    }
  };

  const surfaceStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 20,
    minHeight: "100dvh",
    padding: "24px 20px 96px",
    background: TOKENS.bg,
    color: TOKENS.textPrimary,
  };

  return (
    <div data-source-cluster-screen="" style={surfaceStyle}>
      <header style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span
          style={{ fontFamily: MONO_FONT_STACK, fontSize: 11, letterSpacing: ".08em", color: TOKENS.textSecondary }}
        >
          YOUR SOURCES · PICK WHO SHAPES YOUR FEED
        </span>
        <h2 style={{ fontFamily: DISPLAY_FONT_STACK, fontSize: 22, fontWeight: 700 }}>Follow the voices you value</h2>
        <p style={{ fontFamily: DISPLAY_FONT_STACK, fontSize: 13, color: TOKENS.textSecondary, lineHeight: 1.4 }}>
          We pre-picked clusters for your topics. Deselect anything you don&apos;t want — your follows lead your feed.
        </p>
      </header>

      {loadPhase === "loading" ? (
        <p data-load-state="loading" style={{ fontFamily: MONO_FONT_STACK, fontSize: 11, color: TOKENS.textSecondary }}>
          LOADING SOURCES…
        </p>
      ) : null}

      {loadPhase === "error" ? (
        <p
          data-load-state="error"
          role="alert"
          style={{ fontFamily: DISPLAY_FONT_STACK, fontSize: 13, color: TOKENS.textSecondary }}
        >
          Couldn&apos;t load sources right now — your category news still leads your feed. You can add follows later.
        </p>
      ) : null}

      {loadPhase === "ready" ? (
        <SourceClusterGrid
          categories={cells}
          recommendedClusterSlugs={recommendedSlugs}
          onSelectionChange={setSelection}
        />
      ) : null}

      {commitError ? (
        <p role="alert" style={{ fontFamily: MONO_FONT_STACK, fontSize: 11, color: "#EF4444" }}>
          {commitError}
        </p>
      ) : null}

      <button
        type="button"
        data-cluster-continue=""
        onClick={() => void handleContinue()}
        disabled={committing || loadPhase === "loading"}
        style={{
          alignSelf: "stretch",
          marginTop: "auto",
          padding: "14px 20px",
          borderRadius: 9999,
          border: "none",
          background: TOKENS.primary,
          color: TOKENS.bg,
          fontFamily: MONO_FONT_STACK,
          fontSize: 13,
          fontWeight: 700,
          letterSpacing: ".04em",
          textTransform: "uppercase",
          cursor: committing ? "default" : "pointer",
          opacity: committing || loadPhase === "loading" ? 0.6 : 1,
        }}
      >
        {committing ? "Saving…" : "Continue →"}
      </button>
    </div>
  );
}
