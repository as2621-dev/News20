/**
 * Unit tests for the defensive paragraph helpers the ArticleLayer long-form
 * body renders through (Bug 4: a legacy mega-chunk must display as real
 * paragraphs, and a duplicated leading headline must be dropped).
 */

import { describe, expect, it } from "vitest";
import { splitChunkTextIntoParagraphs, stripLeadingHeadlineDuplicate } from "@/lib/detail/paragraphs";

describe("splitChunkTextIntoParagraphs", () => {
  it("splits a legacy single-newline mega-chunk into paragraphs (happy path)", () => {
    const chunk_text = "Headline line\nDek line goes here.\nNEW YORK — Body paragraph one.\nBody paragraph two.";
    expect(splitChunkTextIntoParagraphs(chunk_text)).toEqual([
      "Headline line",
      "Dek line goes here.",
      "NEW YORK — Body paragraph one.",
      "Body paragraph two.",
    ]);
  });

  it("splits blank-line-separated text and drops whitespace-only segments", () => {
    expect(splitChunkTextIntoParagraphs("Para one.\n\n   \n\nPara two.  ")).toEqual(["Para one.", "Para two."]);
  });

  it("returns a correctly chunked (newline-free) paragraph unchanged", () => {
    expect(splitChunkTextIntoParagraphs("One clean paragraph.")).toEqual(["One clean paragraph."]);
  });

  it("returns [] for empty or whitespace-only input (edge case)", () => {
    expect(splitChunkTextIntoParagraphs("")).toEqual([]);
    expect(splitChunkTextIntoParagraphs("  \n \n ")).toEqual([]);
  });
});

describe("stripLeadingHeadlineDuplicate", () => {
  const story_headline = "Intel vs TSMC: Which Semiconductor Giant Offers Stronger Outlook for Investors in 2026";

  it("drops a leading paragraph that duplicates the headline (happy path)", () => {
    const paragraphs = [story_headline, "Dek line.", "Body."];
    expect(stripLeadingHeadlineDuplicate(paragraphs, story_headline)).toEqual(["Dek line.", "Body."]);
  });

  it("matches case-insensitively, with collapsed whitespace and trailing punctuation", () => {
    const paragraphs = [
      "  intel vs tsmc:   which semiconductor giant offers stronger outlook for investors in 2026. ",
      "Body.",
    ];
    expect(stripLeadingHeadlineDuplicate(paragraphs, story_headline)).toEqual(["Body."]);
  });

  it("keeps a non-duplicate leading paragraph (failure-to-match path)", () => {
    const paragraphs = ["A different opening line.", "Body."];
    expect(stripLeadingHeadlineDuplicate(paragraphs, story_headline)).toEqual(paragraphs);
  });

  it("never empties the body: a lone headline-only paragraph is kept (edge case)", () => {
    expect(stripLeadingHeadlineDuplicate([story_headline], story_headline)).toEqual([story_headline]);
  });

  it("ignores an empty headline (edge case)", () => {
    expect(stripLeadingHeadlineDuplicate(["First.", "Second."], "")).toEqual(["First.", "Second."]);
  });
});
