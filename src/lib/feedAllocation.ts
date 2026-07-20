/**
 * Feed-allocation data layer ("Build your 30, in order", Blip Flow Stage 3) — the typed,
 * client-side Supabase reads/writes for the per-user `user_feed_allocation` table
 * (migration `0008_feed_allocation.sql`).
 *
 * **Same client pattern as `src/lib/sources.ts`:** every exported fn takes an optional
 * `client` (injected in tests) defaulting to the shared browser anon client — there is NO
 * Next.js server runtime on device (Capacitor static export), so the REST surface ships as
 * client-side Supabase reads/writes under RLS.
 *
 * `user_feed_allocation` is OWNER-SCOPED: the `user_feed_allocation_owner_all` policy
 * (migration 0008) pins every row to `auth.uid()` via both USING and WITH CHECK, so a
 * caller can only ever touch their own allocation rows. We additionally resolve the user
 * id app-side so an UNAUTHENTICATED write throws a loud, actionable error (Rule 12) rather
 * than relying on RLS to reject an anon write with an opaque PostgREST message.
 *
 * Taxonomy (SP3): the screen draws the 10 canonical buckets (the 8 picker roots +
 * `youtube`/`x`), each of which is its own `feed_category` enum value (identity map in
 * `feedBuckets.ts`). The retired `podcasts` design bucket — and the pre-0010 "podcasts
 * enum value missing" graceful-degrade path — no longer exist, so every saved bucket has
 * a real enum value and the save has no degrade case.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import {
  ALLOCATION_TOTAL,
  type AllocationSegment,
  DESIGN_BUCKET_TO_ENUM,
  type DesignBucketId,
  ENUM_TO_DESIGN_BUCKET,
  type FeedCategoryEnum,
  isCoarseAllocationSegment,
  sumSegmentCounts,
} from "@/lib/feedBuckets";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** The `user_feed_allocation` table name — single source of truth for the table ref. */
const USER_FEED_ALLOCATION_TABLE = "user_feed_allocation";

/**
 * The column projection {@link getUserFeedAllocation} requests (DDL order, not `*`). Includes
 * the migration-0026 niche columns (`allocation_interest_id`, `allocation_section_label`) so a
 * niche/beyond-bubble SECTION row is legible to the screen as a read-only named block rather
 * than collapsing into an anonymous coarse block (slice #12 loader-projection fix).
 */
const USER_FEED_ALLOCATION_COLUMNS =
  "allocation_category,allocation_slot_count,allocation_sort_order,allocation_interest_id,allocation_section_label";

/** The shape PostgREST returns for a `user_feed_allocation` row read (TS twin of migration 0026). */
interface FeedAllocationRow {
  allocation_category: FeedCategoryEnum;
  allocation_slot_count: number;
  allocation_sort_order: number;
  /** The niche interest node a section is named for (migration 0026); NULL on a coarse row. */
  allocation_interest_id: string | null;
  /** The user-vocabulary section label (migration 0026); NULL on a coarse/source row. */
  allocation_section_label: string | null;
}

/**
 * Resolve the authed user's id, or throw a loud, actionable error when signed out
 * (mirrors `sources.ts` `requireAuthedUserId` — a feed-allocation write is an explicit
 * authed action with no graceful no-op semantics).
 *
 * @param client - The Supabase client to read the session from.
 * @returns The authed `user_id` (= `auth.uid()`).
 * @throws If unauthenticated (or the session read fails) — never returns null.
 */
async function requireAuthedUserId(client: SupabaseClient): Promise<string> {
  const { data, error } = await client.auth.getUser();
  if (error || !data.user) {
    logger.error("feed_allocation_requires_auth", {
      error_message: error?.message ?? "no_active_session",
      fix_suggestion:
        "Sign in (email magic-link) before saving a feed allocation — anon users have no allocation rows.",
    });
    throw new Error(
      "Cannot save a feed allocation while signed out. " +
        "fix_suggestion: sign in (email magic-link) before saving your 30.",
    );
  }
  return data.user.id;
}

/** One `user_feed_allocation` row to upsert (migration 0008 column shape). */
interface FeedAllocationUpsertRow {
  follow_user_id: string;
  allocation_category: FeedCategoryEnum;
  allocation_slot_count: number;
  allocation_sort_order: number;
}

