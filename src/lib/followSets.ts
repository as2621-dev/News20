/**
 * Recursive interest-picker engine (Phase 5 SP3) — the pure data + state core that
 * `FollowSet`/`FollowChip` render against, and that SP4's `OnboardingPicker`/
 * `SelectionTray` read for counts + the §7 persistence payload.
 *
 * Three responsibilities:
 *   1. **Lift** the prototype's `DATA` tree (`RAW_PICKER_DATA`) into the typed §5
 *      `PickerCategory[]` (`PICKER_TREE`) with **path-derived ids** that EXACTLY
 *      equal SP1's registry `entity_id` (so a seed chip's id == its `entities` row),
 *      attaching a `registry` pointer to each entity set (gating Show-more /
 *      Add-your-own) and keeping `set.more` as the `moreSeeds` offline fallback.
 *   2. **Canonical identity** — derive a `CanonicalKey` so the same real-world entity
 *      reached via several paths (Nvidia under AI-hardware AND Business-earnings)
 *      collapses to ONE follow carrying BOTH paths (spec §11 dedupe).
 *   3. **Selection store** — a framework-light, subscribable store keyed by
 *      `followId` that the components drive via `useSyncExternalStore`. State (not
 *      DOM) is the source of truth for lazy-mount + preserve-on-collapse, so child
 *      selections survive a parent collapse/unmount (spec §11).
 *
 * State choice (Rule 11): the repo lists `zustand` in package.json but uses it
 * NOWHERE in `src/` (verified by grep); the shipped onboarding (`InterestChips`)
 * uses React `useState`. To avoid both a brand-new state idiom AND component-local
 * `useState` (which can't survive the unmount that preserve-on-collapse requires),
 * this ships a tiny dependency-free vanilla store with `subscribe`/`getSnapshot`,
 * consumed via React 19's built-in `useSyncExternalStore`. No new dependency added.
 */

import type { EntityKind, EntityResult } from "@/lib/entities";
import { logger } from "@/lib/logger";
import { RAW_PICKER_DATA, type RawCategory, type RawNode, type RawSet } from "@/lib/pickerSeedTree";
import type {
  CanonicalKey,
  FollowSelection,
  FollowSource,
  PickerCategory,
  PickerFollowSet,
  PickerNode,
  PickerSubcategory,
  SelectionStore,
} from "@/types/picker";

/**
 * Slugify a label EXACTLY as the prototype does (`interest_picker.html` line 93):
 * lowercase → collapse any run of non-`[a-z0-9]` to a single `-` → trim leading/
 * trailing `-`. Ported verbatim so generated ids equal SP1's registry `entity_id`.
 *
 * @param value - The raw label.
 * @returns The path-segment slug.
 *
 * @example
 * slug("Companies to track"); // "companies-to-track"
 * slug("Ukraine–Russia");     // "ukraine-russia"  (en-dash collapses)
 */
