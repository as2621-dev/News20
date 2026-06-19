/**
 * sourceSwipeData (Phase 5c SP-UI) — load + shape the source-swipe deck's
 * per-platform card view-models from the already-shipped phase-5c logic.
 *
 * Ported from the Claude Design "blip" handoff — Source Swipe. The prototype
 * hardcoded a `DATA` object; this replaces it with the REAL recommendation
 * pipeline (`reference/archetypes.md` §1, steps 1–3):
 *
 *   1. {@link rollUpInterestVector} — read the user's persisted topic + entity
 *      follows into the 8-category interest vector (zero vector when anon/new).
 *   2. {@link getArchetypes} + {@link mapToArchetype} — map that vector to the
 *      nearest archetype (cosine), falling back to `balanced-generalist`.
 *   3. {@link getRecommendedSources} — per platform axis, a balanced,
 *      popularity-ranked, follow-annotated source list for that archetype.
 *
 * Each catalog row is shaped into a {@link SourceSwipeCardModel} the deck renders
 * (name, thumbnail, follower label, coverage tags, why-text, % match, accent).
 *
 * Client-side only (Capacitor static export — no server runtime): every read is a
 * client Supabase read under RLS, degrading gracefully to an empty deck for a
 * brand-new/anon user (no crash — Rule 12 surfaces only genuine failures).
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { mapToArchetype } from "@/lib/archetypeMatch";
import { rollUpInterestVector } from "@/lib/interestVector";
import { logger } from "@/lib/logger";
import { getRecommendedSources, type RecommendedSource } from "@/lib/sourceRecommendations";
import { getArchetypes } from "@/lib/sources";
import type { ContentSourceType } from "@/types/source";

/**
 * The 4 swipe-deck platforms in their fixed onboarding order (YouTube → Podcasts →
 * X → People), each mapped to its catalog {@link ContentSourceType} axis, label,
 * pass title, and follower-count unit. Ported byte-for-byte from the prototype's
 * `PLATFORMS` (`blip-sources.js`), with `kind` added to bind each to the real axis.
 */
export const SOURCE_SWIPE_PLATFORMS = [
  {
    key: "yt",
    kind: "youtube_channel",
    label: "YouTube",
    glyph: "g-yt",
    title: "Channels for you",
    unit: "subscribers",
  },
  { key: "pod", kind: "podcast", label: "Podcasts", glyph: "g-pod", title: "Podcasts for you", unit: "listeners" },
  { key: "x", kind: "x_account", label: "X", glyph: "g-x", title: "Accounts for you", unit: "followers" },
  {
    key: "people",
    kind: "personality",
    label: "People",
    glyph: "g-people",
    title: "People for you",
    unit: "tracked everywhere",
  },
] as const satisfies ReadonlyArray<{
  key: string;
  kind: ContentSourceType;
  label: string;
  glyph: string;
  title: string;
  unit: string;
}>;

/** One platform pass config (an element of {@link SOURCE_SWIPE_PLATFORMS}). */
export type SourceSwipePlatform = (typeof SOURCE_SWIPE_PLATFORMS)[number];

/** The per-platform key (`"yt" | "pod" | "x" | "people"`). */
export type SourceSwipePlatformKey = SourceSwipePlatform["key"];

/** Max cards fetched per platform pass (a comfortable swipe set, not the whole catalog). */
const CARDS_PER_PLATFORM = 12;

/**
 * The accent gradient stops a card's logo header uses. The prototype keyed accent
 * off a hand-tagged topic color; here we derive it from the source's first
 * `topic_tag` so the accent stays meaningfully tied to the source's subject, with
 * a neutral primary-blue default for an untagged row. News20 palette only
 * (`reference/design-language.md`) — no carried-over prototype neon.
 */
const ACCENT_BY_TOPIC: Readonly<Record<string, string>> = {
  ai: "#3B82F6",
  geopolitics: "#EF4444",
  business: "#22C55E",
  environment: "#34D399",
  politics: "#A78BFA",
  tech: "#22D3EE",
  sport: "#F59E0B",
  arts: "#E8B7BC",
};

/** Default accent when a source carries no recognizable topic tag (primary blue). */
const DEFAULT_ACCENT = "#3B82F6";

