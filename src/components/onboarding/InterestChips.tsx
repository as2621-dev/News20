"use client";

/**
 * InterestChips — the 3-level interest-chip onboarding tree (Phase 1e SP3).
 *
 * Renders the `interests` taxonomy with **lazy child expansion**: depth-0
 * categories load on mount ({@link fetchRootInterests}); tapping a chip fetches
 * and reveals its direct children ({@link fetchChildInterests}) — depth-1 under a
 * depth-0 tap, depth-2 under a depth-1 tap. Each selected node carries a per-node
 * **strict toggle** ("just give me cricket, nothing broader" →
 * `profile_is_strict`). A {@link CustomInterestChip} adds free-text customs.
 *
 * SP3 is selection-only: NO DB write. The full in-memory selection (taxonomy
 * picks + strict flags + customs) is surfaced via
 * {@link InterestChipsProps.onSelectionChange} so SP4 can persist it to
 * `user_interest_profile`.
 *
 * Visual register matches the reel/onboarding surface (`EmailSignIn`,
 * `TapToStart`): near-black canvas, soft pill chips, Inter + JetBrains Mono chrome.
 */

import { useCallback, useEffect, useState } from "react";
import { CustomInterestChip } from "@/components/onboarding/CustomInterestChip";
import { fetchChildInterests, fetchRootInterests, type Interest } from "@/lib/interests";
import { logger } from "@/lib/logger";

/**
 * A picked taxonomy interest in the pending (un-persisted) selection. Mirrors the
 * `user_interest_profile` columns SP4 writes: the interest id, the strict flag,
 * and the resolved depth (so SP4 can weight depth-2 leaves vs depth-0 categories,
 * phase Open Q1). Defined here (not a shared type) to honour SP3's scope lock.
 */
export interface SelectedTaxonomyInterest {
  selection_kind: "taxonomy";
  interest_id: string;
  interest_label: string;
  depth_level: number;
  profile_is_strict: boolean;
}

/**
 * A pending free-text custom interest. Carries `interest_kind: "custom"` and the
 * typed label; it has no `interest_id` yet — SP4 canonicalizes/persists it
 * (phase Open Q2: flat custom node for v1).
 */
export interface SelectedCustomInterest {
  selection_kind: "custom";
  interest_kind: "custom";
  custom_label: string;
}

/** The full pending selection InterestChips surfaces to SP4. */
export interface InterestSelection {
  taxonomy_selections: SelectedTaxonomyInterest[];
  custom_selections: SelectedCustomInterest[];
}

export interface InterestChipsProps {
  /**
   * Fired whenever the pending selection changes (a chip picked/unpicked, a
   * strict toggle flipped, or a custom added). SP4 wires this to persist the
   * profile — SP3 itself writes nothing.
   */
  onSelectionChange?: (selection: InterestSelection) => void;
}

/**
 * Render an interest chip's "strict" toggle. Compact, only shown for a selected
 * chip — strictness is meaningless on an unpicked interest.
 */
function StrictToggle({
  interestLabel,
  isStrict,
  onToggle,
}: {
  interestLabel: string;
  isStrict: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={isStrict}
      aria-label={`Only ${interestLabel}, nothing broader`}
      className={`rounded-pill border px-2.5 py-1 font-mono text-[10px] tracking-wide transition-colors ${
        isStrict
          ? "border-caption-highlight/60 bg-caption-highlight/15 text-caption-highlight"
          : "border-white/15 bg-white/5 text-white/45"
      }`}
    >
      ONLY THIS
    </button>
  );
}

/**
 * Render the interest-chip onboarding tree.
 *
 * @param props - {@link InterestChipsProps}.
 */
