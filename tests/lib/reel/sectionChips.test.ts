import { beforeEach, describe, expect, it, vi } from "vitest";
import { logger } from "@/lib/logger";
import { computeSectionChips } from "@/lib/reel/sectionChips";
import type { Story } from "@/types/feed";

/**
 * FSR slice #8 — section-chip derivation from `daily_feeds` row metadata.
 *
 * WHY these tests matter (Rule 9): the owner's honesty decision is that section
 * headers come from the user's OWN vocabulary stamped on the row by assembly, and
 * that a climbed (fallback) slot SAYS SO. A implementation that quietly rendered
 * the category label ("Sport") for a niche slot, or dropped the fallback line,
 * would pass a smoke test but betray the product promise — these tests fail on
 * exactly that regression:
 *  - the happy-path fixture deliberately carries a DIFFERENT category label
 *    ("Sport") from its section label ("IPL"), so deriving the header from the
 *    category/slug client-side FAILS the assertion (acceptance criterion #1).
 *  - the climbed-slot test asserts the fallback line NAMES the level actually
 *    filled from (the matched interest's label) — silent substitution fails it.
 *  - the legacy-row test pins today's behavior (category label, no header
 *    invention, no crash).
 *  - the malformed-metadata test pins the safe fallback + the structured warning
 *    with `fix_suggestion` (acceptance criterion #6).
 */

