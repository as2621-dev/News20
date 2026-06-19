import { describe, expect, it } from "vitest";
import { getFeed } from "@/lib/feed/fixtureFeed";
import { DESIGN_BUCKETS } from "@/lib/feedBuckets";
import type { SegmentKey, Story } from "@/types/feed";

/**
 * Reel-chip ↔ onboarding-chip parity (Phase SP3 Sub-phase 3, Rule 9).
 *
 * WHY THIS MATTERS (not just WHAT it does): the whole point of SP3's taxonomy
 * unification is that a given picker root renders the **identical label + accent
 * color** in onboarding, "Build your 30", and the reel chip. The reel chip
 * (`ReelStage.tsx` / `ArticleLayer.tsx`) draws `story.segment_label` colored by
 * `story.segment_accent_hex`; the onboarding chip draws `DESIGN_BUCKETS[root]`'s
 * `name` + `color`. If the segment label/accent maps ever drift from
 * `DESIGN_BUCKETS` (e.g. a future edit reverts "Tech" back to "Tech & Science",
 * or re-folds `business`→`markets`), the reel chip and the onboarding chip would
 * silently disagree — exactly the bug SP3 exists to kill. This test fails the
 * moment that divergence appears.
 */
describe("reel chip == onboarding chip (SP3 taxonomy parity)", () => {
  it("renders a sport story's chip with the onboarding sport label + hex", async () => {
    const feed: Story[] = await getFeed();
    const sportStory = feed.find((story) => story.segment_key === "sport");
    expect(sportStory, "fixture feed must contain a sport story").toBeDefined();

    // The reel chip's label + color (what ReelStage/ArticleLayer paint) MUST equal
    // the onboarding `sport` chip — the DoD's explicit assertion.
    expect(sportStory?.segment_label).toBe(DESIGN_BUCKETS.sport.name);
    expect(sportStory?.segment_accent_hex).toBe(DESIGN_BUCKETS.sport.color);
  });

  it("every fixture story's reel chip label + hex equals its onboarding chip", async () => {
    const feed: Story[] = await getFeed();
    expect(feed.length).toBeGreaterThan(0);

    for (const story of feed) {
      const onboardingChip = DESIGN_BUCKETS[story.segment_key];
      expect(onboardingChip, `segment_key "${story.segment_key}" must be a DESIGN_BUCKETS root`).toBeDefined();
      expect(story.segment_label).toBe(onboardingChip.name);
      expect(story.segment_accent_hex).toBe(onboardingChip.color);
    }
  });

  it("all 8 topic roots resolve to a category-kind onboarding chip with a distinct hex", () => {
    // Locks the full 8-root surface (fixtures only cover 5 of 8). Every SegmentKey
    // must be a topic-category DESIGN_BUCKET, and the 8 accents must stay distinct
    // (a collision would make two reel chips indistinguishable).
    const eightRoots: SegmentKey[] = ["ai", "geopolitics", "business", "environment", "politics", "tech", "sport", "arts"];
    const hexes = new Set<string>();
    for (const root of eightRoots) {
      const chip = DESIGN_BUCKETS[root];
      expect(chip, `root "${root}" must exist in DESIGN_BUCKETS`).toBeDefined();
      expect(chip.kind).toBe("cat");
      hexes.add(chip.color);
    }
    expect(hexes.size, "all 8 root accent hexes must be distinct").toBe(eightRoots.length);
  });
});
