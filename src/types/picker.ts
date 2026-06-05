/**
 * Recursive interest-picker data model (Phase 5 SP3) — the §5 `Node`/`FollowSet`
 * contract from `onboarding_interest_picker_spec.md`, plus the selection-store and
 * canonical-identity types the engine (`src/lib/followSets.ts`) and SP4's
 * `OnboardingPicker`/`SelectionTray` consume.
 *
 * The picker tree is **lifted** (not re-authored) from `interest_picker.html`'s
 * `DATA` const and transformed into these typed nodes with **path-derived ids**
 * that EXACTLY equal SP1's registry `entity_id` (see `src/lib/followSets.ts`), so a
 * seed chip's id matches its `entities` row.
 *
 * Two axes the model encodes (spec §2):
 *   - `type: 'topic'`  — stable/finite, shipped inline (no registry).
 *   - `type: 'entity'` — dynamic/unbounded; the set carries a `registry` pointer so
 *     Show-more (`listEntities`) and Add-your-own (`searchEntities`) can page/search it.
 */

import type { EntityKind } from "@/lib/entities";

/** Re-export so SP3 consumers (and SP4) import the picker's kind union from one place. */
export type { EntityKind } from "@/lib/entities";

/**
 * Where a follow originated (spec §7). A `custom` follow (the user typed it) is
 * higher-intent than a seed tap; SP4 weights it more heavily in ranking. Mirrors
 * the `entity_follow_source` enum (migration 0007) plus the picker-only marker for
 * free-text customs that resolve to no registry entity.
 */
export type FollowSource = "seed" | "more" | "custom";

/**
 * A selectable bubble OR a navigational container (spec §5 "A node"). When a node
 * carries child `sets`, selecting it lazily reveals them (recursive nesting). `kind`
 * + `ticker` are entity-only affordances; topic nodes omit both.
 */
export interface PickerNode {
  /** Stable, path-derived id (== SP1 registry `entity_id` for seed entities). */
  id: string;
  /** Human-readable label rendered on the chip. */
  label: string;
  /** `'entity'` when the node carries a `kind`, else `'topic'` (spec §2). */
  type: "topic" | "entity";
  /** Entity taxonomy kind (companies render a ticker, etc.); entities only. */
  kind?: EntityKind;
  /** Ticker symbol rendered in the rust accent; companies only. */
  ticker?: string;
  /** Child follow-sets revealed when this node is selected (recursive). */
  sets?: PickerFollowSet[];
}

/**
 * The registry pointer on an **entity** follow-set (spec §5). Present only when the
 * set's items are entities — it scopes Show-more (`listEntities`) and Add-your-own
 * (`searchEntities`) calls. Pure-topic sets omit this (and so get neither control).
 */
export interface PickerRegistryPointer {
  /** The set-id scope passed as `parent` to the registry reads (== set `id`). */
  parent: string;
  /** The dominant `kind` of the set's entity items, passed as the `kind` filter. */
  kind: EntityKind;
}

/**
 * A labeled group of bubbles (spec §5 "A FollowSet"). Always provides Select-all and
 * Add-your-own; entity sets additionally provide Show-more (gated by `registry`).
 */
export interface PickerFollowSet {
  /** Stable, path-derived id (the eyebrow set's scope id). */
  id: string;
  /** Eyebrow label shown above the chip grid (mono caps). */
  label: string;
  /** Seed bubbles shown immediately on mount. */
  items: PickerNode[];
  /**
   * Registry pointer — present on entity sets (gates Show-more + scopes
   * Add-your-own), ABSENT on pure-topic sets (which get neither, matching the
   * prototype that only renders Show-more when `set.more` exists).
   */
  registry?: PickerRegistryPointer;
  /** Always true per product requirement (spec §5) — every set accepts customs. */
  allowCustom: true;
  /**
   * The lifted `set.more` rows kept as the **OFFLINE fallback** for Show-more: when
   * `listEntities` errors/offline, these are appended instead of the live page.
   * Entity sets only.
   */
  moreSeeds?: PickerNode[];
}

