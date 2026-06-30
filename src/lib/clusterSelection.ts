/**
 * Opt-out cluster-selection state model (Phase FSR-M6a SP2) — the PURE selection
 * reducer over (clusters, members, pre-selected cluster ids, individual toggles).
 *
 * Onboarding is OPT-OUT (User Story 12): recommended clusters arrive PRE-SELECTED so
 * the user DESELECTS what they don't want, rather than hand-picking ~90 accounts.
 * Selecting a cluster bulk-follows all its members; deselecting bulk-unfollows them —
 * EXCEPT members the user individually kept stay followed (the PRD edge case). The
 * resolved set is the deduped `{ sources, personalities }` SP4 persists.
 *
 * No I/O, no React — a plain immutable state value + transition functions, so it is
 * unit-tested directly (Rule 9: the deselect-keeps-individual-member + zero-cluster
 * cases are the load-bearing tests). The UI (SP3) holds one {@link ClusterSelection}
 * in state and calls these transitions.
 *
 * ── The model ────────────────────────────────────────────────────────────────
 * A member is identified by its kind + followable id (a {@link MemberRef}). The state
 * carries:
 *   - `selectedClusterIds` — clusters whose membership is "bulk-on";
 *   - `memberOverrides` — per-member explicit on/off that WINS over its clusters'
 *     bulk state (so a member kept after its cluster is deselected stays on, and a
 *     member removed from a selected cluster stays off).
 * A member is FOLLOWED iff: its override is `on`, OR (no override AND it belongs to a
 * selected cluster). This single rule yields every edge case below without special-casing.
 */

import type { ResolvedCluster } from "@/lib/sourceClusters";

/** A stable reference to one rendered followable (the dedup + override key). */
export interface MemberRef {
  /** `source` (a content_sources row) | `personality` (a personalities row). */
  kind: "source" | "personality";
  /** The underlying `source_id` / `personality_id`. */
  followable_id: string;
}

/**
 * The opt-out selection state. Immutable — every transition returns a NEW value
 * (the UI swaps it into React state). `clusters` is the resolved input the
 * transitions read (cluster→member membership); the two sets/maps are the mutable
 * selection on top of it.
 */
export interface ClusterSelection {
  /** The resolved clusters this selection is over (read-only input; carries membership). */
  readonly clusters: readonly ResolvedCluster[];
  /** Cluster slugs currently bulk-selected. */
  readonly selectedClusterSlugs: ReadonlySet<string>;
  /**
   * Per-member explicit override that WINS over cluster bulk state, keyed by
   * {@link memberKey}. `true` = individually kept on; `false` = individually removed.
   */
  readonly memberOverrides: ReadonlyMap<string, boolean>;
}

/** The deduped follow set {@link resolveFollowSet} produces — what SP4 persists. */
export interface ResolvedFollowSet {
  /** `content_sources.source_id`s to write to `user_content_sources`. */
  sources: string[];
  /** `personalities.personality_id`s to write to `user_personalities`. */
  personalities: string[];
}

/** The override-map key for a member — `kind:id` so a source + personality can't collide. */
function memberKey(ref: MemberRef): string {
  return `${ref.kind}:${ref.followable_id}`;
}

/**
 * Build the INITIAL opt-out selection: the recommended clusters are PRE-SELECTED, so
 * their members start followed (User Story 12 — opt-out, not opt-in). No member
 * overrides yet — the user hasn't individually touched anything.
 *
 * A recommended slug not present in `clusters` is ignored (the resolver may have
 * dropped an empty/un-curated cluster). Selecting ZERO recommended clusters is valid
 * (User Story 21 — the user can opt out of all; the feed still works via news).
 *
 * @param clusters - The resolved clusters for the chosen categories (SP1 output).
 * @param recommendedClusterSlugs - The editorially-recommended cluster slugs to pre-select.
 * @returns The initial {@link ClusterSelection}.
 */
export function buildInitialSelection(
  clusters: readonly ResolvedCluster[],
  recommendedClusterSlugs: readonly string[],
): ClusterSelection {
  const availableSlugs = new Set(clusters.map((c) => c.cluster_slug));
  const selected = new Set(recommendedClusterSlugs.filter((slug) => availableSlugs.has(slug)));
  return {
    clusters,
    selectedClusterSlugs: selected,
    memberOverrides: new Map(),
  };
}

