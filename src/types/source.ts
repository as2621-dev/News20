/**
 * Content-source domain types (Phase 5b SP4) — the News20 TypeScript shapes for
 * the source-axis catalog created by migration `0009_content_sources.sql`.
 *
 * These types mirror the `0009` columns EXACTLY (names + nullability), so the
 * client-side data layer (`src/lib/sources.ts`) and the later recommendation
 * (5c) / control-surface (5e) UIs read a single typed contract instead of raw
 * PostgREST rows. Per CLAUDE.md C3, follow-as-source lives here (the
 * `user_content_sources` follow → ingestion), distinct from follow-as-filter
 * (`follows` → ranking) in `src/lib/follows.ts`.
 *
 * `SOURCE_TYPE_CONFIGS` is the per-axis display config ported from TL;DW
 * `src/types/source.ts:100-119` (reuse-map §5), extended with the News20-only
 * `x_account` 4th axis. It is News20-neutral copy/iconography — none of TL;DW's
 * "editorial-dark amber" palette is carried over (re-skin rule, reuse-map §5).
 */

/**
 * The closed set of followable source axes — the `content_source_type` Postgres
 * enum (migration 0009). `youtube_channel`/`podcast` carry over from the donor;
 * `x_account` re-adds the donor's pruned `twitter_account`; `personality` lets a
 * named creator be addressed as a source axis.
 */
export type ContentSourceType = "youtube_channel" | "podcast" | "x_account" | "personality";

/**
 * The 3-state follow priority for the control surface — the `source_priority`
 * Postgres enum (migration 0009). `off` = followed-but-muted; `big_stuff` = only
 * high-traction items; `everything` = ingest all (the default on a fresh follow).
 */
export type SourcePriority = "off" | "big_stuff" | "everything";

/**
 * One row of the public-read `content_sources` curated catalog (migration 0009).
 *
 * Field names + nullability track the DDL one-for-one: the four `*_at`/optional
 * columns (`source_description`, `thumbnail_url`, `subscriber_count`,
 * `platform_metadata`, `last_fetched_at`) are nullable in the schema and so are
 * `null` here; the array/score/flag columns are `NOT NULL` with defaults and are
 * therefore always present.
 */
export interface ContentSource {
  /** `source_id uuid` — primary key. */
  source_id: string;
  /** `content_source_type` — which axis this source lives on. */
  content_source_type: ContentSourceType;
  /** `external_id text` — the platform id (YouTube channel id, iTunes id, handle). */
  external_id: string;
  /** `source_name text` — the display name. */
  source_name: string;
  /** `source_description text` — nullable blurb. */
  source_description: string | null;
  /** `thumbnail_url text` — nullable avatar URL. */
  thumbnail_url: string | null;
  /** `subscriber_count bigint` — nullable follower count (string-safe; see note). */
  subscriber_count: number | null;
  /** `platform_metadata jsonb` — nullable per-axis blob (e.g. podcast `feed_url`). */
  platform_metadata: Record<string, unknown> | null;
  /** `personas text[] NOT NULL default '{}'` — archetype slugs this source serves. */
  personas: string[];
  /** `topic_tags text[] NOT NULL default '{}'` — 8-category-aligned tags. */
  topic_tags: string[];
  /** `popularity_score numeric NOT NULL default 50` — catalog rank within a persona. */
  popularity_score: number;
  /** `is_curated boolean NOT NULL default true` — curated vs user-added. */
  is_curated: boolean;
  /** `last_fetched_at timestamptz` — nullable last ingestion timestamp (ISO string). */
  last_fetched_at: string | null;
}

/**
 * One row of the owner-scoped `user_content_sources` follow junction
 * (migration 0009). RLS pins every row to `auth.uid()` via the
 * `user_content_sources_owner_all` policy.
 */
export interface UserContentSource {
  /** `user_id uuid` → `auth.users(id)` — the owner (= `auth.uid()`). */
  user_id: string;
  /** `source_id uuid` → `content_sources(source_id)` — the followed source. */
  source_id: string;
  /** `source_priority` — the 3-state ingestion priority (default `everything`). */
  source_priority: SourcePriority;
  /** `added_via text` — nullable free-text follow origin (onboarding, manual, …). */
  added_via: string | null;
}

