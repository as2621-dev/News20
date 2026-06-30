"use client";

/**
 * SourceClusterGrid — the M6 per-category source/cluster selection surface (Phase
 * FSR-M6a SP3). Above the fold per category: the recommended CLUSTER cards
 * (pre-selected, opt-out — User Story 12); each cluster card bulk-toggles its
 * members. Below: the popularity-ordered MEMBER tiles, whose selected state visibly
 * flips when their cluster is toggled. The grid honors the NO-DUP rule by
 * construction — it renders only the resolver's output (SP1), where a personality
 * appears once as a personality member and its bundled handle rows are already
 * suppressed; the grid never re-derives membership, so a bundled handle can't leak
 * back as a separate tile.
 *
 * CONTROLLED-ish: it owns the {@link ClusterSelection} reducer state internally
 * (seeded from `clusters` + `recommendedClusterSlugs` via {@link buildInitialSelection}),
 * and calls `onSelectionChange` with the new state on every toggle so the parent
 * (SP4 flow) can read {@link resolveFollowSet} on continue. Selection MATH lives in
 * the pure SP2 model — the component only renders + dispatches.
 *
 * A chosen category with ZERO clusters renders a GRACEFUL FALLBACK (never randoms —
 * PRD edge case): a short "nothing curated here yet" note, not an empty void or a
 * fallback to unrelated sources.
 *
 * **Re-skin (CLAUDE.md):** reuses {@link ClusterCard} + {@link SourceArtwork} and the
 * SAME News20 dark-editorial tokens (no new design tokens — phase Out of Scope).
 */

import { type CSSProperties, useCallback, useEffect, useId, useRef, useState } from "react";
import { ClusterCard } from "@/components/sources/ClusterCard";
import { SourceArtwork } from "@/components/sources/SourceArtwork";
import {
  buildInitialSelection,
  type ClusterSelection,
  isMemberFollowed,
  type MemberRef,
  toggleCluster,
  toggleMember,
} from "@/lib/clusterSelection";
import type { ResolvedCluster, ResolvedClusterMember } from "@/lib/sourceClusters";

const TOKENS = {
  primary: "#3B82F6",
  bg: "#020617",
  textPrimary: "#FFFFFF",
  textSecondary: "#A1A1AA",
  border: "#D1D4BD",
} as const;

const DISPLAY_FONT_STACK = "Inter, system-ui, sans-serif";
const MONO_FONT_STACK = '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace';

const MEMBER_AVATAR_SIZE_PX = 56;

export interface SourceClusterGridProps {
  /**
   * The chosen categories in render order, each with its resolved clusters (SP1
   * {@link getClustersForCategories} output). A category with `[]` clusters renders
   * the graceful empty-cell fallback.
   */
  categories: ReadonlyArray<{ category: string; label: string; clusters: ResolvedCluster[] }>;
  /** The editorially-recommended cluster slugs to pre-select (opt-out). */
  recommendedClusterSlugs: readonly string[];
  /** Called with the new selection on every toggle (the parent reads resolveFollowSet on continue). */
  onSelectionChange?: (selection: ClusterSelection) => void;
}

/** Flatten every category's clusters into one list for the shared selection model. */
function allClusters(categories: SourceClusterGridProps["categories"]): ResolvedCluster[] {
  return categories.flatMap((c) => c.clusters);
}

/**
 * Render the per-category cluster + member selection surface.
 *
 * @param props - {@link SourceClusterGridProps}.
 *
 * @example
 * <SourceClusterGrid categories={cats} recommendedClusterSlugs={["ai-labs"]} onSelectionChange={setSel} />
 */
export function SourceClusterGrid({ categories, recommendedClusterSlugs, onSelectionChange }: SourceClusterGridProps) {
  const [selection, setSelection] = useState<ClusterSelection>(() =>
    buildInitialSelection(allClusters(categories), recommendedClusterSlugs),
  );

  // Emit the INITIAL pre-selected set once so the parent has the opt-out follow set
  // even if the user toggles nothing (continue must commit the pre-selection). Fires
  // once on mount — subsequent emits come from toggles via `apply`.
  const onSelectionChangeRef = useRef(onSelectionChange);
  onSelectionChangeRef.current = onSelectionChange;
  const didEmitInitial = useRef(false);
  useEffect(() => {
    if (didEmitInitial.current) {
      return;
    }
    didEmitInitial.current = true;
    onSelectionChangeRef.current?.(selection);
    // Reason: run-once initial emit; `selection` is the freshly-built pre-selection.
  }, [selection]);

  const apply = useCallback(
    (next: ClusterSelection) => {
      setSelection(next);
      onSelectionChange?.(next);
    },
    [onSelectionChange],
  );

  const onToggleCluster = useCallback(
    (clusterSlug: string) => apply(toggleCluster(selection, clusterSlug)),
    [apply, selection],
  );
  const onToggleMember = useCallback((ref: MemberRef) => apply(toggleMember(selection, ref)), [apply, selection]);

  return (
    <div data-source-cluster-grid="" style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      {categories.map((cell) => (
        <CategorySection
          key={cell.category}
          category={cell.category}
          label={cell.label}
          clusters={cell.clusters}
          selection={selection}
          onToggleCluster={onToggleCluster}
          onToggleMember={onToggleMember}
        />
      ))}
    </div>
  );
}

