"use client";

/**
 * BuildYour30 — the Blip Flow Stage 3 "Build your 30, in order" screen (the feed-allocation
 * step that runs AFTER the source swipe, BEFORE the reel). The user stacks category + source
 * "blocks" to fill a 30-story briefing: each block has a slot count (steppers), can be
 * reordered (▲/▼), removed (×), and new blocks are added via a bottom sheet. A 30-cell
 * "spine" previews the fill; a budget footer enforces EXACTLY 30; "Save this order →" is
 * disabled until the budget is full — pixel-sourced from the prototype's `blip-sequence.js`
 * + `blip-flow.css`.
 *
 * It seeds from the user's saved allocation ({@link getUserFeedAllocation}) when one exists,
 * else the prototype's default segments. On save it persists ({@link saveUserFeedAllocation},
 * RLS-scoped to the authed user) then hands the ordered segments to {@link onDone}.
 *
 * Static-export safe: client-only (`"use client"`), no `window`/server APIs at module scope;
 * the saved-allocation seed runs in an effect (browser-only). Styling comes from the verbatim
 * `src/styles/blip-flow.css` (imported here) — the only inline styles are the per-bucket
 * accent colors the prototype sets imperatively (`spine i`, `.sdot`).
 */

import "@/styles/blip-flow.css";
import { type CSSProperties, useCallback, useEffect, useRef, useState } from "react";
import { BlipIconDefs } from "@/components/blip/BlipIconDefs";
import { assembleFirstRunFeed, markFirstRunFeed, todayUtcFeedDate } from "@/lib/feed/assembleFirstRunFeed";
import { getUserFeedAllocation, saveUserFeedAllocation } from "@/lib/feedAllocation";
import {
  ALLOCATION_TOTAL,
  type AllocationSegment,
  buildDefaultSegments,
  buildSegmentsForSelectedCategories,
  DESIGN_BUCKET_IDS,
  DESIGN_BUCKETS,
  type DesignBucket,
  type DesignBucketId,
  sumSegmentCounts,
} from "@/lib/feedBuckets";
import { logger } from "@/lib/logger";

/** What a completed "Build your 30" hands back — the ordered segments (mirrors the prototype's `segs`). */
export interface BuildYour30Segment {
  /** The design bucket id this segment allocates slots to. */
  bucketId: DesignBucketId;
  /** How many of the 30 slots this bucket claims. */
  count: number;
}

export interface BuildYour30Props {
  /**
   * Called when the user taps "Save this order →" (AFTER the allocation is persisted).
   * Receives the ordered, exactly-30 segments. The onboarding flow routes to the reel.
   */
  onDone: (segments: BuildYour30Segment[]) => void;
  /**
   * Optional skip / "do this later" handler. When provided, a skip control is rendered;
   * tapping it routes onward WITHOUT saving (the Python allocator has a balanced default
   * for users with no allocation — phase-5a). Omit to hide the skip control.
   */
  onSkip?: () => void;
  /**
   * The CATEGORY buckets the user selected in the interest picker (derived via
   * {@link categoryBucketsFromFollows}). When provided and NON-EMPTY, the screen seeds
   * only those category blocks (+ always-on "breaking" + the source blocks) instead of all
   * 8 — so categories the user skipped no longer appear. Empty/undefined (picker skipped, or
   * a returning user re-onboarding) falls back to the full default seed, never an empty one.
   * A saved allocation, when present, still takes precedence over this (returning users).
   */
  selectedCategoryBuckets?: DesignBucketId[];
  /**
   * Render filling the parent container instead of the full viewport. Used by the library
   * "Thirty" tab ({@link AppShell}), which mounts this inside a `flex:1` surface above the
   * tab bar — so the scene must size to that box, not `100dvh` (which would overflow the bar).
   * Default `false` keeps the onboarding full-bleed behavior.
   */
  embedded?: boolean;
}

/** The full-bleed scene surface giving `.a-scroll` (position:absolute; inset:0) its sizing context. */
const SCENE_SURFACE_STYLE: CSSProperties = {
  position: "relative",
  minHeight: "100dvh",
  width: "100%",
  background: "#020617",
  color: "#fff",
  overflow: "hidden",
};

/**
 * Embedded scene surface (the "Thirty" library tab): fills its positioned parent (`inset:0`)
 * rather than the viewport, so `.a-scroll` / `.a-foot` resolve inside the tab box and the
 * Save footer lands just above the bottom tab bar instead of overflowing past it.
 */
