"use client";

/**
 * ReelChrome — the overlay chrome that frames one reel story (everything except
 * the karaoke caption + ambient wash): the finite progress bar, the date +
 * `blip` wordmark + counter + profile button (top), the segment chip + headline
 * (lower-left), the speaker label, the per-story progress bar, and the action
 * row (bottom).
 *
 * Ports the prototype `mountReel` chrome + `renderFiniteBar` / `renderStory` /
 * `renderActions` markup to React, reusing the ported CSS classes verbatim
 * (`.finite-seg`, `.seg-chip`, `.seg-dot`, `.story-progress`, `.act-btn`,
 * `.act-label`) so the visual contract is unchanged.
 *
 * **Action-row scope (M1).** Save + Follow are real but LOCAL/in-memory only —
 * their on/off state is owned by `Reel.tsx` (lifted so it survives scroll-away)
 * and toggled through the callbacks here. Ask / Voice / Detail RENDER but are
 * deferred no-ops (their navigation targets are M2/M3); each is wired to a logged
 * no-op. All hit targets are ≥ 44px (`.act-btn` is 44×44).
 */
import type { CSSProperties } from "react";
import { BlipLogo } from "@/components/BlipLogo";
import { logger } from "@/lib/logger";
import type { AnchorSpeaker, Story } from "@/types/feed";

// --- Finite-bar provenance (data.js) -----------------------------------------
// Reason: the prototype's `data.js` places the 5 detailed digests at the END of
// a 30-story briefing so the "all caught up" finish line is reachable in-demo —
// the counter reads 26/30 … 30/30. Ported here as named constants where the
// FiniteBar lives (the only consumer). FEED_TOTAL = total stories in the day's
// briefing; FEED_START_INDEX = 0-based feed position of the FIRST fixture story.
export const FEED_TOTAL = 30;
export const FEED_START_INDEX = 25;

/** Fixed anchor identity colours (NOT segment accents) — port-map §2 / app.js. */
const SPEAKER_IDENTITY_COLOR: Record<AnchorSpeaker, string> = {
  ALEX: "#6C8CFF",
  JORDAN: "#C792EA",
};

/** Format the finite counter, e.g. `feedPosition 25` → `"26 / 30"`. */
function formatCounter(feedPosition: number): string {
  return `${String(feedPosition + 1).padStart(2, "0")} / ${FEED_TOTAL}`;
}

/** Props for the finite segmented bar (top progress through the whole briefing). */
interface FiniteBarProps {
  /** 0-based position of THIS story within the full `FEED_TOTAL`-long briefing. */
  feedPosition: number;
}

/**
 * The segmented top bar showing progress through the finite briefing: segments
 * before `feedPosition` are `done`, the one at `feedPosition` is `current`
 * (accent-coloured via `var(--accent)`), the rest are upcoming.
 */
function FiniteBar({ feedPosition }: FiniteBarProps) {
  return (
    <div className="mb-3 flex gap-[3px]">
      {Array.from({ length: FEED_TOTAL }, (_unused, segmentIndex) => {
        let stateClass = "";
        if (segmentIndex < feedPosition) {
          stateClass = " done";
        } else if (segmentIndex === feedPosition) {
          stateClass = " current";
        }
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: fixed-length positional bar; segment index IS the identity.
          <div key={segmentIndex} className={`finite-seg${stateClass}`} />
        );
      })}
    </div>
  );
}

/** Props for one action-row button. */
interface ActionButtonProps {
  /** Accessible label + visible mono caption. */
  label: string;
  /** Inline SVG icon glyph. */
  icon: React.ReactNode;
  /** Active/"on" visual state (Save = yellow `.on`, Follow = accent `.follow-on`). */
  isOn?: boolean;
  /** Extra `.act-btn` modifier classes (`"primary"`, `"follow"`). */
  variantClass?: string;
  /** "on" modifier class to apply when `isOn` (`"on"` or `"follow-on"`). */
  onClass?: string;
  /** Tap handler. */
  onPress: () => void;
}

/** One labelled 44×44 action button (Save / Share / Follow / Ask / Voice). */
function ActionButton({ label, icon, isOn = false, variantClass = "", onClass = "on", onPress }: ActionButtonProps) {
  const activeClass = isOn ? ` ${onClass}` : "";
  return (
    <button type="button" className="press flex flex-col items-center gap-1.5" onClick={onPress} aria-pressed={isOn}>
      <span className={`act-btn ${variantClass}${activeClass}`.trim()}>{icon}</span>
      <span className="act-label">{label}</span>
    </button>
  );
}

export interface ReelChromeProps {
  /** The story this chrome frames. */
  story: Story;
  /** This story's 0-based index in the loaded feed (0 → counter 26/30). */
  storyIndex: number;
  /** The current speaker for the active sentence, or `null` before the first word. */
  currentSpeaker: AnchorSpeaker | null;
  /** Sampled audio position in ms — drives the per-story progress bar fill. */
  currentTimeMs: number;
  /** Whether this story is saved (lifted state in `Reel.tsx`). */
  isSaved: boolean;
  /** Whether this story is followed (lifted state in `Reel.tsx`). */
  isFollowed: boolean;
  /** Toggle saved for this story. */
  onToggleSave: () => void;
  /** Toggle followed for this story. */
  onToggleFollow: () => void;
}

/**
 * Render the full chrome overlay for one story. Reads `var(--accent)` (set on
 * the `ReelStory` root) for the finite bar's current segment, the seg dot, and
 * the Follow-on tint; uses the static `text-seg-*` token only for the chip text.
 */