/**
 * Toggle a whole cluster's bulk-selection. Selecting it bulk-follows its members;
 * deselecting it bulk-unfollows them — but PRESERVES per-member overrides, so a
 * member the user INDIVIDUALLY KEPT stays followed after its cluster is deselected
 * (the PRD edge case — User Story 12: "deselecting a cluster after individually
 * keeping one member leaves that member followed").
 *
 * The override map is left untouched: it always wins over bulk state in
 * {@link isMemberFollowed}, so an explicit keep survives a deselect and an explicit
 * removal survives a (re)select. The cluster toggle only flips the BULK signal; the
 * user's individual intent is sticky until they flip it again. This is the simplest
 * rule that satisfies the opt-out edge cases (Rule 2).
 *
 * @param state - The current selection.
 * @param clusterSlug - The cluster to toggle.
 * @returns A NEW selection with the cluster's bulk state flipped (overrides preserved).
 */
export function toggleCluster(state: ClusterSelection, clusterSlug: string): ClusterSelection {
  const selected = new Set(state.selectedClusterSlugs);
  if (selected.has(clusterSlug)) {
    selected.delete(clusterSlug);
  } else {
    selected.add(clusterSlug);
  }
  return { ...state, selectedClusterSlugs: selected };
}

/**
 * Toggle a single member's follow state, independent of its cluster(s). Sets an
 * explicit override that WINS over the cluster bulk state: keeping a member after its
 * cluster is deselected leaves it followed (the PRD edge case); removing a member
 * from a selected cluster leaves it unfollowed.
 *
 * The override is set to the OPPOSITE of the member's current effective follow state,
 * so one tap always flips what the user sees.
 *
 * @param state - The current selection.
 * @param ref - The member to flip.
 * @returns A NEW selection with the member's override set to its flipped state.
 */
export function toggleMember(state: ClusterSelection, ref: MemberRef): ClusterSelection {
  const overrides = new Map(state.memberOverrides);
  const nextFollowed = !isMemberFollowed(state, ref);
  overrides.set(memberKey(ref), nextFollowed);
  return { ...state, memberOverrides: overrides };
}

/**
 * Whether a member is currently followed under the opt-out rule: its override wins,
 * else it follows iff it belongs to ANY selected cluster.
 *
 * @param state - The current selection.
 * @param ref - The member to test.
 * @returns `true` when the member is in the resolved follow set.
 */
export function isMemberFollowed(state: ClusterSelection, ref: MemberRef): boolean {
  const override = state.memberOverrides.get(memberKey(ref));
  if (override !== undefined) {
    return override;
  }
  return isMemberInSelectedCluster(state, ref);
}

/** Whether a member belongs to any currently-selected cluster (the bulk-on signal). */
function isMemberInSelectedCluster(state: ClusterSelection, ref: MemberRef): boolean {
  const key = memberKey(ref);
  for (const cluster of state.clusters) {
    if (!state.selectedClusterSlugs.has(cluster.cluster_slug)) {
      continue;
    }
    if (cluster.members.some((m) => memberKey(m) === key)) {
      return true;
    }
  }
  return false;
}

/**
 * Whether a cluster is currently bulk-selected (drives the cluster card's
 * `aria-pressed` / pre-selected render in SP3).
 *
 * @param state - The current selection.
 * @param clusterSlug - The cluster to test.
 * @returns `true` when the cluster is in `selectedClusterSlugs`.
 */
export function isClusterSelected(state: ClusterSelection, clusterSlug: string): boolean {
  return state.selectedClusterSlugs.has(clusterSlug);
}

/**
 * Resolve the deduped follow set to persist (SP4): every member that is followed
 * under the opt-out rule, partitioned into `sources` (→ `user_content_sources`) and
 * `personalities` (→ `user_personalities`), each deduped (a member shared by two
 * selected clusters appears once).
 *
 * Picking ZERO clusters with no overrides yields an empty set — allowed (User Story
 * 21; the feed still works via shared-backbone news).
 *
 * @param state - The current selection.
 * @returns The deduped {@link ResolvedFollowSet}.
 */
export function resolveFollowSet(state: ClusterSelection): ResolvedFollowSet {
  const sourceIds = new Set<string>();
  const personalityIds = new Set<string>();

  // Walk every member of every cluster once; the followed rule (override-or-bulk)
  // decides inclusion. A Set dedups a member that lives in two selected clusters.
  for (const cluster of state.clusters) {
    for (const member of cluster.members) {
      if (!isMemberFollowed(state, member)) {
        continue;
      }
      if (member.kind === "source") {
        sourceIds.add(member.followable_id);
      } else {
        personalityIds.add(member.followable_id);
      }
    }
  }

  return { sources: [...sourceIds], personalities: [...personalityIds] };
}