const EMBEDDED_SURFACE_STYLE: CSSProperties = {
  position: "absolute",
  inset: 0,
  background: "#020617",
  color: "#fff",
  overflow: "hidden",
};

/** The single accent used on this screen (the prototype's `#EF4444`) — set as the `--ac` CSS var. */
const ACCENT_RED = "#EF4444";

/** Inline-style a spine cell / dot for a SOURCE bucket (translucent fill + colored inset ring). */
function sourceSwatchStyle(bucket: DesignBucket): CSSProperties {
  return { background: "rgba(255,255,255,.16)", boxShadow: `inset 0 0 0 1.5px ${bucket.color}` };
}

/** Inline-style a spine cell / dot for a CATEGORY bucket (solid color fill). */
function categorySwatchStyle(bucket: DesignBucket): CSSProperties {
  return { background: bucket.color };
}

/** A source-glyph SVG (resolves a `BlipIconDefs` `<symbol>`), matching the prototype's `glyphSvg`. */
function GlyphSvg({ glyphId }: { glyphId: string }) {
  return (
    <svg className="glyph" viewBox="0 0 24 24" style={{ color: "#cbd5e1" }} aria-hidden="true">
      <use href={`#${glyphId}`} />
    </svg>
  );
}

/** Build the 30 spine cells: one filled cell per allocated slot (in order), then empty cells. */
function SpineCells({ segments }: { segments: AllocationSegment[] }) {
  const cells: CSSProperties[] = [];
  for (const segment of segments) {
    const bucket = DESIGN_BUCKETS[segment.bucketId];
    const swatch = bucket.kind === "src" ? sourceSwatchStyle(bucket) : categorySwatchStyle(bucket);
    for (let slotIndex = 0; slotIndex < segment.count; slotIndex++) {
      cells.push(swatch);
    }
  }
  for (let emptyIndex = sumSegmentCounts(segments); emptyIndex < ALLOCATION_TOTAL; emptyIndex++) {
    cells.push({ background: "rgba(255,255,255,.06)" });
  }
  return (
    <div className="spine" id="spine">
      {cells.map((cellStyle, cellIndex) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: spine cells are positional + identityless.
        <i key={cellIndex} style={cellStyle} />
      ))}
    </div>
  );
}

/**
 * Render the Stage 3 "Build your 30, in order" allocation screen.
 *
 * @param props - {@link BuildYour30Props}.
 *
 * @example
 * <BuildYour30 onDone={(segments) => router.push("/")} onSkip={() => router.push("/")} />
 */
