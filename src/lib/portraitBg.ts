/**
 * portraitBg — stable, hashed avatar fallback (Phase 5c SP2).
 *
 * When a source/personality has no thumbnail (or its `<img>` 404s), the universal
 * avatar ({@link "@/components/sources/SourceArtwork"}) renders an initials-on-
 * gradient tile instead. This module is the PURE helper behind that fallback: it
 * derives BOTH the initials and a deterministic gradient from the source name (or
 * any stable id), so the same input always yields the same gradient — avatars
 * never flicker or re-roll their color between renders.
 *
 * **Re-skin (CLAUDE.md / reuse-map §5):** ported in structure only from TL;DW's
 * `src/lib/portrait-bg.ts`. TL;DW's amber/neon `FALLBACK_GRADIENTS` are DROPPED;
 * the gradient stops are constrained to the News20 dark-editorial palette from
 * `reference/design-language.md` (primary `#3B82F6`, accent/blush `#E8B7BC`,
 * sage `#D1D4BD`, near-black `#020617`) so a fallback tile reads as part of the
 * same system as the reel — never as a carried-over TL;DW look.
 */

/**
 * News20 gradient stops, sourced VERBATIM from `reference/design-language.md`
 * Colors. A hash over the name picks one pair, so the gradient is stable per name
 * yet visually varied across the catalog. Both stops are palette tokens — no
 * arbitrary/amber color is ever emitted.
 */
const NEWS20_GRADIENT_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ["#3B82F6", "#020617"], // primary blue → near-black canvas
  ["#E8B7BC", "#3B82F6"], // blush accent → primary blue
  ["#D1D4BD", "#020617"], // sage surface → near-black canvas
  ["#3B82F6", "#E8B7BC"], // primary blue → blush accent
  ["#D1D4BD", "#3B82F6"], // sage surface → primary blue
  ["#020617", "#E8B7BC"], // near-black canvas → blush accent
] as const;

/**
 * Deterministic 32-bit string hash (the classic `h = h*31 + c` rolling hash,
 * `| 0`-clamped to 32 bits each step). Stable across renders/runtimes, so a given
 * `seed` always maps to the same gradient/index.
 *
 * @param seed - The string to hash (a source name or stable id).
 * @returns A non-negative 32-bit integer hash.
 *
 * @example
 * hashSeed("Lex Fridman") === hashSeed("Lex Fridman"); // true (stable)
 */
function hashSeed(seed: string): number {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

/**
 * Build a stable CSS `linear-gradient(...)` from a name/id, constrained to the
 * News20 palette. Same input → same gradient (so avatars don't flicker).
 *
 * @param seed - The source name (or any stable id) to derive the gradient from.
 * @returns A `linear-gradient(135deg, <stop>, <stop>)` CSS string (News20 tokens only).
 *
 * @example
 * portraitGradient("Acquired");
 * // => "linear-gradient(135deg, #3B82F6, #020617)"  (always the same for "Acquired")
 */
export function portraitGradient(seed: string): string {
  const [fromColor, toColor] = NEWS20_GRADIENT_PAIRS[hashSeed(seed) % NEWS20_GRADIENT_PAIRS.length];
  return `linear-gradient(135deg, ${fromColor}, ${toColor})`;
}

/**
 * Derive 1–2 uppercase initials from a name. Multi-word names take the first
 * letter of the first two words ("Sam Altman" → "SA"); single-word names take one
 * letter ("Acquired" → "A"). An empty/blank name falls back to "?".
 *
 * @param name - The source/personality display name.
 * @returns 1–2 uppercase initial characters (or "?" when the name is blank).
 *
 * @example
 * initials("Lex Fridman"); // "LF"
 * initials("Stratechery"); // "S"
 * initials("");            // "?"
 */
export function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .map((word) => word[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return letters || "?";
}