vi.mock("@/lib/logger", () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

/** Build a minimal Story carrying only the fields section rendering reads. */
function makeStory(overrides: Partial<Story>): Story {
  return {
    digest_id: "story-1",
    headline: "A headline",
    segment_key: "sport",
    story_detail_category: "sport",
    segment_label: "Sport",
    segment_accent_hex: "#F59E0B",
    anchors: ["ALEX", "JORDAN"],
    digest_audio_url: "https://cdn/audio.mp3",
    audio_duration_ms: 1000,
    speech_end_ms: 1000,
    poster_url: "",
    caption_sentences: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("computeSectionChips (FSR slice #8 row-metadata section headers)", () => {
  it("renders a niche section header from the user's own label + slot count, NOT the category", () => {
    // 2 IPL slots + 1 legacy slot: the IPL chips must read "IPL — 2" (label + count
    // from row metadata). The category label is "Sport" — if the implementation
    // derived the header client-side from the category/slug, this fails.
    const stories: Story[] = [
      makeStory({
        digest_id: "ipl-1",
        feed_slot_kind: "interest",
        feed_section_label: "IPL",
        feed_section_interest_id: "int-ipl",
        feed_matched_interest_id: "int-ipl",
        feed_fallback_source_level: 0,
      }),
      makeStory({
        digest_id: "ipl-2",
        feed_slot_kind: "interest",
        feed_section_label: "IPL",
        feed_section_interest_id: "int-ipl",
        feed_matched_interest_id: "int-ipl",
        feed_fallback_source_level: 0,
      }),
      makeStory({ digest_id: "legacy-1" }),
    ];

    const chips = computeSectionChips(stories);

    expect(chips[0].chip_label).toBe("IPL — 2");
    expect(chips[1].chip_label).toBe("IPL — 2");
    expect(chips[0].chip_label).not.toContain("Sport");
    // Direct fills carry NO fallback line (acceptance criterion #2, second half).
    expect(chips[0].fallback_label).toBeNull();
    expect(chips[1].fallback_label).toBeNull();
  });

  it("labels a climbed slot honestly, naming the level actually filled from", () => {
    const stories: Story[] = [
      makeStory({
        digest_id: "ipl-climbed",
        feed_slot_kind: "interest",
        feed_section_label: "IPL",
        feed_section_interest_id: "int-ipl",
        feed_matched_interest_id: "int-cricket",
        feed_matched_interest_label: "Cricket",
        feed_fallback_source_level: 1,
      }),
    ];

    const chips = computeSectionChips(stories);

    expect(chips[0].chip_label).toBe("IPL — 1");
    expect(chips[0].fallback_label).toBe("Nothing new in IPL today — here's Cricket");
  });

  it("falls back to the category label AND warns when a climbed slot's matched-interest label is missing", () => {
    // The matched interest node was pruned (FK on delete set null) or the embed
    // failed — the honesty line still renders, naming the broadest truthful level
    // (the category), never silently dropping the substitution notice; the
    // degraded naming is surfaced as a structured warning (review-panel finding).
    const stories: Story[] = [
      makeStory({
        digest_id: "ipl-climbed-unnamed",
        feed_section_label: "IPL",
        feed_matched_interest_id: null,
        feed_matched_interest_label: null,
        feed_fallback_source_level: 2,
      }),
    ];

    const chips = computeSectionChips(stories);

    expect(chips[0].fallback_label).toBe("Nothing new in IPL today — here's Sport");
    expect(logger.warn).toHaveBeenCalledWith(
      "reel_section_fallback_source_unnamed",
      expect.objectContaining({
        story_id: "ipl-climbed-unnamed",
        fix_suggestion: expect.stringContaining("matched-interest"),
      }),
    );
  });

  it("counts two sections that share a display label separately (keyed by section interest id)", () => {
    // interests.interest_label is NOT unique — two niches under different roots can
    // both read "Playoffs". Their chips must each count their OWN slots, not merge.
    const stories: Story[] = [
      makeStory({ digest_id: "nba-1", feed_section_label: "Playoffs", feed_section_interest_id: "int-nba-po" }),
      makeStory({ digest_id: "nba-2", feed_section_label: "Playoffs", feed_section_interest_id: "int-nba-po" }),
      makeStory({ digest_id: "ipl-po-1", feed_section_label: "Playoffs", feed_section_interest_id: "int-ipl-po" }),
    ];

    const chips = computeSectionChips(stories);

    expect(chips[0].chip_label).toBe("Playoffs — 2");
    expect(chips[1].chip_label).toBe("Playoffs — 2");
    expect(chips[2].chip_label).toBe("Playoffs — 1");
  });

  it("renders a legacy row (no section metadata) with the category label exactly as today", () => {
    const stories: Story[] = [makeStory({ digest_id: "legacy-1" })];

    const chips = computeSectionChips(stories);

    expect(chips[0].chip_label).toBe("Sport");
    expect(chips[0].fallback_label).toBeNull();
    expect(logger.warn).not.toHaveBeenCalled();
  });

  it("treats an empty/whitespace section label as absent (never an empty header)", () => {
    const stories: Story[] = [makeStory({ digest_id: "blank-label", feed_section_label: "   " })];

    const chips = computeSectionChips(stories);

    expect(chips[0].chip_label).toBe("Sport");
  });

  it("renders malformed metadata (fallback level but no section label) with the category fallback and a structured warning", () => {
    const stories: Story[] = [
      makeStory({
        digest_id: "malformed-1",
        feed_section_label: null,
        feed_fallback_source_level: 1,
      }),
    ];

    const chips = computeSectionChips(stories);

    expect(chips[0].chip_label).toBe("Sport");
    expect(chips[0].fallback_label).toBeNull();
    expect(logger.warn).toHaveBeenCalledWith(
      "reel_section_metadata_malformed",
      expect.objectContaining({
        story_id: "malformed-1",
        feed_fallback_source_level: 1,
        fix_suggestion: expect.stringContaining("feed_section_label"),
      }),
    );
  });

  it("keeps a followed-source slot on its existing category treatment (no section header)", () => {
    const stories: Story[] = [
      makeStory({
        digest_id: "source-1",
        feed_slot_kind: "source",
        // Defensive: even a stray label on a source row must not change the
        // source lead-slot treatment (acceptance criterion #5).
        feed_section_label: "Stray",
      }),
    ];

    const chips = computeSectionChips(stories);

    expect(chips[0].chip_label).toBe("Sport");
    expect(chips[0].fallback_label).toBeNull();
  });

  it("renders the beyond-bubble section under its reserved label with its slot count", () => {
    const stories: Story[] = [
      makeStory({ digest_id: "bb-1", feed_section_label: "Beyond your bubble", feed_fallback_source_level: 0 }),
      makeStory({ digest_id: "bb-2", feed_section_label: "Beyond your bubble", feed_fallback_source_level: 0 }),
      makeStory({ digest_id: "bb-3", feed_section_label: "Beyond your bubble", feed_fallback_source_level: 0 }),
    ];

    const chips = computeSectionChips(stories);

    expect(chips[0].chip_label).toBe("Beyond your bubble — 3");
    expect(chips[0].fallback_label).toBeNull();
  });

  it("never re-writes the user's words — a long label is passed through verbatim", () => {
    const longLabel = "Formula 1 silly season — every driver-market rumor, all of it";
    const stories: Story[] = [makeStory({ digest_id: "long-1", feed_section_label: longLabel })];

    const chips = computeSectionChips(stories);

    expect(chips[0].chip_label).toBe(`${longLabel} — 1`);
  });
});