/** A single deck card's view-model — everything {@link "@/components/sources/SourceSwipeCard"} renders. */
export interface SourceSwipeCardModel {
  /** The catalog `content_sources.source_id` — the follow target (stable key). */
  source_id: string;
  /** The source axis this card lives on (drives the follow + the glyph). */
  platform_kind: ContentSourceType;
  /** Display name (Playfair headline). */
  source_name: string;
  /** Thumbnail URL, or null → the initials-gradient fallback (portraitBg). */
  thumbnail_url: string | null;
  /** The follower/subscriber count label (e.g. "18.4M"), or null when unknown. */
  follower_label: string | null;
  /** 1–3 short coverage tags (the catalog `topic_tags`, upper-cased by the card). */
  coverage_tags: string[];
  /** The "why we picked this" blurb (the catalog description, or a derived line). */
  why_text: string;
  /** The 0–99 "% match" badge value (see {@link computeMatchPct} for the formula). */
  match_pct: number;
  /** The logo-header accent color (gradient start). */
  accent_color: string;
  /** True when the user ALREADY follows this source (annotated by the recommender). */
  is_already_added: boolean;
}

/** The full loaded deck: one card list per platform, parallel to {@link SOURCE_SWIPE_PLATFORMS}. */
export interface SourceSwipeDeck {
  /** Card lists keyed by platform key, in platform order. */
  cards_by_platform: Record<SourceSwipePlatformKey, SourceSwipeCardModel[]>;
  /** The matched archetype label (for any "people like you" copy). */
  archetype_label: string;
  /** The user's interest-vector picks, derived for the curtain pills (top categories). */
  curtain_picks: string[];
}

/**
 * Compute the 0–99 "% match" badge for a recommended source.
 *
 * Formula (documented per the task): blend the USER↔ARCHETYPE affinity (how well
 * the matched archetype fits the user — `archetypeScore`, cosine 0–1) with the
 * SOURCE's standing within that archetype (`popularity_score`, 0–100), then clamp
 * to a believable 60–99 band:
 *
 *   raw = 50 + archetypeScore × 30 + (popularity_score − 50) × 0.4
 *   match_pct = clamp(round(raw), 60, 99)
 *
 * - A strong archetype fit (cosine ≈ 1) adds up to +30; a weak/fallback fit adds
 *   little, so a generalist user's cards read as plausibly-but-not-perfectly matched.
 * - A top-popularity source (~100) adds +20 over the midpoint; an obscure one (~0)
 *   subtracts −20 — so the in-archetype rank visibly orders the badges.
 * - The 60-floor keeps every recommended card feeling like a real recommendation
 *   (we only ever SHOW archetype-matched sources), the 99-ceiling avoids a fake
 *   "100% perfect" claim.
 *
 * @param popularityScore - The source's `popularity_score` (0–100).
 * @param archetypeScore - The user↔archetype cosine similarity (0–1).
 * @returns An integer 60–99.
 */
export function computeMatchPct(popularityScore: number, archetypeScore: number): number {
  const raw = 50 + archetypeScore * 30 + (popularityScore - 50) * 0.4;
  return Math.min(99, Math.max(60, Math.round(raw)));
}

/** Pick a card accent from the source's first topic tag (palette-constrained). */
function accentForSource(topicTags: string[]): string {
  const firstTag = topicTags[0]?.toLowerCase();
  return (firstTag && ACCENT_BY_TOPIC[firstTag]) || DEFAULT_ACCENT;
}

/** Format a raw subscriber count into a short label ("18400000" → "18.4M"). */
function formatFollowerLabel(count: number | null): string | null {
  if (count === null || !Number.isFinite(count) || count <= 0) {
    return null;
  }
  if (count >= 1_000_000) {
    return `${(count / 1_000_000).toFixed(count >= 10_000_000 ? 0 : 1)}M`;
  }
  if (count >= 1_000) {
    return `${Math.round(count / 1_000)}K`;
  }
  return String(count);
}

/**
 * Derive the "why we picked this" blurb. Prefer the catalog `source_description`;
 * fall back to a short archetype-aware line so a description-less row still reads
 * as a real recommendation (never an empty box).
 */
function whyTextForSource(source: RecommendedSource, archetypeLabel: string): string {
  if (source.source_description && source.source_description.trim() !== "") {
    return source.source_description.trim();
  }
  const lead = source.topic_tags[0] ? `${source.topic_tags[0]} ` : "";
  return `A top ${lead}pick for your ${archetypeLabel} taste.`;
}

