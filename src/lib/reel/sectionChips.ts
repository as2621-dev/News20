/**
 * Section-chip derivation for the reel (FSR slice #8 — the render half of the
 * honest fallback ladder).
 *
 * Turns the `daily_feeds` section metadata that assembly stamped on each row
 * (`feed_section_label` / `feed_fallback_source_level` / the matched-interest
 * label, see `src/types/feed.ts` `Story`) into what the reel chrome paints:
 * a section header in the user's OWN vocabulary with its slot count
 * (`"IPL — 4"`), and — on a climbed slot — the honesty line
 * (`"Nothing new in IPL today — here's Cricket"`).
 *
 * **Zero client-side inference (owner decision, PRD §Section rendering):** every
 * header comes from row metadata. This module only aggregates (slot counts per
 * section) and formats; it never derives a section name from a category or an
 * interest slug. Rows without metadata (legacy feeds, source slots) keep today's
 * category-label treatment untouched.
 *
 * Called ONCE per feed load (memoized in `BlipReel`), so the malformed-metadata
 * warning logs once per story, not once per render frame.
 */

import { logger } from "@/lib/logger";
import type { Story } from "@/types/feed";

/** What the reel chrome paints for one slot's section chip. */
export interface SectionChipModel {
  /**
   * The chip text: the user-vocabulary section header with its slot count
   * (`"IPL — 4"`, `"Beyond your bubble — 3"`), or the story's plain category
   * label (`"Sport"`) for legacy / source / metadata-less rows.
   */
  chip_label: string;
  /**
   * The honesty line for a climbed slot (`"Nothing new in IPL today — here's
   * Cricket"`), or `null` for a direct fill / legacy / source slot.
   */
  fallback_label: string | null;
}

/** A story's section label, trimmed; `null` when absent/blank (never an empty header). */
function sectionLabelOf(story: Story): string | null {
  const trimmed = story.feed_section_label?.trim();
  return trimmed ? trimmed : null;
}

/** True when this row keeps the followed-source lead-slot treatment (no section header). */
function isSourceSlot(story: Story): boolean {
  return story.feed_slot_kind === "source";
}

/**
 * Derive each story's {@link SectionChipModel} from its row metadata, in feed order.
 *
 * Slot counts are aggregated per section label across the loaded feed (counting
 * rows that share a label IS reading row metadata — no category/slug inference).
 * Malformed metadata (a fallback level with no section label) renders safely on
 * the category label and logs a structured warning with a `fix_suggestion`.
 *
 * @param stories - The loaded feed, in `feed_position` order.
 * @returns One chip model per story, index-aligned with `stories`.
 *
 * @example
 * const chips = computeSectionChips(stories);
 * chips[0]; // { chip_label: "IPL — 4", fallback_label: null }
 */
export function computeSectionChips(stories: Story[]): SectionChipModel[] {
  // One counting pass: slots per section label (source slots keep their existing
  // treatment, so a stray label on one must not inflate a section's count).
  const slotCountBySectionLabel = new Map<string, number>();
  for (const story of stories) {
    const sectionLabel = sectionLabelOf(story);
    if (sectionLabel !== null && !isSourceSlot(story)) {
      slotCountBySectionLabel.set(sectionLabel, (slotCountBySectionLabel.get(sectionLabel) ?? 0) + 1);
    }
  }

  return stories.map((story) => {
    const sectionLabel = sectionLabelOf(story);
    const fallbackLevel = story.feed_fallback_source_level ?? 0;

    if (isSourceSlot(story) || sectionLabel === null) {
      // Legacy / coarse / followed-source rows: today's category chip, unchanged.
      if (fallbackLevel > 0 && !isSourceSlot(story)) {
        logger.warn("reel_section_metadata_malformed", {
          story_id: story.digest_id,
          feed_fallback_source_level: fallbackLevel,
          fix_suggestion:
            "A daily_feeds row has feed_fallback_source_level > 0 but no feed_section_label; " +
            "rendering the category label. Check assemble_niche_feed's section stamping (slice #7).",
        });
      }
      return { chip_label: story.segment_label, fallback_label: null };
    }

    // The user's words, verbatim — formatting appends the slot count, never rewrites.
    const chipLabel = `${sectionLabel} — ${slotCountBySectionLabel.get(sectionLabel) ?? 1}`;
    const fallbackLabel =
      fallbackLevel > 0
        ? `Nothing new in ${sectionLabel} today — here's ${story.feed_matched_interest_label ?? story.segment_label}`
        : null;
    return { chip_label: chipLabel, fallback_label: fallbackLabel };
  });
}