export function slug(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

/**
 * Derive the canonical-identity key for a selection (spec §11 cross-path dedupe).
 * Entities collapse on `kind:ticker` (or `kind:slug(label)` when tickerless); topics
 * and free-text customs collapse on their slug. So Nvidia reached via two paths —
 * both `company:NVDA` — is ONE follow; two genuinely different companies are not.
 *
 * @param params - The identity-bearing fields (`type`, `kind?`, `ticker?`, `label`).
 * @returns The canonical key string.
 *
 * @example
 * canonicalKeyFor({ type: "entity", kind: "company", ticker: "NVDA", label: "Nvidia" });
 * // "company:NVDA"
 * canonicalKeyFor({ type: "topic", label: "Inflation" }); // "topic:inflation"
 */
export function canonicalKeyFor(params: {
  type: "topic" | "entity";
  kind?: EntityKind | "freetext";
  ticker?: string;
  label: string;
}): CanonicalKey {
  const { type, kind, ticker, label } = params;
  if (type === "topic") {
    return `topic:${slug(label)}`;
  }
  if (kind === "freetext") {
    return `freetext:${slug(label)}`;
  }
  const identity = ticker && ticker.trim() !== "" ? ticker.trim() : slug(label);
  // Reason: entities of the SAME kind + ticker are the same real-world thing even
  // when their path-derived ids (and thus registry rows) differ.
  return `${kind ?? "entity"}:${identity}`;
}

/**
 * Pick a set's registry `kind`: the FIRST entity item's kind (the prototype groups a
 * set by one dominant kind — a Companies set is all `company`, a Teams set all
 * `team`). `undefined` when the set has no entity items (a pure-topic set).
 */
function dominantEntityKind(items: RawNode[]): EntityKind | undefined {
  for (const item of items) {
    if (item.kind) {
      return item.kind;
    }
  }
  return undefined;
}

/**
 * Transform a raw set into a typed {@link PickerFollowSet}, recursively lifting each
 * item's own nested sets. The set id = `${idBase}/${slug(set.label)}` (prototype
 * `renderSet`). A registry pointer is attached ONLY when the set has entity items —
 * this is what gates Show-more + Add-your-own to entity sets (topic sets get neither,
 * matching the prototype that shows Show-more only when `set.more` exists).
 *
 * @param rawSet - The lifted raw set.
 * @param idBase - The parent id this set hangs under (subcategory id or a chip id).
 * @returns The typed follow-set.
 */
function liftSet(rawSet: RawSet, idBase: string): PickerFollowSet {
  const setId = `${idBase}/${slug(rawSet.label)}`;
  const items = rawSet.items.map((rawItem) => liftNode(rawItem, setId));
  const kind = dominantEntityKind(rawSet.items);

  const followSet: PickerFollowSet = {
    id: setId,
    label: rawSet.label,
    items,
    allowCustom: true,
  };

  if (kind) {
    // Entity set → registry pointer scopes listEntities/searchEntities to this set.
    followSet.registry = { parent: setId, kind };
  }
  if (rawSet.more && rawSet.more.length > 0) {
    // Offline fallback for Show-more: the prototype's curated extra rows, lifted as
    // typed nodes so a registry error/offline still reveals more (spec §11).
    followSet.moreSeeds = rawSet.more.map((rawItem) => liftNode(rawItem, setId));
  }
  return followSet;
}

/**
 * Transform a raw node into a typed {@link PickerNode}. The node id =
 * `${idBase}/${slug(node.label)}` (prototype `makeChip`). `type` is `'entity'` when
 * the node carries a `kind`, else `'topic'` (spec §2). Child sets are lifted
 * recursively under THIS node's id (so a nested set's id descends from the chip).
 *
 * @param rawNode - The lifted raw node.
 * @param idBase - The set id this node is a chip of.
 * @returns The typed node.
 */
function liftNode(rawNode: RawNode, idBase: string): PickerNode {
  const nodeId = `${idBase}/${slug(rawNode.label)}`;
  const node: PickerNode = {
    id: nodeId,
    label: rawNode.label,
    type: rawNode.kind ? "entity" : "topic",
  };
  if (rawNode.kind) {
    node.kind = rawNode.kind;
  }
  if (rawNode.ticker) {
    node.ticker = rawNode.ticker;
  }
  if (rawNode.sets && rawNode.sets.length > 0) {
    node.sets = rawNode.sets.map((childSet) => liftSet(childSet, nodeId));
  }
  return node;
}

/**
 * Transform a raw category into a typed {@link PickerCategory}. Each subcategory's id
 * base = `${cat.id}/${slug(sub.label)}` (prototype's `cat.id+'/'+slug(sub.label)`),
 * under which its sets (and their chips) descend.
 */
function liftCategory(rawCategory: RawCategory): PickerCategory {
  const subs: PickerSubcategory[] = rawCategory.subs.map((rawSub) => {
    const subId = `${rawCategory.id}/${slug(rawSub.label)}`;
    return {
      id: subId,
      label: rawSub.label,
      sets: rawSub.sets.map((rawSet) => liftSet(rawSet, subId)),
    };
  });
  return { id: rawCategory.id, label: rawCategory.label, subs };
}

/**
 * Lift the entire raw prototype tree into the typed §5 model. Pure + deterministic.
 *
 * @param raw - The raw lifted dataset (defaults to {@link RAW_PICKER_DATA}).
 * @returns The 8 typed categories.
 */
export function liftPickerTree(raw = RAW_PICKER_DATA): PickerCategory[] {
  return raw.categories.map(liftCategory);
}

/**
 * The lifted, typed picker tree — the single seed the engine + components render.
 * Computed once at module load (the source data is static).
 */
export const PICKER_TREE: PickerCategory[] = liftPickerTree();

/**
 * Build a {@link FollowSelection} from a node the user tapped, on a given path. The
 * canonical key is derived here so the store can dedupe immediately.
 *
 * @param params.node - The tapped picker node.
 * @param params.path - The ancestry trail `[Category, …, label]`.
 * @param params.source - Where the follow came from (`seed`/`more`/`custom`).
 * @returns The selection payload (spec §7 shape + `canonicalKey`).
 */
export function selectionFromNode(params: {
  node: Pick<PickerNode, "id" | "label" | "type" | "kind" | "ticker">;
  path: string[];
  source: FollowSource;
}): FollowSelection {
  const { node, path, source } = params;
  const selection: FollowSelection = {
    followId: node.id,
    label: node.label,
    path,
    type: node.type,
    source,
    canonicalKey: canonicalKeyFor({
      type: node.type,
      kind: node.kind,
      ticker: node.ticker,
      label: node.label,
    }),
  };
  if (node.kind) {
    selection.kind = node.kind;
  }
  if (node.ticker) {
    selection.ticker = node.ticker;
  }
  return selection;
}

/**
 * Build a {@link FollowSelection} from a registry hit (a Show-more row or an
 * Add-your-own resolved match — an {@link EntityResult}). `id` is the registry
 * entity id; `type` is always `'entity'` (the registry holds only entities).
 *
 * @param params.entity - The resolved registry entity.
 * @param params.path - The ancestry trail the user reached it by.
 * @param params.source - `'more'` for Show-more, `'custom'` for an Add-your-own pick.
 * @returns The selection payload.
 */
export function selectionFromEntity(params: {
  entity: EntityResult;
  path: string[];
  source: FollowSource;
}): FollowSelection {
  const { entity, path, source } = params;
  const selection: FollowSelection = {
    followId: entity.id,
    label: entity.label,
    path,
    type: "entity",
    kind: entity.kind,
    source,
    canonicalKey: canonicalKeyFor({
      type: "entity",
      kind: entity.kind,
      ticker: entity.ticker,
      label: entity.label,
    }),
  };
  if (entity.ticker) {
    selection.ticker = entity.ticker;
  }
  return selection;
}

/**
 * Build a free-text custom {@link FollowSelection} for an Add-your-own miss (the
 * typed value resolved to no registry entity; spec §6 — still a valid follow). Its
 * `followId` is path-derived from the parent set so it's stable + non-colliding, and
 * its `kind` is the picker-only marker `'freetext'`.
 *
 * @param params.label - The trimmed free-text the user typed.
 * @param params.setId - The id of the set the custom was added under.
 * @param params.path - The ancestry trail (parent path + the custom label).
 * @returns The custom selection (`source:'custom'`, `kind:'freetext'`).
 */
export function selectionFromFreeText(params: { label: string; setId: string; path: string[] }): FollowSelection {
  const { label, setId, path } = params;
  return {
    followId: `${setId}/${slug(label)}`,
    label,
    path,
    type: "entity",
    kind: "freetext",
    source: "custom",
    canonicalKey: canonicalKeyFor({ type: "entity", kind: "freetext", label }),
  };
}

/**
 * A subscribable selection store keyed by `followId`, with canonical dedupe. NOT a
 * React hook — a plain object the page owns and shares; components read it via
 * `useSyncExternalStore`. State here (not the DOM) is the source of truth for
 * preserve-on-collapse, so a child selection survives its parent's unmount.
 *
 * Dedupe model: the store keeps ONE entry per {@link CanonicalKey}. Toggling a
 * `followId` whose canonical key already exists under a DIFFERENT id APPENDS the new
 * `path` to the existing entry (and records the alternate `followId`) rather than
 * adding a second entry. `count()`/`all()` therefore report canonical follows.
 */
class FollowSelectionStore implements SelectionStore {
  /** Canonical key → the single canonical selection (carrying all its paths). */
  private readonly byCanonical = new Map<CanonicalKey, FollowSelection>();
  /** followId → canonical key, so `has`/`toggle` can resolve a tapped id fast. */
  private readonly canonicalByFollowId = new Map<string, CanonicalKey>();
  /** Subscribers (React `useSyncExternalStore` listeners). */
  private readonly listeners = new Set<() => void>();
  /** Immutable snapshot rebuilt on every mutation (stable identity between them). */
  private snapshot: readonly FollowSelection[] = [];

  /**
   * Toggle a selection. Returns the resulting selected state for that `followId`
   * (`true` = now selected, `false` = now removed). See {@link SelectionStore.toggle}.
   */
  toggle(selection: FollowSelection): boolean {
    const existingCanonical = this.canonicalByFollowId.get(selection.followId);
    if (existingCanonical !== undefined) {
      // This exact followId was selected → remove it. If it was the only id backing
      // its canonical entry, drop the entry; else just unrecord this followId/path.
      this.removeFollowId(selection.followId, existingCanonical);
      this.commit();
      return false;
    }

    const canonical = selection.canonicalKey;
    const existing = this.byCanonical.get(canonical);
    if (existing) {
      // Same real-world entity reached via a NEW path → append the path, keep ONE
      // entry. The first-seen entry stays primary; the alternate path/id are recorded.
      const extraPaths = existing.extraPaths ? [...existing.extraPaths] : [];
      extraPaths.push(selection.path);
      this.byCanonical.set(canonical, { ...existing, extraPaths });
      this.canonicalByFollowId.set(selection.followId, canonical);
      logger.info("follow_selection_path_appended", {
        canonical_key: canonical,
        follow_id: selection.followId,
        total_paths: 1 + extraPaths.length,
      });
    } else {
      // First time this canonical identity is selected → new entry.
      this.byCanonical.set(canonical, selection);
      this.canonicalByFollowId.set(selection.followId, canonical);
    }
    this.commit();
    return true;
  }

  /** Remove a followId from its canonical entry, dropping the entry if it was last. */
  private removeFollowId(followId: string, canonical: CanonicalKey): void {
    this.canonicalByFollowId.delete(followId);
    const stillBacked = [...this.canonicalByFollowId.values()].includes(canonical);
    if (stillBacked) {
      return; // Another path/id still backs this canonical follow → keep it.
    }
    this.byCanonical.delete(canonical);
  }

  /** True when this exact `followId` is currently selected. */
  has(followId: string): boolean {
    return this.canonicalByFollowId.has(followId);
  }

  /** True when ANY active selection shares this canonical identity. */
  hasCanonical(canonicalKey: CanonicalKey): boolean {
    return this.byCanonical.has(canonicalKey);
  }

  /** All active canonical selections (one per real-world follow). */
  all(): FollowSelection[] {
    return [...this.byCanonical.values()];
  }

  /** Count of active canonical selections (the tray total). */
  count(): number {
    return this.byCanonical.size;
  }

  /** Subscribe to mutations; returns an unsubscribe fn. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Stable snapshot for `useSyncExternalStore` (new identity on each mutation). */
  getSnapshot(): readonly FollowSelection[] {
    return this.snapshot;
  }

  /** Rebuild the snapshot and notify subscribers after a mutation. */
  private commit(): void {
    this.snapshot = [...this.byCanonical.values()];
    for (const listener of this.listeners) {
      listener();
    }
  }
}

/**
 * Create a fresh selection store (SP4's `OnboardingPicker` owns one per picker
 * session; tests create one per case). A factory — not a module singleton — so
 * sessions/tests never bleed state into each other.
 *
 * @returns A new {@link SelectionStore}.
 */
export function createSelectionStore(): SelectionStore {
  return new FollowSelectionStore();
}