/** Shape one recommended catalog row into a deck card view-model. */
function toCardModel(
  source: RecommendedSource,
  platformKind: ContentSourceType,
  archetypeScore: number,
  archetypeLabel: string,
): SourceSwipeCardModel {
  return {
    source_id: source.source_id,
    platform_kind: platformKind,
    source_name: source.source_name,
    thumbnail_url: source.thumbnail_url,
    follower_label: formatFollowerLabel(source.subscriber_count),
    coverage_tags: source.topic_tags.slice(0, 3),
    why_text: whyTextForSource(source, archetypeLabel),
    match_pct: computeMatchPct(source.popularity_score, archetypeScore),
    accent_color: accentForSource(source.topic_tags),
    is_already_added: source.is_already_added,
  };
}

/**
 * Derive the curtain "picks" pills from the user's interest vector — the top
 * non-zero pinned categories (title-cased), so the curtain reflects the REAL
 * profile that drove the recommendations. Empty for a brand-new/anon user (the
 * curtain then leans on its rows-ticking animation instead of pills).
 *
 * @param interestVector - The rolled-up 8-category vector (may be partial/empty).
 * @returns Up to 3 title-cased category labels, strongest first.
 */
function deriveCurtainPicks(interestVector: Record<string, number>): string[] {
  return Object.entries(interestVector)
    .filter(([, weight]) => weight > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([category]) => category.charAt(0).toUpperCase() + category.slice(1));
}

/**
 * Load the full source-swipe deck for the current user: roll up their interests,
 * map to an archetype, and fetch + shape a balanced recommended card list per
 * platform axis. Reads run client-side under RLS and degrade gracefully — a new/
 * anon user gets the `balanced-generalist` archetype and whatever the catalog
 * surfaces for it (often a short or empty deck), never a crash.
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client when omitted.
 * @returns The {@link SourceSwipeDeck} — per-platform card lists + the archetype label + curtain picks.
 * @throws If a genuine catalog/profile read fails (surfaced, never swallowed — Rule 12).
 *
 * @example
 * const deck = await loadSourceSwipeDeck();
 * deck.cards_by_platform.yt[0].source_name; // top YouTube recommendation
 */
export async function loadSourceSwipeDeck(client?: SupabaseClient): Promise<SourceSwipeDeck> {
  logger.info("load_source_swipe_deck_started", {});

  // Steps 1–2: roll up the interest vector and map it to the nearest archetype.
  const interestVector = client ? await rollUpInterestVector(client) : await rollUpInterestVector();
  const archetypes = client ? await getArchetypes(client) : await getArchetypes();
  const match = mapToArchetype(interestVector, archetypes);

  // The recommender balances across the top archetype(s); v1 uses the single best
  // match (the matcher returns one). The vector's non-zero categories double as the
  // sub-niche boost tags so a strongly-themed user nudges matching sources up.
  const archetypeSlugs = [match.archetype_id];
  const subNiches = Object.keys(interestVector).filter(
    (category) => (interestVector[category as keyof typeof interestVector] ?? 0) > 0,
  );

  // Step 3: fetch a balanced, ranked, follow-annotated list per platform axis in parallel.
  const perPlatformLists = await Promise.all(
    SOURCE_SWIPE_PLATFORMS.map((platform) =>
      getRecommendedSources(platform.kind, {
        archetypes: archetypeSlugs,
        subNiches,
        limit: CARDS_PER_PLATFORM,
        client,
      }),
    ),
  );

  const cards_by_platform = {} as Record<SourceSwipePlatformKey, SourceSwipeCardModel[]>;
  SOURCE_SWIPE_PLATFORMS.forEach((platform, index) => {
    cards_by_platform[platform.key] = perPlatformLists[index].map((source) =>
      toCardModel(source, platform.kind, match.archetype_score, match.archetype_label),
    );
  });

  const deck: SourceSwipeDeck = {
    cards_by_platform,
    archetype_label: match.archetype_label,
    curtain_picks: deriveCurtainPicks(interestVector),
  };

  logger.info("load_source_swipe_deck_completed", {
    archetype_id: match.archetype_id,
    is_fallback: match.is_fallback,
    yt: cards_by_platform.yt.length,
    pod: cards_by_platform.pod.length,
    x: cards_by_platform.x.length,
    people: cards_by_platform.people.length,
  });

  return deck;
}
