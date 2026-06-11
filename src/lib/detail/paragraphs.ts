/**
 * Pure paragraph helpers for the full-article Detail layer (ArticleLayer).
 *
 * Defensive rendering seam: older pipeline rows hold a WHOLE article in one
 * `detail_chunks` row, with paragraphs separated by single `\n` (and the
 * headline + dek as the first lines). Splitting at render time fixes every
 * already-stored row without a data backfill; correctly chunked rows pass
 * through unchanged (their `chunk_text` contains no newlines).
 */

/**
 * Split one detail chunk's text into renderable paragraphs.
 *
 * @param chunk_text - The stored `detail_chunks.chunk_text`.
 * @returns Trimmed, non-empty paragraphs (a newline-free chunk returns itself).
 *
 * @example
 * splitChunkTextIntoParagraphs("Para one.\nPara two.");
 * // ["Para one.", "Para two."]
 */
export function splitChunkTextIntoParagraphs(chunk_text: string): string[] {
  return chunk_text
    .split(/\n+/)
    .map((paragraph) => paragraph.trim())
    .filter((paragraph) => paragraph.length > 0);
}

/** Lowercase, collapse whitespace, strip trailing punctuation — for headline compare. */
function normalizeForHeadlineCompare(text: string): string {
  return text
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ")
    .replace(/[.!?:;,]+$/, "");
}

/**
 * Drop a leading paragraph that duplicates the story headline.
 *
 * The article header already renders the headline as `.art-h1`, so a body
 * whose first line repeats it would show the headline twice. Never empties a
 * non-empty input: if the headline is the ONLY paragraph it is kept.
 *
 * @param paragraphs - Paragraphs from {@link splitChunkTextIntoParagraphs}.
 * @param story_headline - The story headline to compare against (normalized).
 * @returns The paragraphs, minus a duplicated leading headline.
 */
export function stripLeadingHeadlineDuplicate(paragraphs: string[], story_headline: string): string[] {
  if (paragraphs.length <= 1 || story_headline.trim() === "") {
    return paragraphs;
  }
  if (normalizeForHeadlineCompare(paragraphs[0]) === normalizeForHeadlineCompare(story_headline)) {
    return paragraphs.slice(1);
  }
  return paragraphs;
}