/** A top-level category (spec §3) — the 8 sections of the picker. */
export interface PickerCategory {
  /** Stable category id (the prototype's `cat.id`, e.g. `"business"`). */
  id: string;
  /** Display label (e.g. `"Business"`) — also the first segment of every `path`. */
  label: string;
  /** Subcategories, each a node whose `sets` render when the subcategory expands. */
  subs: PickerSubcategory[];
}

/** A subcategory (spec §3) — a collapsible group of follow-sets under a category. */
export interface PickerSubcategory {
  /** Stable, path-derived subcategory id (`category/slug(label)`). */
  id: string;
  /** Display label. */
  label: string;
  /** The follow-sets rendered when this subcategory expands. */
  sets: PickerFollowSet[];
}

/**
 * One user selection, keyed in the store by its `followId` (== node `id`). Mirrors
 * the spec §7 payload shape, plus the `canonicalKey` the store dedupes on so that
 * the same real-world entity reached via two paths collapses to ONE entry carrying
 * BOTH `paths`.
 */
export interface FollowSelection {
  /** The node id the user tapped (the path the selection came IN by). */
  followId: string;
  /** Human-readable label. */
  label: string;
  /**
   * The ancestry trail (spec §7 `path`): `[Category, …, label]`. Multiple paths
   * accumulate here when the same canonical entity is selected via several routes.
   */
  path: string[];
  /** Additional paths recorded when this canonical entity was reached another way. */
  extraPaths?: string[][];
  /** `'topic'` or `'entity'` (mirrors the source node). */
  type: "topic" | "entity";
  /** Entity kind, or `'freetext'` for an unresolved Add-your-own custom. */
  kind?: EntityKind | "freetext";
  /** Ticker symbol (companies only). */
  ticker?: string;
  /** Where the follow came from (spec §7 intent signal). */
  source: FollowSource;
  /**
   * The canonical identity this selection dedupes on (see {@link CanonicalKey}). All
   * selections sharing a `canonicalKey` are ONE follow with several `path`s.
   */
  canonicalKey: string;
}

/**
 * A canonical identity string used for cross-path dedupe (spec §11). Two registry
 * rows for the same real-world entity (Nvidia reached via AI-hardware AND
 * Business-earnings) share a `CanonicalKey` and collapse to one follow.
 *
 * Derivation (see `canonicalKeyFor` in `src/lib/followSets.ts`):
 *   - entity:  `${kind}:${ticker ?? slug(label)}`   e.g. `company:NVDA`
 *   - topic:   `topic:${slug(label)}`                e.g. `topic:inflation`
 *   - freetext:`freetext:${slug(label)}`             (an Add-your-own miss)
 */
export type CanonicalKey = string;

/**
 * The public selection-store surface (a framework-light subscribable store; see
 * `createSelectionStore` in `src/lib/followSets.ts`). SP4's `OnboardingPicker` reads
 * this via `useSyncExternalStore`; the store is the SINGLE source of truth for
 * lazy-mount, preserve-on-collapse, and canonical dedupe.
 */
export interface SelectionStore {
  /**
   * Toggle a selection by its `followId`. If a selection with the SAME
   * `canonicalKey` already exists under a DIFFERENT `followId`, the new path is
   * APPENDED to that canonical entry instead of creating a second entry (dedupe).
   * Toggling an already-present `followId` removes it (and, if it was the last path
   * for its canonical entry, drops the entry). Returns the new selected state.
   */
  toggle(selection: FollowSelection): boolean;
  /** True when a selection with this `followId` is currently active. */
  has(followId: string): boolean;
  /** True when ANY active selection shares this canonical identity. */
  hasCanonical(canonicalKey: string): boolean;
  /** All active canonical selections (one entry per real-world follow). */
  all(): FollowSelection[];
  /** Count of active canonical selections (what the tray shows). */
  count(): number;
  /** Subscribe to changes; returns an unsubscribe fn (for `useSyncExternalStore`). */
  subscribe(listener: () => void): () => void;
  /** Stable snapshot for `useSyncExternalStore` (changes identity on mutation). */
  getSnapshot(): readonly FollowSelection[];
}