interface CategorySectionProps {
  category: string;
  label: string;
  clusters: ResolvedCluster[];
  selection: ClusterSelection;
  onToggleCluster: (clusterSlug: string) => void;
  onToggleMember: (ref: MemberRef) => void;
}

/** One category's section: a heading, its cluster cards, then its member tiles (or a fallback). */
function CategorySection({
  category,
  label,
  clusters,
  selection,
  onToggleCluster,
  onToggleMember,
}: CategorySectionProps) {
  const headingStyle: CSSProperties = {
    fontFamily: MONO_FONT_STACK,
    fontSize: 12,
    fontWeight: 600,
    letterSpacing: ".08em",
    textTransform: "uppercase",
    color: TOKENS.textSecondary,
  };

  // Empty-cell fallback: a chosen category with no curated clusters shows a note,
  // NEVER randoms (PRD edge case).
  if (clusters.length === 0) {
    return (
      <section
        data-category-section={category}
        data-empty="true"
        style={{ display: "flex", flexDirection: "column", gap: 12 }}
      >
        <h3 style={headingStyle}>{label}</h3>
        <p
          data-empty-fallback=""
          style={{ fontFamily: DISPLAY_FONT_STACK, fontSize: 13, color: TOKENS.textSecondary, lineHeight: 1.4 }}
        >
          Nothing curated here yet — your {label} news still leads your feed.
        </p>
      </section>
    );
  }

  return (
    <section data-category-section={category} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h3 style={headingStyle}>{label}</h3>

      {/* Cluster cards (pre-selected, opt-out). */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {clusters.map((cluster) => (
          <ClusterCard
            key={cluster.cluster_slug}
            cluster={cluster}
            selected={selection.selectedClusterSlugs.has(cluster.cluster_slug)}
            onToggle={() => onToggleCluster(cluster.cluster_slug)}
          />
        ))}
      </div>

      {/* Member tiles — selection flips when their cluster toggles. */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 12 }}>
        {clusters.flatMap((cluster) =>
          cluster.members.map((member) => (
            <MemberTile
              key={`${cluster.cluster_slug}:${member.kind}:${member.followable_id}`}
              member={member}
              selected={isMemberFollowed(selection, member)}
              onToggle={() => onToggleMember({ kind: member.kind, followable_id: member.followable_id })}
            />
          )),
        )}
      </div>
    </section>
  );
}

interface MemberTileProps {
  member: ResolvedClusterMember;
  selected: boolean;
  onToggle: () => void;
}

/**
 * One selectable member tile (avatar + name + `aria-pressed`). Lightweight (the
 * resolver gives name + kind, not a full catalog row), so it renders via
 * {@link SourceArtwork}'s initials fallback rather than a thumbnail — sufficient for
 * the selection surface; the full {@link SourceCard} is for the catalog browse.
 */
function MemberTile({ member, selected, onToggle }: MemberTileProps) {
  const nameId = useId();
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={selected}
      aria-labelledby={nameId}
      data-member-tile={member.followable_id}
      data-member-kind={member.kind}
      data-selected={selected ? "true" : "false"}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
        padding: 12,
        cursor: "pointer",
        borderRadius: 16,
        border: `1px solid ${selected ? TOKENS.primary : TOKENS.border}`,
        background: TOKENS.bg,
        color: TOKENS.textPrimary,
        textAlign: "center",
      }}
    >
      <SourceArtwork
        source_name={member.display_name}
        image_url={null}
        kind={member.kind === "personality" ? "personality" : "youtube_channel"}
        size={MEMBER_AVATAR_SIZE_PX}
      />
      <span
        id={nameId}
        style={{
          fontFamily: DISPLAY_FONT_STACK,
          fontSize: 13,
          fontWeight: 600,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: "100%",
        }}
      >
        {member.display_name}
      </span>
    </button>
  );
}
