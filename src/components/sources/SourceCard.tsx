"use client";

/**
 * SourceCard — one selectable source row in the 5c recommendation grid (Phase 5c
 * SP2). It is a CONTROLLED component: it renders the avatar ({@link SourceArtwork})
 * + name + description + a follow toggle, reflects the caller's `selected` state via
 * `aria-pressed`, and calls `onToggle` on tap — it does NOT itself persist the
 * follow (that's SP3/SP4's `followSource`). The whole card is a real `<button>`, so
 * the entire row is one keyboard-focusable, screen-reader-labelled toggle.
 *
 * **Re-skin (CLAUDE.md / reuse-map §5):** ported in structure only from TL;DW's
 * `source-card.tsx` (the alt list-style selectable card). Its amber tokens are
 * dropped; every color/space/radius/font here is a News20 dark-editorial token from
 * `reference/design-language.md` — primary `#3B82F6` for the selected/follow state,
 * surface text `#FFFFFF`/`#A1A1AA`, JetBrains Mono for the toggle label, Playfair
 * for the description, `control` radius, 8px spacing base.
 */

import { type CSSProperties, useId } from "react";
import { SourceArtwork } from "@/components/sources/SourceArtwork";
import type { ContentSource } from "@/types/source";

/** Design-language.md tokens used by the card, inlined (no globals.css edit — SP2 scope). */
const TOKENS = {
  primary: "#3B82F6", // actions / "follow" / selected fill
  bg: "#020617", // near-black canvas
  textPrimary: "#FFFFFF",
  textSecondary: "#A1A1AA",
  border: "#D1D4BD", // sage line
} as const;

/** design-language.md typography faces. */
const DISPLAY_FONT_STACK = "Inter, system-ui, sans-serif";
const BODY_FONT_STACK = '"Playfair Display", Georgia, serif';
const MONO_FONT_STACK = '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace';

/** design-language.md spacing/shape: 8px base, control radius 16px. */
const CARD_GAP_PX = 16;
const CARD_PADDING_PX = 16;
const CARD_RADIUS_PX = 16;
const AVATAR_SIZE_PX = 56;

export interface SourceCardProps {
  /** The catalog row this card represents (avatar, name, description, kind). */
  source: ContentSource;
  /** Whether this source is currently followed/selected (drives fill + `aria-pressed`). */
  selected: boolean;
  /** Toggle handler — the parent flips selected state and (SP3/SP4) persists the follow. */
  onToggle: () => void;
}

/**
 * Render one controlled, selectable source card.
 *
 * @param props - {@link SourceCardProps}.
 *
 * @example
 * <SourceCard source={channel} selected={isFollowed} onToggle={() => toggleFollow(channel.source_id)} />
 */
export function SourceCard({ source, selected, onToggle }: SourceCardProps) {
  // Reason: pair the visible name with the toggle label via aria-labelledby so a
  // screen reader announces "<name>, Following/Follow, pressed/not pressed".
  const nameId = useId();
  const followLabel = selected ? "Following" : "Follow";

  const cardStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: CARD_GAP_PX,
    width: "100%",
    minHeight: 72, // ≥44px touch target with room for two text rows
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
      aria-labelledby={nameId}
      data-source-card={source.source_id}
      data-selected={selected ? "true" : "false"}
      style={cardStyle}
    >
      <SourceArtwork
        source_name={source.source_name}
        image_url={source.thumbnail_url}
        kind={source.content_source_type}
        size={AVATAR_SIZE_PX}
      />

      <span style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1 }}>
        <span
          id={nameId}
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
          {source.source_name}
        </span>
        {source.source_description ? (
          <span
            data-source-description=""
            style={{
              fontFamily: BODY_FONT_STACK,
              fontSize: 13,
              lineHeight: 1.4,
              color: TOKENS.textSecondary,
              // Clamp the blurb to two lines so cards stay uniform.
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {source.source_description}
          </span>
        ) : null}
      </span>

      {/* The follow toggle's visual affordance. The whole card IS the <button>, so
          this is a styled <span> (not a nested button — nested buttons are invalid
          HTML); the card's aria-pressed already conveys the toggle state. */}
      <span
        aria-hidden
        data-follow-toggle=""
        style={{
          flexShrink: 0,
          alignSelf: "center",
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
        {followLabel}
      </span>
    </button>
  );
}
