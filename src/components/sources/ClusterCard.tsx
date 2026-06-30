"use client";

/**
 * ClusterCard — one selectable CLUSTER in the M6 source-onboarding grid (Phase
 * FSR-M6a SP3). A cluster bulk-follows all its members in one tap (User Story 11):
 * the card shows the cluster label, a member-count + member-avatar stack, and a
 * one-tap select/deselect toggle reflecting `aria-pressed`. Recommended clusters
 * arrive PRE-SELECTED (the parent passes `selected`); the user DESELECTS (opt-out).
 *
 * CONTROLLED (mirrors {@link SourceCard}): it does NOT own selection or persist
 * anything — the parent ({@link SourceClusterGrid}) owns the {@link ClusterSelection}
 * state, passes `selected`, and handles `onToggle`. The whole card is a real
 * `<button>` so the entire row is one keyboard-focusable, screen-reader-labelled
 * toggle.
 *
 * **Re-skin (CLAUDE.md / reuse-map §5):** reuses {@link SourceArtwork} for the member
 * avatar stack and the SAME News20 dark-editorial tokens as `SourceCard` (no new
 * design tokens — `reference/design-language.md` untouched, phase Out of Scope).
 */

import { type CSSProperties, useId } from "react";
import { SourceArtwork } from "@/components/sources/SourceArtwork";
import type { ResolvedCluster } from "@/lib/sourceClusters";

/** Design-language.md tokens (inlined, mirroring SourceCard — no globals.css edit). */
const TOKENS = {
  primary: "#3B82F6",
  bg: "#020617",
  textPrimary: "#FFFFFF",
  textSecondary: "#A1A1AA",
  border: "#D1D4BD",
} as const;

const DISPLAY_FONT_STACK = "Inter, system-ui, sans-serif";
const MONO_FONT_STACK = '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace';

const CARD_PADDING_PX = 16;
const CARD_RADIUS_PX = 16;
const AVATAR_SIZE_PX = 36;
/** How many member avatars the stack shows before a "+N" overflow chip. */
const MAX_AVATARS = 5;

export interface ClusterCardProps {
  /** The resolved cluster this card represents (label + ordered members). */
  cluster: ResolvedCluster;
  /** Whether the cluster is currently selected (drives fill + `aria-pressed`). */
  selected: boolean;
  /** Toggle handler — the parent flips the cluster's bulk selection. */
  onToggle: () => void;
}

/**
 * Render one controlled, selectable cluster card with a member-avatar stack.
 *
 * @param props - {@link ClusterCardProps}.
 *
 * @example
 * <ClusterCard cluster={c} selected={isSelected} onToggle={() => toggle(c.cluster_slug)} />
 */
export function ClusterCard({ cluster, selected, onToggle }: ClusterCardProps) {
  const labelId = useId();
  const toggleLabel = selected ? "Selected" : "Select all";
  const memberCount = cluster.members.length;
  const visibleMembers = cluster.members.slice(0, MAX_AVATARS);
  const overflow = memberCount - visibleMembers.length;

  const cardStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 12,
    width: "100%",
    padding: CARD_PADDING_PX,
    textAlign: "left",
    cursor: "pointer",
    borderRadius: CARD_RADIUS_PX,
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: selected ? TOKENS.primary : TOKENS.border,
    background: TOKENS.bg,
    color: TOKENS.textPrimary,
    transition: "border-color .12s",
  };

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={selected}
      aria-labelledby={labelId}
      data-cluster-card={cluster.cluster_slug}
      data-selected={selected ? "true" : "false"}
      style={cardStyle}
    >
      <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          <span
            id={labelId}
            style={{
              fontFamily: DISPLAY_FONT_STACK,
              fontSize: 15,
              fontWeight: 600,
              color: TOKENS.textPrimary,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {cluster.cluster_label}
          </span>
          <span
            data-cluster-member-count=""
            style={{ fontFamily: MONO_FONT_STACK, fontSize: 11, color: TOKENS.textSecondary, letterSpacing: ".04em" }}
          >
            {memberCount} {memberCount === 1 ? "account" : "accounts"}
          </span>
        </span>

        <span
          aria-hidden
          data-cluster-toggle=""
          style={{
            flexShrink: 0,
            fontFamily: MONO_FONT_STACK,
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: ".04em",
            textTransform: "uppercase",
            padding: "6px 12px",
            minHeight: 32,
            display: "inline-flex",
            alignItems: "center",
            borderRadius: 9999,
            border: `1px solid ${TOKENS.primary}`,
            background: selected ? TOKENS.primary : "transparent",
            color: selected ? TOKENS.bg : TOKENS.primary,
          }}
        >
          {toggleLabel}
        </span>
      </span>

      {/* Member-avatar stack — a glimpse of who's in the cluster. */}
      <span data-cluster-avatar-stack="" style={{ display: "flex", alignItems: "center" }}>
        {visibleMembers.map((member, index) => (
          <span
            key={`${member.kind}:${member.followable_id}`}
            style={{ marginLeft: index === 0 ? 0 : -10, borderRadius: "50%", background: TOKENS.bg }}
          >
            <SourceArtwork
              source_name={member.display_name}
              image_url={null}
              kind={member.kind === "personality" ? "personality" : "youtube_channel"}
              size={AVATAR_SIZE_PX}
            />
          </span>
        ))}
        {overflow > 0 ? (
          <span
            data-cluster-avatar-overflow=""
            style={{
              marginLeft: -10,
              width: AVATAR_SIZE_PX,
              height: AVATAR_SIZE_PX,
              borderRadius: "50%",
              border: `1px solid ${TOKENS.border}`,
              background: TOKENS.bg,
              color: TOKENS.textSecondary,
              fontFamily: MONO_FONT_STACK,
              fontSize: 11,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            +{overflow}
          </span>
        ) : null}
      </span>
    </button>
  );
}