/**
 * One row of the public-read `personalities` catalog (migration 0009) — the
 * named-creator axis. Mirrors the DDL: `bio`/`photo_url` are nullable; the
 * array/score/flag columns are `NOT NULL` with defaults.
 */
export interface Personality {
  /** `personality_id uuid` — primary key. */
  personality_id: string;
  /** `display_name text` — unique display name. */
  display_name: string;
  /** `aliases text[] NOT NULL default '{}'` — alternate names for the hunt match. */
  aliases: string[];
  /** `bio text` — nullable blurb. */
  bio: string | null;
  /** `photo_url text` — nullable portrait URL. */
  photo_url: string | null;
  /** `youtube_channel_ids text[] NOT NULL default '{}'` — own-channel exclusion ids. */
  youtube_channel_ids: string[];
  /** `personas text[] NOT NULL default '{}'` — archetype slugs this person serves. */
  personas: string[];
  /** `topic_tags text[] NOT NULL default '{}'` — 8-category-aligned tags. */
  topic_tags: string[];
  /** `popularity_score numeric NOT NULL default 50` — catalog rank within a persona. */
  popularity_score: number;
  /** `is_curated boolean NOT NULL default true` — curated vs user-added. */
  is_curated: boolean;
}

/**
 * One row of the public-read `archetypes` reference table (migration 0009) — a
 * normalized interest vector the 5c matcher scores a user's interests against.
 */
export interface Archetype {
  /** `archetype_id uuid` — primary key. */
  archetype_id: string;
  /** `archetype_slug text` — the stable, unique seed key (e.g. `ai-frontier-tech`). */
  archetype_slug: string;
  /** `archetype_label text` — the human-readable label. */
  archetype_label: string;
  /**
   * `archetype_vector jsonb` — a normalized weight map over the 8 pinned
   * categories (`ai|geopolitics|business|environment|politics|tech|sport|arts`).
   */
  archetype_vector: Record<string, number>;
}

/** Per-axis display config: the copy + iconography a source-picker tile renders. */
export interface SourceTypeConfig {
  /** Singular display label (e.g. "YouTube channel"). */
  label: string;
  /** Plural display label (e.g. "YouTube channels"). */
  label_plural: string;
  /** A short, stable icon key the UI maps to an SVG/glyph (News20-neutral, no emoji). */
  icon_key: string;
  /** Placeholder copy for the axis's search/add field. */
  search_placeholder: string;
  /** Short pill/badge text shown on a source tile to mark its axis. */
  pill_label: string;
  /**
   * Tile geometry the universal avatar honors: people render as a circle, the
   * channel/podcast/account axes as a rounded square (reuse-map §5 source-artwork).
   */
  tile_shape: "circle" | "square";
}

/**
 * Per-axis display config map ported from TL;DW `src/types/source.ts:100-119`
 * (reuse-map §5), extended with the News20-only `x_account` 4th entry. Keyed by
 * `ContentSourceType` so a lookup is exhaustive and type-checked — adding a new
 * axis to the enum forces a new entry here. Copy is News20-neutral; the
 * `icon_key`s are stable string handles the UI resolves to glyphs (no palette).
 *
 * @example
 * const config = SOURCE_TYPE_CONFIGS["podcast"];
 * config.label; // "Podcast"
 * config.tile_shape; // "square"
 */
export const SOURCE_TYPE_CONFIGS: Record<ContentSourceType, SourceTypeConfig> = {
  youtube_channel: {
    label: "YouTube channel",
    label_plural: "YouTube channels",
    icon_key: "youtube",
    search_placeholder: "Search YouTube channels",
    pill_label: "Channel",
    tile_shape: "square",
  },
  podcast: {
    label: "Podcast",
    label_plural: "Podcasts",
    icon_key: "podcast",
    search_placeholder: "Search podcasts",
    pill_label: "Podcast",
    tile_shape: "square",
  },
  x_account: {
    label: "X account",
    label_plural: "X accounts",
    icon_key: "x",
    search_placeholder: "Search X accounts",
    pill_label: "X",
    tile_shape: "circle",
  },
  personality: {
    label: "Personality",
    label_plural: "Personalities",
    icon_key: "personality",
    search_placeholder: "Search people",
    pill_label: "Person",
    tile_shape: "circle",
  },
};