export function BuildYour30({ onDone, onSkip, selectedCategoryBuckets, embedded = false }: BuildYour30Props) {
  // The ordered allocation segments (the prototype's `segs`). Seeded from the user's picked
  // categories when they made any selection (filtered seed), else the full default; the
  // effect below replaces it with the user's saved allocation when one exists.
  const [segments, setSegments] = useState<AllocationSegment[]>(() =>
    selectedCategoryBuckets && selectedCategoryBuckets.length > 0
      ? buildSegmentsForSelectedCategories(selectedCategoryBuckets)
      : buildDefaultSegments(),
  );
  // Whether the Add-block bottom sheet is open.
  const [isSheetOpen, setIsSheetOpen] = useState(false);
  // True while persisting on save — disables the CTA so a double-tap can't double-write.
  const [isSaving, setIsSaving] = useState(false);
  // Guard so the saved-allocation seed runs at most once (and never clobbers a live edit).
  const hasSeededRef = useRef(false);

  // Seed from the user's saved allocation on mount (browser-only; static-export safe). A read
  // failure (e.g. signed out) is non-fatal — we keep the default segments and log it (Rule 12).
  useEffect(() => {
    if (hasSeededRef.current) {
      return;
    }
    hasSeededRef.current = true;
    let isMounted = true;
    void (async () => {
      try {
        const saved = await getUserFeedAllocation();
        // Only adopt a saved allocation that actually totals 30 — a partial/legacy row set
        // (e.g. a pre-0010 save that dropped podcasts) would seed a non-30 budget the user
        // could not save. Falling back to the default keeps the screen immediately savable.
        if (isMounted && saved.length > 0 && sumSegmentCounts(saved) === ALLOCATION_TOTAL) {
          setSegments(saved);
          logger.info("build_your_30_seeded_from_saved", { segment_count: saved.length });
        } else if (saved.length > 0) {
          logger.info("build_your_30_saved_ignored_non_30", {
            segment_count: saved.length,
            total_slots: sumSegmentCounts(saved),
          });
        }
      } catch (error) {
        // Signed-out / read error → keep the default seed (the screen is still fully usable).
        logger.warn("build_your_30_seed_read_failed", {
          error_message: error instanceof Error ? error.message : "unknown",
          fix_suggestion: "Saved allocation could not be read; using the default 30 — harmless, user can re-edit.",
        });
      }
    })();
    return () => {
      isMounted = false;
    };
  }, []);

  const allocatedTotal = sumSegmentCounts(segments);
  const slotsLeft = ALLOCATION_TOTAL - allocatedTotal;
  const isBudgetFull = allocatedTotal === ALLOCATION_TOTAL;

  // ── Mutators (port of the prototype's stepper / reorder / add / remove handlers) ──────

  /** Decrement a segment's count; at count 1 a further decrement REMOVES the block (prototype). */
  const decrementSegment = useCallback((segmentIndex: number) => {
    setSegments((previous) => {
      const next = previous.map((segment) => ({ ...segment }));
      if (next[segmentIndex].count > 1) {
        next[segmentIndex].count -= 1;
        return next;
      }
      next.splice(segmentIndex, 1);
      return next;
    });
  }, []);

  /** Increment a segment's count, but never past the 30-slot budget (prototype `sum() < TOTAL`). */
  const incrementSegment = useCallback((segmentIndex: number) => {
    setSegments((previous) => {
      if (sumSegmentCounts(previous) >= ALLOCATION_TOTAL) {
        return previous;
      }
      const next = previous.map((segment) => ({ ...segment }));
      next[segmentIndex].count += 1;
      return next;
    });
  }, []);

  /** Swap a segment with its neighbor one step up (▲) — no-op at the top. */
  const moveSegmentUp = useCallback((segmentIndex: number) => {
    setSegments((previous) => {
      if (segmentIndex <= 0) {
        return previous;
      }
      const next = previous.slice();
      [next[segmentIndex - 1], next[segmentIndex]] = [next[segmentIndex], next[segmentIndex - 1]];
      return next;
    });
  }, []);

  /** Swap a segment with its neighbor one step down (▼) — no-op at the bottom. */
  const moveSegmentDown = useCallback((segmentIndex: number) => {
    setSegments((previous) => {
      if (segmentIndex >= previous.length - 1) {
        return previous;
      }
      const next = previous.slice();
      [next[segmentIndex + 1], next[segmentIndex]] = [next[segmentIndex], next[segmentIndex + 1]];
      return next;
    });
  }, []);

  /** Remove a segment outright (the × control). */
  const removeSegment = useCallback((segmentIndex: number) => {
    setSegments((previous) => previous.filter((_, index) => index !== segmentIndex));
  }, []);

  /** Add a bucket from the sheet: append it with min(2, remaining) slots (prototype), then close. */
  const addBucket = useCallback((bucketId: DesignBucketId) => {
    setSegments((previous) => {
      if (previous.some((segment) => segment.bucketId === bucketId)) {
        return previous; // Already in the list (the sheet chip is `used`).
      }
      const remaining = ALLOCATION_TOTAL - sumSegmentCounts(previous);
      const initialCount = Math.min(2, remaining) || 1;
      logger.info("build_your_30_block_added", { bucket_id: bucketId, initial_count: initialCount });
      return [...previous, { bucketId, count: initialCount }];
    });
    setIsSheetOpen(false);
  }, []);

  // Add-block is disabled when the budget is full OR every bucket is already in the list.
  const isAddDisabled = allocatedTotal >= ALLOCATION_TOTAL || segments.length >= DESIGN_BUCKET_IDS.length;

  /** Save: persist the allocation (RLS-scoped) then hand the ordered segments to onDone. */
  const handleSave = useCallback(async () => {
    if (!isBudgetFull || isSaving) {
      return;
    }
    setIsSaving(true);
    logger.info("build_your_30_save_started", { segment_count: segments.length });
    try {
      const result = await saveUserFeedAllocation(segments);
      logger.info("build_your_30_save_completed", {
        persisted_count: result.persisted_count,
        deferred_count: result.deferred_buckets.length,
      });
      // First-run feed assembly: build the just-onboarded user's feed from the
      // existing catalog so they land on a populated reel (Phase 7b SP2). This is
      // NON-FATAL — a worker outage must not block finishing onboarding, so any
      // failure is swallowed and we still route to the reel (global-feed fallback).
      // The per-date first-run flag is persisted ONLY on success (SP3 reads it).
      const feedDate = todayUtcFeedDate();
      try {
        const assembled = await assembleFirstRunFeed(feedDate);
        markFirstRunFeed(feedDate);
        logger.info("build_your_30_first_run_assembled", { allocated_count: assembled.allocated_count });
      } catch (assembleError) {
        logger.warn("build_your_30_first_run_assemble_failed", {
          error_message: assembleError instanceof Error ? assembleError.message : "unknown",
          fix_suggestion: "Non-fatal; routing to the global feed. Confirm the worker /feed/assemble-mine is reachable.",
        });
      }
      onDone(segments.map((segment) => ({ bucketId: segment.bucketId, count: segment.count })));
    } catch (error) {
      // A persist failure must not silently swallow the user's work (Rule 12). Re-enable the
      // CTA so they can retry; the error is logged with an actionable fix.
      logger.error("build_your_30_save_failed", {
        error_message: error instanceof Error ? error.message : "unknown",
        fix_suggestion:
          "Retry; if it persists confirm migration 0008 applied and user_feed_allocation RLS permits the write.",
      });
      setIsSaving(false);
    }
  }, [isBudgetFull, isSaving, segments, onDone]);

  // The CTA label mirrors the prototype: full → save; under → "Fill N more"; over → "Remove N".
  const ctaLabel = isBudgetFull
    ? "Save this order →"
    : slotsLeft > 0
      ? `Fill ${slotsLeft} more`
      : `Remove ${Math.abs(slotsLeft)}`;

  // The budget label mirrors the prototype: "N/30 · X left | X over | full".
  const budgetTail = slotsLeft > 0 ? `${slotsLeft} left` : slotsLeft < 0 ? `${Math.abs(slotsLeft)} over` : "full";

  return (
    <div style={embedded ? EMBEDDED_SURFACE_STYLE : SCENE_SURFACE_STYLE}>
      <div style={{ "--ac": ACCENT_RED } as CSSProperties}>
        <BlipIconDefs />

        <div className="a-scroll" data-screen-label="Allocation · Sequence">
          <div className="a-top">
            <span className="ey">Allocation · Sequence</span>
            <h1>Build your 30, in order.</h1>
            <p>Stack what fills your briefing top to bottom — the first block plays first.</p>
          </div>

          <SpineCells segments={segments} />
          <div className="spine-x">
            <span>STORY 1</span>
            <span>30</span>
          </div>

          {/* Only the block list scrolls; the intro + 30-cell spine above and the budget/save
              footer below stay pinned (so the spine is always visible while you allocate). */}
          <div className="a-blocks">
            <div className="seglist" id="seglist">
              {segments.map((segment, segmentIndex) => (
                <SegmentRow
                  key={segment.bucketId}
                  segment={segment}
                  segmentIndex={segmentIndex}
                  rangeStart={computeRangeStart(segments, segmentIndex)}
                  onDecrement={decrementSegment}
                  onIncrement={incrementSegment}
                  onMoveUp={moveSegmentUp}
                  onMoveDown={moveSegmentDown}
                  onRemove={removeSegment}
                />
              ))}
            </div>

            <button
              type="button"
              className="addseg"
              id="addSeg"
              disabled={isAddDisabled}
              onClick={() => setIsSheetOpen(true)}
            >
              ＋ Add a block
            </button>

            {onSkip ? (
              <button type="button" className="exp" style={SKIP_BUTTON_STYLE} onClick={onSkip}>
                I&apos;ll do this later
              </button>
            ) : null}
          </div>
        </div>

        <div className="a-foot">
          <div className="budget">
            <div className="bar">
              <i style={{ width: `${Math.min(100, (allocatedTotal / ALLOCATION_TOTAL) * 100)}%` }} />
            </div>
            <span className={`lbl${slotsLeft !== 0 ? " over" : ""}`} id="blbl">
              <b>{allocatedTotal}</b>/30 · {budgetTail}
            </span>
          </div>
          <button
            type="button"
            className="a-cta"
            id="cta"
            disabled={!isBudgetFull || isSaving}
            onClick={() => void handleSave()}
          >
            {isSaving ? "Saving…" : ctaLabel}
          </button>
        </div>

        <button
          type="button"
          className={`scrim${isSheetOpen ? " on" : ""}`}
          id="scrim"
          aria-label="Close add-block sheet"
          tabIndex={isSheetOpen ? 0 : -1}
          onClick={() => setIsSheetOpen(false)}
        />
        <div className={`sheet2${isSheetOpen ? " on" : ""}`} id="sheet">
          <div className="grab" />
          <h3>Add to your 30</h3>
          <div className="grp">News categories</div>
          <div className="bk-grid" id="catGrid">
            {DESIGN_BUCKET_IDS.filter((bucketId) => DESIGN_BUCKETS[bucketId].kind === "cat").map((bucketId) => (
              <AddChip key={bucketId} bucketId={bucketId} segments={segments} onAdd={addBucket} />
            ))}
          </div>
          <div className="grp">From your sources</div>
          <div className="bk-grid" id="srcGrid">
            {DESIGN_BUCKET_IDS.filter((bucketId) => DESIGN_BUCKETS[bucketId].kind === "src").map((bucketId) => (
              <AddChip key={bucketId} bucketId={bucketId} segments={segments} onAdd={addBucket} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Skip control reuses the `.exp` ghost-button look, centered under the Add-block button. */
const SKIP_BUTTON_STYLE: CSSProperties = {
  display: "block",
  margin: "10px auto 0",
};

/** Compute a segment's 1-based starting story number (the cumulative sum before it). */
function computeRangeStart(segments: AllocationSegment[], segmentIndex: number): number {
  let cumulative = 0;
  for (let index = 0; index < segmentIndex; index++) {
    cumulative += segments[index].count;
  }
  return cumulative + 1;
}

/** Props for one segment row in the seglist. */
interface SegmentRowProps {
  segment: AllocationSegment;
  segmentIndex: number;
  rangeStart: number;
  onDecrement: (segmentIndex: number) => void;
  onIncrement: (segmentIndex: number) => void;
  onMoveUp: (segmentIndex: number) => void;
  onMoveDown: (segmentIndex: number) => void;
  onRemove: (segmentIndex: number) => void;
}

/** One allocation block row: range · dot · name · stepper · ▲▼ · × (port of the prototype `.seg`). */
function SegmentRow({
  segment,
  segmentIndex,
  rangeStart,
  onDecrement,
  onIncrement,
  onMoveUp,
  onMoveDown,
  onRemove,
}: SegmentRowProps) {
  const bucket = DESIGN_BUCKETS[segment.bucketId];
  const rangeEnd = rangeStart + segment.count - 1;
  const rangeText = segment.count === 1 ? String(rangeStart) : `${rangeStart}–${rangeEnd}`;
  const dotStyle = bucket.kind === "src" ? sourceSwatchStyle(bucket) : categorySwatchStyle(bucket);

  return (
    <div className="seg">
      <span className="rng">{rangeText}</span>
      <span className="sdot" style={dotStyle} />
      <span className="nm">
        {bucket.kind === "src" && bucket.glyph ? <GlyphSvg glyphId={bucket.glyph} /> : null}
        {bucket.name}
      </span>
      <div className="stepper">
        <button type="button" data-d="-1" onClick={() => onDecrement(segmentIndex)} aria-label={`Fewer ${bucket.name}`}>
          −
        </button>
        <span className="ct">{segment.count}</span>
        <button type="button" data-d="1" onClick={() => onIncrement(segmentIndex)} aria-label={`More ${bucket.name}`}>
          +
        </button>
      </div>
      <div className="reorder">
        <button type="button" data-u onClick={() => onMoveUp(segmentIndex)} aria-label={`Move ${bucket.name} up`}>
          ▲
        </button>
        <button type="button" data-dn onClick={() => onMoveDown(segmentIndex)} aria-label={`Move ${bucket.name} down`}>
          ▼
        </button>
      </div>
      <button type="button" className="rm" onClick={() => onRemove(segmentIndex)} aria-label={`Remove ${bucket.name}`}>
        ×
      </button>
    </div>
  );
}

/** Props for one Add-sheet chip. */
interface AddChipProps {
  bucketId: DesignBucketId;
  segments: AllocationSegment[];
  onAdd: (bucketId: DesignBucketId) => void;
}

/** One bottom-sheet chip (`.bk`, `.bk.used` when already in the list) — port of the prototype. */
function AddChip({ bucketId, segments, onAdd }: AddChipProps) {
  const bucket = DESIGN_BUCKETS[bucketId];
  const isUsed = segments.some((segment) => segment.bucketId === bucketId);
  return (
    <button type="button" className={`bk${isUsed ? " used" : ""}`} disabled={isUsed} onClick={() => onAdd(bucketId)}>
      {bucket.kind === "src" && bucket.glyph ? (
        <GlyphSvg glyphId={bucket.glyph} />
      ) : (
        <span className="sdot" style={{ background: bucket.color }} />
      )}
      {bucket.name}
    </button>
  );
}