export function InterestChips({ onSelectionChange }: InterestChipsProps) {
  // Loaded nodes, keyed by id, so children fetched lazily accumulate in one map.
  const [interestsById, setInterestsById] = useState<Record<string, Interest>>({});
  // The id-ordered list per parent ("__root__" for depth-0), for stable render order.
  const [childIdsByParent, setChildIdsByParent] = useState<Record<string, string[]>>({});
  // Which expandable chips are currently open (their children revealed).
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  // Parents whose children are mid-fetch (avoid duplicate round-trips + show state).
  const [loadingParentIds, setLoadingParentIds] = useState<Set<string>>(new Set());
  // Selected taxonomy picks: interest_id → strict flag.
  const [selectedStrictById, setSelectedStrictById] = useState<Record<string, boolean>>({});
  // Pending custom free-text selections (no interest_id until SP4 persists).
  const [customLabels, setCustomLabels] = useState<string[]>([]);
  const [rootLoadError, setRootLoadError] = useState<string | null>(null);

  const ROOT_KEY = "__root__";

  // Load depth-0 roots once on mount.
  useEffect(() => {
    let cancelled = false;
    async function loadRoots() {
      try {
        const roots = await fetchRootInterests();
        if (cancelled) {
          return;
        }
        setInterestsById((prev) => {
          const next = { ...prev };
          for (const root of roots) {
            next[root.interest_id] = root;
          }
          return next;
        });
        setChildIdsByParent((prev) => ({ ...prev, [ROOT_KEY]: roots.map((root) => root.interest_id) }));
      } catch (error) {
        if (cancelled) {
          return;
        }
        const message = error instanceof Error ? error.message : "Unknown error loading interests.";
        logger.error("interest_chips_root_load_failed", {
          error_message: message,
          fix_suggestion: "Confirm migration 0003 applied and interests are seeded (anon SELECT).",
        });
        setRootLoadError(message);
      }
    }
    void loadRoots();
    return () => {
      cancelled = true;
    };
  }, []);

  // Surface the selection upward whenever any part of it changes.
  useEffect(() => {
    if (!onSelectionChange) {
      return;
    }
    const taxonomy_selections: SelectedTaxonomyInterest[] = Object.entries(selectedStrictById).map(
      ([interest_id, profile_is_strict]) => {
        const interest = interestsById[interest_id];
        return {
          selection_kind: "taxonomy",
          interest_id,
          interest_label: interest?.interest_label ?? interest_id,
          depth_level: interest?.depth_level ?? 0,
          profile_is_strict,
        };
      },
    );
    const custom_selections: SelectedCustomInterest[] = customLabels.map((custom_label) => ({
      selection_kind: "custom",
      interest_kind: "custom",
      custom_label,
    }));
    onSelectionChange({ taxonomy_selections, custom_selections });
  }, [selectedStrictById, customLabels, interestsById, onSelectionChange]);

  /** Lazily fetch a node's direct children once, caching them. */
  const loadChildren = useCallback(
    async (parentInterestId: string) => {
      if (childIdsByParent[parentInterestId] !== undefined || loadingParentIds.has(parentInterestId)) {
        return;
      }
      setLoadingParentIds((prev) => new Set(prev).add(parentInterestId));
      try {
        const children = await fetchChildInterests(parentInterestId);
        setInterestsById((prev) => {
          const next = { ...prev };
          for (const child of children) {
            next[child.interest_id] = child;
          }
          return next;
        });
        setChildIdsByParent((prev) => ({ ...prev, [parentInterestId]: children.map((child) => child.interest_id) }));
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown error loading child interests.";
        logger.error("interest_chips_children_load_failed", {
          parent_interest_id: parentInterestId,
          error_message: message,
          fix_suggestion: "Confirm migration 0003 applied and interests are seeded (anon SELECT).",
        });
      } finally {
        setLoadingParentIds((prev) => {
          const next = new Set(prev);
          next.delete(parentInterestId);
          return next;
        });
      }
    },
    [childIdsByParent, loadingParentIds],
  );

  /** Tapping a chip: toggle its selection AND lazily reveal its children. */
  function handleChipTap(interest: Interest) {
    setSelectedStrictById((prev) => {
      const next = { ...prev };
      if (interest.interest_id in next) {
        // Reason: unpicking also drops the strict flag — strictness is a property
        // of an active selection, not a sticky preference on a deselected node.
        delete next[interest.interest_id];
      } else {
        next[interest.interest_id] = false;
      }
      return next;
    });
    // Expand to reveal children (depth-2 is the deepest seeded level; a leaf simply
    // fetches zero children and shows nothing).
    setExpandedIds((prev) => new Set(prev).add(interest.interest_id));
    void loadChildren(interest.interest_id);
  }

  /** Flip the strict flag on an already-selected chip. */
  function handleStrictToggle(interestId: string) {
    setSelectedStrictById((prev) => {
      if (!(interestId in prev)) {
        return prev;
      }
      return { ...prev, [interestId]: !prev[interestId] };
    });
  }

  /** Add a pending custom free-text interest (deduped, no DB write — SP4 persists). */
  function handleAddCustom(label: string) {
    setCustomLabels((prev) => {
      if (prev.some((existing) => existing.toLowerCase() === label.toLowerCase())) {
        return prev;
      }
      logger.info("interest_chips_custom_added", { custom_label: label });
      return [...prev, label];
    });
  }

  /** Recursively render a node and (when expanded) its loaded children. */
  function renderNode(interestId: string, depth: number) {
    const interest = interestsById[interestId];
    if (!interest) {
      return null;
    }
    const isSelected = interestId in selectedStrictById;
    const isStrict = selectedStrictById[interestId] === true;
    const isExpanded = expandedIds.has(interestId);
    const isLoadingChildren = loadingParentIds.has(interestId);
    const childIds = childIdsByParent[interestId];

    return (
      <div
        key={interestId}
        className="flex flex-col gap-1.5"
        style={{ marginLeft: depth > 0 ? depth * 14 : undefined }}
      >
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => handleChipTap(interest)}
            aria-pressed={isSelected}
            className={`rounded-pill border px-4 py-2 font-sans text-[14px] transition-colors ${
              isSelected
                ? "border-white bg-white text-background"
                : "border-white/20 bg-white/5 text-text-primary hover:border-white/40"
            }`}
          >
            {interest.interest_label}
          </button>
          {isSelected ? (
            <StrictToggle
              interestLabel={interest.interest_label}
              isStrict={isStrict}
              onToggle={() => handleStrictToggle(interestId)}
            />
          ) : null}
          {isLoadingChildren ? (
            <span className="font-mono text-[10px] tracking-wide text-white/35">LOADING…</span>
          ) : null}
        </div>

        {isExpanded && childIds && childIds.length > 0 ? (
          <div className="flex flex-col gap-1.5">{childIds.map((childId) => renderNode(childId, depth + 1))}</div>
        ) : null}
      </div>
    );
  }

  const rootIds = childIdsByParent[ROOT_KEY];

  return (
    <section className="flex min-h-full flex-col gap-6 px-6 py-8">
      <header className="text-center">
        <h1 className="font-sans text-[17px] font-semibold text-text-primary">What are you into?</h1>
        <p className="mt-2 font-sans text-[13px] leading-relaxed text-text-secondary">
          Pick a few. Tap one to dig deeper, or add your own.
        </p>
      </header>

      {rootLoadError ? (
        <p role="alert" className="font-mono text-[11px] tracking-wide text-seg-wildcard">
          Couldn&apos;t load interests. {rootLoadError}
        </p>
      ) : null}

      <div className="flex flex-col gap-2.5">
        {rootIds === undefined && !rootLoadError ? (
          <span className="font-mono text-[10px] tracking-wide text-white/35">LOADING INTERESTS…</span>
        ) : null}
        {(rootIds ?? []).map((rootId) => renderNode(rootId, 0))}
      </div>

      <div className="mt-2 flex flex-col gap-2.5">
        <span className="font-mono text-[10px] tracking-wide text-white/40">SOMETHING ELSE?</span>
        <CustomInterestChip onAddCustom={handleAddCustom} />
        {customLabels.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {customLabels.map((label) => (
              <span
                key={label}
                className="rounded-pill border border-caption-highlight/40 bg-caption-highlight/10 px-3 py-1.5 font-sans text-[13px] text-caption-highlight"
              >
                {label}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