/**
 * Read the authed user's saved feed allocation from `user_feed_allocation` (RLS returns
 * only the caller's rows), ordered by `allocation_sort_order`, mapped back to design
 * buckets. Used to SEED the "Build your 30" screen for a returning user.
 *
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns The caller's ordered allocation as {@link AllocationSegment}s; `[]` when none saved.
 * @throws If unauthenticated, or if the query fails (surfaced, never swallowed — Rule 12).
 *
 * @example
 * const saved = await getUserFeedAllocation();
 * saved; // [{ bucketId: "geopolitics", count: 5 }, { bucketId: "tech", count: 5 }, …]
 */
export async function getUserFeedAllocation(
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<AllocationSegment[]> {
  const authedUserId = await requireAuthedUserId(client);
  logger.info("get_user_feed_allocation_started", { user_id: authedUserId });

  // Reason: the owner predicate is redundant with RLS (the policy already pins rows to
  // auth.uid()), but pinning it explicitly mirrors sources.ts and makes the owner-scoping
  // legible at the call site. Order by the user's manual sequence so the screen rebuilds it.
  const { data, error } = await client
    .from(USER_FEED_ALLOCATION_TABLE)
    .select(USER_FEED_ALLOCATION_COLUMNS)
    .eq("follow_user_id", authedUserId)
    .order("allocation_sort_order", { ascending: true })
    .returns<FeedAllocationRow[]>();

  if (error) {
    logger.error("get_user_feed_allocation_failed", {
      error_message: error.message,
      fix_suggestion:
        "Confirm migration 0008 applied and the user_feed_allocation_owner_all RLS policy allows the authed SELECT.",
    });
    throw new Error(
      `Failed to read user feed allocation: ${error.message}. ` +
        "fix_suggestion: confirm migration 0008 applied and user_feed_allocation allows the authed SELECT.",
    );
  }

  // Map each enum row back to its design bucket, dropping any enum value with no design
  // mapping (defensive — should never happen, but never surface an unknown bucket). A NICHE /
  // beyond-bubble SECTION row (either niche column set) carries its columns through so the
  // screen can render a read-only named block; a coarse row stays exactly `{ bucketId, count }`.
  const segments: AllocationSegment[] = [];
  for (const row of data ?? []) {
    const bucketId: DesignBucketId | undefined = ENUM_TO_DESIGN_BUCKET[row.allocation_category];
    if (bucketId === undefined) {
      logger.warn("feed_allocation_unknown_enum_value_skipped", {
        allocation_category: row.allocation_category,
        fix_suggestion: "An allocation_category with no design-bucket mapping was read; confirm DESIGN_BUCKET maps it.",
      });
      continue;
    }
    const interestId = row.allocation_interest_id ?? null;
    const sectionLabel = row.allocation_section_label ?? null;
    // Use the shared coarse discriminator so "coarse = both niche columns null" lives in exactly
    // one place (feedBuckets.ts) — a coarse row stays bare `{ bucketId, count }`; a section row
    // carries its niche columns through for the read-only named-block render.
    if (isCoarseAllocationSegment({ interestId, sectionLabel })) {
      segments.push({ bucketId, count: row.allocation_slot_count });
    } else {
      segments.push({ bucketId, count: row.allocation_slot_count, interestId, sectionLabel });
    }
  }

  logger.info("get_user_feed_allocation_completed", { user_id: authedUserId, returned: segments.length });
  return segments;
}

/** Typed outcome of a {@link saveUserFeedAllocation} run. */
export interface SaveAllocationResult {
  /** How many `user_feed_allocation` rows were upserted (the persisted buckets). */
  persisted_count: number;
  /**
   * Design bucket ids that could NOT be persisted because their enum value does not exist in
   * the live DB. Under the SP3 taxonomy every bucket has a real enum value, so this is ALWAYS
   * empty — the field is retained for the result-shape stability the caller logs against, and
   * keeps the surface ready if a future bucket ever ships ahead of its enum migration.
   */
  deferred_buckets: DesignBucketId[];
}

/**
 * Persist a completed "Build your 30" allocation for the authed user, scoped to their
 * `auth.uid()`. COARSE-ONLY (founder decision 2026-07-05): only coarse blocks are written —
 * read-only SECTION rows (niche + "Beyond your bubble" reserve) from the backend niche
 * allocator are filtered out of the write and PRESERVED untouched. Upserts one
 * `user_feed_allocation` row per coarse segment (`allocation_category` = mapped enum,
 * `allocation_slot_count` = count, `allocation_sort_order` = the segment's index in the
 * ordered list), then DELETES any stale COARSE rows for buckets the user removed — so the
 * coarse set reflects EXACTLY the saved blocks while section rows survive the round-trip.
 * Idempotent: re-saving the same allocation rides the
 * `(follow_user_id, allocation_category, allocation_interest_id)` unique constraint
 * (migration 0026; NULLS NOT DISTINCT so coarse rows still key on user+category) and the
 * delete is a no-op when nothing was removed.
 *
 * The total SHOULD be {@link ALLOCATION_TOTAL} (the UI enforces it). We don't reject a
 * non-30 total here (that would crash the flow on a UI bug), but we LOG it loudly (Rule 12)
 * so a drifting invariant is visible rather than silently persisted.
 *
 * @param segments - The ordered `[{ bucketId, count }]` from the screen (index = sort order).
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns A {@link SaveAllocationResult} — rows written (`deferred_buckets` is always empty
 *   under the SP3 taxonomy, where every bucket has a real enum value).
 * @throws If unauthenticated, or if an upsert/delete fails (surfaced — Rule 12).
 *
 * @example
 * const result = await saveUserFeedAllocation([
 *   { bucketId: "geopolitics", count: 5 },
 *   { bucketId: "tech", count: 25 },
 * ]);
 * result.persisted_count;   // 2
 */
export async function saveUserFeedAllocation(
  segments: AllocationSegment[],
  client: SupabaseClient = getSupabaseBrowserClient(),
): Promise<SaveAllocationResult> {
  const authedUserId = await requireAuthedUserId(client);
  const total = sumSegmentCounts(segments);
  logger.info("save_user_feed_allocation_started", {
    user_id: authedUserId,
    segment_count: segments.length,
    total_slots: total,
  });

  // The UI enforces an exactly-30 budget; a non-30 total here means a UI invariant drifted.
  // Don't crash the flow — but never let it pass silently (Rule 12).
  if (total !== ALLOCATION_TOTAL) {
    logger.warn("feed_allocation_total_not_30", {
      user_id: authedUserId,
      total_slots: total,
      expected_total: ALLOCATION_TOTAL,
      fix_suggestion:
        "The Build-your-30 budget should total 30; the screen enforces this — investigate the caller if this fires.",
    });
  }

  // COARSE-ONLY save (founder decision 2026-07-05): this writer edits and persists ONLY coarse
  // "Build your 30" blocks. Read-only SECTION rows (niche / "Beyond your bubble" reserve, either
  // migration-0026 niche column set) are edited via re-interview, never here — so we filter them
  // OUT of the write set. Without this, a section segment that round-tripped through the screen
  // would be upserted as a coarse row (its niche columns dropped), flattening the backend's niche
  // allocation. The screen already passes coarse-only segments; this is the defensive belt.
  const coarseSegments = segments.filter(isCoarseAllocationSegment);
  const droppedSectionCount = segments.length - coarseSegments.length;
  if (droppedSectionCount > 0) {
    logger.warn("save_user_feed_allocation_section_segments_ignored", {
      user_id: authedUserId,
      dropped_section_count: droppedSectionCount,
      fix_suggestion:
        "Niche/beyond-bubble SECTION segments were passed to the coarse save and ignored — sections are read-only " +
        "(edited via re-interview). Pass only coarse blocks; sections persist untouched via the backend allocator.",
    });
  }

  // Build one upsert row per coarse segment, mapping the design bucket id → enum value and the
  // list index → the user's manual sort order.
  const upsertRows: FeedAllocationUpsertRow[] = coarseSegments.map((segment, index) => ({
    follow_user_id: authedUserId,
    allocation_category: DESIGN_BUCKET_TO_ENUM[segment.bucketId],
    allocation_slot_count: segment.count,
    allocation_sort_order: index,
  }));

  // Track which design buckets are in this save so we can DELETE the rest (removed buckets).
  const savedEnumValues = new Set<FeedCategoryEnum>(upsertRows.map((row) => row.allocation_category));

  // 1. Upsert the allocation rows. Migration 0026 moved the PK to a surrogate
  // `allocation_id` and made the upsert arbiter the 3-column unique
  // (follow_user_id, allocation_category, allocation_interest_id) — declared NULLS NOT
  // DISTINCT so these coarse rows (allocation_interest_id NULL) still conflict on
  // (user, category) exactly as before. The niche allocator (backend) writes the
  // non-null-interest rows; this "Build your 30" writer only ever writes coarse rows.
  if (upsertRows.length > 0) {
    const { error: upsertError } = await client.from(USER_FEED_ALLOCATION_TABLE).upsert(upsertRows, {
      onConflict: "follow_user_id,allocation_category,allocation_interest_id",
    });

    if (upsertError) {
      logger.error("save_user_feed_allocation_upsert_failed", {
        error_message: upsertError.message,
        fix_suggestion:
          "Confirm migration 0008 applied and the user_feed_allocation_owner_all RLS policy permits the authed upsert.",
      });
      throw new Error(
        `Failed to persist feed allocation: ${upsertError.message}. ` +
          "fix_suggestion: confirm migration 0008 applied and user_feed_allocation permits the authed upsert.",
      );
    }
  }

  // 2. Delete any COARSE rows for buckets NOT in this save (the user removed them). Scoped to the
  // authed user (also pinned by RLS) so the coarse set reflects EXACTLY the saved blocks.
  //
  // ANTI-CORRUPTION (slice #12, coarse-only): the delete is pinned to coarse rows only via
  // `allocation_interest_id IS NULL AND allocation_section_label IS NULL`, so read-only SECTION
  // rows written by the backend niche allocator are spared here. NICHE rows (interest set) are
  // fully safe across the whole save — the upsert arbiter includes `allocation_interest_id`, so a
  // coarse row (interest NULL) never collides with a niche row (interest set), and this delete
  // spares them. Without these two `.is()` predicates the old `.not.in` prune deleted any section
  // row whose category fell outside the coarse saved set, silently destroying the niche allocation.
  //
  // RESIDUAL (beyond-bubble only): a "Beyond your bubble" reserve row (interest NULL, label set)
  // is spared by THIS delete, but the migration-0026 upsert arbiter
  // `(follow_user_id, allocation_category, allocation_interest_id)` excludes the label — so a
  // coarse upsert for a category that COINCIDES with a beyond-bubble root can collide with it. The
  // full fix is a 4-column arbiter (incl. `allocation_section_label`), a schema migration deferred
  // to a follow-on issue. Not reachable today (the niche allocator is unwired). See
  // docs/residual-review-findings/ for the tracked finding.
  //
  // A `.not.in` with an empty saved set would prune every coarse row — that is the intended
  // clear-all-coarse path (sections still survive via the same two `.is()` predicates).
  const savedEnumList = Array.from(savedEnumValues);
  let deleteQuery = client
    .from(USER_FEED_ALLOCATION_TABLE)
    .delete()
    .eq("follow_user_id", authedUserId)
    .is("allocation_interest_id", null)
    .is("allocation_section_label", null);
  if (savedEnumList.length > 0) {
    deleteQuery = deleteQuery.not("allocation_category", "in", `(${savedEnumList.join(",")})`);
  }
  const { error: deleteError } = await deleteQuery;

  if (deleteError) {
    logger.error("save_user_feed_allocation_delete_stale_failed", {
      error_message: deleteError.message,
      fix_suggestion:
        "Confirm migration 0008 applied and the user_feed_allocation_owner_all RLS policy permits the authed DELETE.",
    });
    throw new Error(
      `Failed to prune removed feed-allocation buckets: ${deleteError.message}. ` +
        "fix_suggestion: confirm migration 0008 applied and user_feed_allocation permits the authed DELETE.",
    );
  }

  const result: SaveAllocationResult = {
    persisted_count: savedEnumList.length,
    deferred_buckets: [],
  };
  logger.info("save_user_feed_allocation_completed", {
    user_id: authedUserId,
    persisted_count: result.persisted_count,
    deferred_count: result.deferred_buckets.length,
  });
  return result;
}