export function ReelChrome({
  story,
  storyIndex,
  currentSpeaker,
  currentTimeMs,
  isSaved,
  isFollowed,
  onToggleSave,
  onToggleFollow,
}: ReelChromeProps) {
  const feedPosition = FEED_START_INDEX + storyIndex; // 0-based position in the briefing
  const progressPercent =
    story.audio_duration_ms > 0 ? Math.min(100, Math.max(0, (currentTimeMs / story.audio_duration_ms) * 100)) : 0;

  // Reason: the chip + headline read the per-story accent via inline style (the
  // dynamic cascade), exactly as the prototype's setAccent-driven `seg-chip`.
  const accentStyle = { color: story.segment_accent_hex } as CSSProperties;

  const speakerColor = currentSpeaker ? SPEAKER_IDENTITY_COLOR[currentSpeaker] : "#9aa3b2";

  /** deferred: M2/M3 — Ask/Voice/Detail navigation targets land in later milestones. */
  const handleDeferredAction = (actionName: string): void => {
    logger.info("reel_action_deferred", { action_name: actionName, story_id: story.digest_id });
  };

  return (
    // pointer-events-none on the frame so taps fall through to the play/pause
    // surface; interactive controls re-enable pointer-events on themselves.
    <div className="pointer-events-none absolute inset-0 z-10 flex flex-col">
      {/* ---- top chrome: finite bar + wordmark/date + counter + profile ---- */}
      <div className="px-5 pt-safe-t">
        <FiniteBar feedPosition={feedPosition} />
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-baseline gap-2">
            <BlipLogo size={20} />
            <span className="whitespace-nowrap font-mono text-[9.5px] tracking-[0.08em] text-white/45">
              THU · MAY 29
            </span>
          </div>
          <div className="flex flex-none items-center gap-2.5">
            <span className="whitespace-nowrap font-mono text-[12px] font-semibold text-white/85">
              {formatCounter(feedPosition)}
            </span>
            <button
              type="button"
              className="act-btn pointer-events-auto h-9 w-9 rounded-pill"
              aria-label="Profile"
              onClick={() => handleDeferredAction("profile")}
            >
              <ProfileIcon />
            </button>
          </div>
        </div>
      </div>

      {/* spacer — the caption hero sits behind this, centered by ReelStory */}
      <div className="flex-1" />

      {/* ---- headline zone (anchored low, left-aligned) ---- */}
      <div className="px-5">
        <div className="seg-chip mb-2.5" style={accentStyle}>
          <span className="seg-dot" />
          {story.segment_label}
        </div>
        <h1
          className="max-w-[330px] font-sans text-[25px] font-semibold leading-[1.12] tracking-[-0.02em] text-white"
          style={{ textShadow: "0 1px 18px rgba(2,6,23,.9)" }}
        >
          {story.headline}
        </h1>
      </div>

      {/* ---- speaker label + per-story progress ---- */}
      <div className="px-5 pt-4">
        <div className="mb-2 flex items-center">
          <span className="seg-chip text-white/80">
            <span className="seg-dot" style={{ background: speakerColor, boxShadow: `0 0 12px ${speakerColor}` }} />
            {currentSpeaker ?? " "}
          </span>
        </div>
        <div className="story-progress">
          <i style={{ width: `${progressPercent}%` }} />
        </div>
      </div>

      {/* ---- action row ---- */}
      <div className="pointer-events-auto flex items-end justify-between px-5 pt-4 pb-2 pb-safe-b">
        <ActionButton label="Save" icon={<SaveIcon />} isOn={isSaved} onClass="on" onPress={onToggleSave} />
        <ActionButton label="Share" icon={<ShareIcon />} onPress={() => handleDeferredAction("share")} />
        <ActionButton
          label="Follow"
          icon={isFollowed ? <FollowingIcon /> : <FollowIcon />}
          isOn={isFollowed}
          variantClass="follow"
          onClass="follow-on"
          onPress={onToggleFollow}
        />
        <ActionButton
          label="Ask"
          icon={<AskIcon />}
          variantClass="primary"
          onPress={() => handleDeferredAction("ask")}
        />
        <ActionButton label="Voice" icon={<VoiceIcon />} onPress={() => handleDeferredAction("voice")} />
      </div>
    </div>
  );
}

// --- icons (ported from the prototype `#i-*` symbol set, inlined as 20px SVGs) ---
// Reason: the prototype uses an SVG <symbol> sprite; inlining the few glyphs the
// reel chrome needs keeps this self-contained for the static export.

function IconBase({ children, size = 20 }: { children: React.ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

function ProfileIcon() {
  return (
    <IconBase size={18}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-3.5 3.5-6 8-6s8 2.5 8 6" />
    </IconBase>
  );
}

function SaveIcon() {
  return (
    <IconBase>
      <path d="M6 4h12v16l-6-4-6 4V4Z" />
    </IconBase>
  );
}

function ShareIcon() {
  return (
    <IconBase>
      <path d="M12 16V4" />
      <path d="M8 8l4-4 4 4" />
      <path d="M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" />
    </IconBase>
  );
}

function FollowIcon() {
  return (
    <IconBase>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </IconBase>
  );
}

function FollowingIcon() {
  return (
    <IconBase>
      <path d="M20 6 9 17l-5-5" />
    </IconBase>
  );
}

function AskIcon() {
  return (
    <IconBase>
      <path d="M21 11.5a8.5 8.5 0 0 1-12.3 7.6L3 21l1.9-5.7A8.5 8.5 0 1 1 21 11.5Z" />
    </IconBase>
  );
}

function VoiceIcon() {
  return (
    <IconBase>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3" />
    </IconBase>
  );
}
