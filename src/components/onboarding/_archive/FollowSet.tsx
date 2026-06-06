"use client";

/**
 * FollowSet — the recursive set unit of the interest picker (Phase 5 SP3). One
 * labeled group of bubbles that ALWAYS provides (spec §4): an eyebrow label,
 * **Select all**, a chip grid, and **Add your own**; entity sets additionally get
 * **Show more** (gated by the set's `registry` pointer). Selecting a chip whose node
 * carries child `sets` lazily mounts those sets directly beneath it (recursive); the
 * selection store — not the DOM — holds state, so deselecting collapses the children
 * yet preserves their internal selections (spec §11 preserve-on-collapse).
 *
 * Show-more / Add-your-own wire to SP2's `listEntities`/`searchEntities`; on error or
 * offline they fall back to the lifted `moreSeeds` / free-text (spec §11). Styling is
 * self-contained via spec §8 hex tokens (no globals.css/tailwind edits — out of scope).
 */

import { type CSSProperties, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { FollowChip } from "@/components/onboarding/_archive/FollowChip";
import { type EntityResult, listEntities, searchEntities } from "@/lib/entities";
import { selectionFromEntity, selectionFromFreeText, selectionFromNode } from "@/lib/followSets";
import { logger } from "@/lib/logger";
import type { PickerFollowSet, PickerNode, SelectionStore } from "@/types/picker";

/** Spec §8 tokens reused by the set chrome (eyebrow, mini buttons, nesting rule). */
const TOKENS = {
  ink: "#1b1a17",
  muted: "#6f6a5e",
  line: "#dcd6c8",
  card: "#fffdf7",
  sel: "#3a5a40",
  bg: "#f4f1ea",
} as const;
const MONO_FONT_STACK = '"Spline Sans Mono", ui-monospace, SFMono-Regular, Menlo, monospace';
const BODY_FONT_STACK = '"Spline Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
/** Add-your-own debounce (spec §6 "debounces the input and calls search"). */
const SEARCH_DEBOUNCE_MS = 250;

export interface FollowSetProps {
  /** The lifted follow-set to render. */
  followSet: PickerFollowSet;
  /** Ancestry trail to this set `[Category, …]` — each chip appends its own label. */
  path: string[];
  /** The shared selection store (owned by the page; read via useSyncExternalStore). */
  store: SelectionStore;
  /** True when this set is rendered nested under a selected chip (adds the left rule). */
  nested?: boolean;
}

/**
 * Subscribe a component to the store so it re-renders whenever any selection changes.
 * The snapshot identity changes on each mutation, so React re-renders the subtree.
 */
function useStoreSnapshot(store: SelectionStore): readonly unknown[] {
  return useSyncExternalStore(
    (listener) => store.subscribe(listener),
    () => store.getSnapshot(),
    () => store.getSnapshot(),
  );
}

/** Small outlined mono "pill" button (Select all / Show more) — spec §8 set chrome. */
function MiniButton({ label, onClick, dataAttr }: { label: string; onClick: () => void; dataAttr: string }) {
  const style: CSSProperties = {
    fontFamily: MONO_FONT_STACK,
    fontSize: 10.5,
    letterSpacing: ".04em",
    textTransform: "uppercase",
    background: "none",
    border: `1px solid ${TOKENS.line}`,
    color: TOKENS.muted,
    borderRadius: 999,
    padding: "3px 9px",
    minHeight: 44,
    cursor: "pointer",
  };
  // Reason: spread the data attr key dynamically so each control has a stable hook.
  const dataProps = { [dataAttr]: "" } as Record<string, string>;
  return (
    <button type="button" onClick={onClick} style={style} {...dataProps}>
      {label}
    </button>
  );
}

/**
 * Render one follow-set and (recursively) the nested sets of its selected chips.
 *
 * @param props - {@link FollowSetProps}.
 */
export function FollowSet({ followSet, path, store, nested = false }: FollowSetProps) {
  // Subscribe so chip selected-states + nested mounts re-render on any store change.
  useStoreSnapshot(store);

  // Entities revealed by Show-more (live registry pages OR the offline moreSeeds),
  // appended to the seed items. Kept in component state — these are ephemeral to the
  // session view; the persisted follow set lives in the store.
  const [extraEntities, setExtraEntities] = useState<EntityResult[]>([]);
  // Keyset cursor for the next Show-more page; null once the registry is exhausted.
  const [showMoreCursor, setShowMoreCursor] = useState<string | null>(null);
  // True when the registry exhausted (live nextCursor===null) OR the offline fallback fired.
  const [showMoreExhausted, setShowMoreExhausted] = useState(false);

  // Add-your-own state.
  const [customQuery, setCustomQuery] = useState("");
  const [searchHits, setSearchHits] = useState<EntityResult[]>([]);

  const registry = followSet.registry;
  const hasMore = registry !== undefined || (followSet.moreSeeds?.length ?? 0) > 0;

  /** Ids already mounted as chips (seed items + previously-revealed extras) — for dedupe. */
  const mountedIds = useRef<Set<string>>(new Set());
  useEffect(() => {
    const ids = new Set<string>();
    for (const item of followSet.items) {
      ids.add(item.id);
    }
    for (const extra of extraEntities) {
      ids.add(extra.id);
    }
    mountedIds.current = ids;
  }, [followSet.items, extraEntities]);

  /** Append the offline `moreSeeds` (deduped) when the live registry is unavailable. */
  const appendOfflineFallback = useCallback(() => {
    const seeds = followSet.moreSeeds ?? [];
    const deduped = seeds
      .filter((seed) => !mountedIds.current.has(seed.id))
      .map<EntityResult>((seed) => ({
        id: seed.id,
        label: seed.label,
        kind: seed.kind ?? "company",
        ...(seed.ticker ? { ticker: seed.ticker } : {}),
      }));
    setExtraEntities((prev) => [...prev, ...deduped]);
    setShowMoreExhausted(true); // moreSeeds is a single static page → exhausted after.
  }, [followSet.moreSeeds]);

  /** Show-more: fetch the next live page (or fall back offline), dedupe, append. */
  const handleShowMore = useCallback(async () => {
    if (!registry) {
      // Pure-topic set with only moreSeeds (rare) → reveal them once.
      appendOfflineFallback();
      return;
    }
    try {
      const page = await listEntities({
        parent: registry.parent,
        kind: registry.kind,
        cursor: showMoreCursor ?? undefined,
      });
      // Dedupe live rows against already-mounted ids (seed top-N may overlap).
      const fresh = page.results.filter((row) => !mountedIds.current.has(row.id));
      setExtraEntities((prev) => [...prev, ...fresh]);
      setShowMoreCursor(page.nextCursor);
      if (page.nextCursor === null) {
        setShowMoreExhausted(true); // No more pages → hide the control (spec §4).
      }
    } catch (error) {
      // Registry offline/error → fall back to the lifted seeds (spec §11).
      logger.warn("follow_set_show_more_fallback", {
        set_id: followSet.id,
        error_message: error instanceof Error ? error.message : "unknown",
        fix_suggestion: "Registry unreachable; appended offline moreSeeds fallback.",
      });
      appendOfflineFallback();
    }
  }, [registry, showMoreCursor, followSet.id, appendOfflineFallback]);

  // Debounced Add-your-own search (spec §6). Empty query → clear suggestions.
  useEffect(() => {
    const trimmed = customQuery.trim();
    if (trimmed === "") {
      setSearchHits([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const hits = await searchEntities({
          q: trimmed,
          ...(registry?.kind ? { kind: registry.kind } : {}),
          ...(registry?.parent ? { parent: registry.parent } : {}),
        });
        if (!cancelled) {
          setSearchHits(hits);
        }
      } catch (error) {
        // Search offline → no suggestions; the user can still submit free text.
        logger.warn("follow_set_search_failed", {
          set_id: followSet.id,
          error_message: error instanceof Error ? error.message : "unknown",
          fix_suggestion: "Registry search unreachable; free-text submit still works.",
        });
        if (!cancelled) {
          setSearchHits([]);
        }
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [customQuery, registry?.kind, registry?.parent, followSet.id]);

  /** Toggle a SEED chip (a lifted node, possibly with child sets). */
  const toggleSeedNode = useCallback(
    (node: PickerNode) => {
      store.toggle(selectionFromNode({ node, path: [...path, node.label], source: "seed" }));
    },
    [store, path],
  );

  /** Toggle a Show-more entity row. */
  const toggleExtraEntity = useCallback(
    (entity: EntityResult) => {
      store.toggle(selectionFromEntity({ entity, path: [...path, entity.label], source: "more" }));
    },
    [store, path],
  );

  /** Pick a search suggestion → store as a custom-resolved entity follow. */
  const pickSuggestion = useCallback(
    (entity: EntityResult) => {
      store.toggle(selectionFromEntity({ entity, path: [...path, entity.label], source: "custom" }));
      setCustomQuery("");
      setSearchHits([]);
    },
    [store, path],
  );

  /** Submit Add-your-own: pick the top hit if present, else store free text (spec §6). */
  const submitCustom = useCallback(() => {
    const trimmed = customQuery.trim().slice(0, 80); // spec §11 "trim/validate sensibly".
    if (trimmed === "") {
      return;
    }
    if (searchHits.length > 0) {
      pickSuggestion(searchHits[0]);
      return;
    }
    // No registry match → a valid free-text follow (spec §6).
    store.toggle(selectionFromFreeText({ label: trimmed, setId: followSet.id, path: [...path, trimmed] }));
    setCustomQuery("");
    setSearchHits([]);
  }, [customQuery, searchHits, pickSuggestion, store, followSet.id, path]);

  /** Select all: turn every CURRENTLY-MOUNTED chip on, or off if all are already on. */
  const handleSelectAll = useCallback(() => {
    const seedSelections = followSet.items.map((node) =>
      selectionFromNode({ node, path: [...path, node.label], source: "seed" }),
    );
    const extraSelections = extraEntities.map((entity) =>
      selectionFromEntity({ entity, path: [...path, entity.label], source: "more" }),
    );
    const all = [...seedSelections, ...extraSelections];
    // Turn ON if ANY is currently off (matches the prototype's `all.onclick`).
    const turnOn = all.some((selection) => !store.has(selection.followId));
    for (const selection of all) {
      const isOn = store.has(selection.followId);
      if (isOn !== turnOn) {
        store.toggle(selection);
      }
    }
  }, [followSet.items, extraEntities, path, store]);

  const setStyle: CSSProperties = {
    margin: nested ? "10px 0 4px" : "10px 0 4px",
    marginLeft: nested ? 14 : undefined,
    paddingLeft: nested ? 14 : 2,
    borderLeft: nested ? `2px solid ${TOKENS.line}` : undefined,
  };

  return (
    <div data-follow-set="" data-set-id={followSet.id} style={setStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "6px 0", flexWrap: "wrap" }}>
        <span
          data-set-label=""
          style={{
            fontFamily: MONO_FONT_STACK,
            fontSize: 11,
            letterSpacing: ".06em",
            textTransform: "uppercase",
            color: TOKENS.muted,
          }}
        >
          {followSet.label}
        </span>
        <MiniButton label="Select all" onClick={handleSelectAll} dataAttr="data-select-all" />
        {hasMore && !showMoreExhausted ? (
          <MiniButton
            label="+ Show more"
            onClick={() => {
              void handleShowMore();
            }}
            dataAttr="data-show-more"
          />
        ) : null}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 7, margin: "4px 0" }}>
        {followSet.items.map((node) => {
          const selected = store.has(node.id);
          return (
            <span key={node.id} style={{ display: "inline-flex", flexDirection: "column" }}>
              <FollowChip
                followId={node.id}
                label={node.label}
                ticker={node.ticker}
                selected={selected}
                onToggle={() => toggleSeedNode(node)}
              />
              {/* Lazy mount: a selected chip with child sets reveals them; deselect
                  collapses (the store still holds the child selections — §11). */}
              {selected && node.sets
                ? node.sets.map((childSet) => (
                    <FollowSet
                      key={childSet.id}
                      followSet={childSet}
                      path={[...path, node.label]}
                      store={store}
                      nested
                    />
                  ))
                : null}
            </span>
          );
        })}
        {extraEntities.map((entity) => (
          <FollowChip
            key={entity.id}
            followId={entity.id}
            label={entity.label}
            ticker={entity.ticker}
            selected={store.has(entity.id)}
            onToggle={() => toggleExtraEntity(entity)}
          />
        ))}
      </div>

      {/* Add your own (spec §4/§6) — always present (allowCustom is always true). */}
      <div
        data-add-your-own=""
        style={{ display: "flex", gap: 6, margin: "7px 0 2px", maxWidth: 340, flexWrap: "wrap" }}
      >
        <input
          data-add-input=""
          value={customQuery}
          placeholder="Add your own…"
          onChange={(event) => setCustomQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              submitCustom();
            }
          }}
          maxLength={80}
          style={{
            flex: 1,
            minHeight: 44,
            fontFamily: BODY_FONT_STACK,
            fontSize: 13,
            padding: "6px 11px",
            border: `1.5px solid ${TOKENS.line}`,
            borderRadius: 999,
            background: TOKENS.card,
          }}
        />
        <button
          type="button"
          data-add-submit=""
          onClick={submitCustom}
          style={{
            fontFamily: MONO_FONT_STACK,
            fontSize: 11,
            textTransform: "uppercase",
            minHeight: 44,
            border: `1.5px solid ${TOKENS.ink}`,
            background: TOKENS.ink,
            color: TOKENS.bg,
            borderRadius: 999,
            padding: "0 14px",
            cursor: "pointer",
          }}
        >
          Add
        </button>
      </div>

      {/* Search suggestions (spec §6) — shown while typing resolves registry hits. */}
      {searchHits.length > 0 ? (
        <div data-search-suggestions="" style={{ display: "flex", flexWrap: "wrap", gap: 7, margin: "2px 0 4px" }}>
          {searchHits.map((hit) => (
            <FollowChip
              key={hit.id}
              followId={hit.id}
              label={hit.label}
              ticker={hit.ticker}
              selected={store.has(hit.id)}
              isCustom
              onToggle={() => pickSuggestion(hit)}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
