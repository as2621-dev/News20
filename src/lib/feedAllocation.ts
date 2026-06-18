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
 * ── The `podcasts` enum value (migration 0010, not yet applied) ──────────────────────
 * The screen draws 9 buckets but the 0008 enum has only 8 values — `podcasts` is added by
 * migration `0010_feed_category_podcasts.sql`, which the owner applies separately. Until
 * then a `podcasts` upsert fails with a Postgres "invalid input value for enum" error.
 * {@link saveUserFeedAllocation} treats THAT specific failure as a graceful-degrade: it
 * logs a structured warning (with a fix_suggestion to apply 0010) and persists the other 8
 * buckets successfully, rather than failing the whole save / crashing the onboarding flow.
 * All 9 buckets still render in the UI.
 */

import type { PostgrestError, SupabaseClient } from "@supabase/supabase-js";
import {
  ALLOCATION_TOTAL,
  type AllocationSegment,
  DESIGN_BUCKET_TO_ENUM,
  type DesignBucketId,
  ENUM_TO_DESIGN_BUCKET,
  type FeedCategoryEnum,
  PODCASTS_ENUM_VALUE,
  sumSegmentCounts,
} from "@/lib/feedBuckets";
import { logger } from "@/lib/logger";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** The `user_feed_allocation` table name — single source of truth for the table ref. */
const USER_FEED_ALLOCATION_TABLE = "user_feed_allocation";

/** The column projection {@link getUserFeedAllocation} requests (DDL order, not `*`). */
const USER_FEED_ALLOCATION_COLUMNS = "allocation_category,allocation_slot_count,allocation_sort_order";

/** The shape PostgREST returns for a `user_feed_allocation` row read. */
interface FeedAllocationRow {
  allocation_category: FeedCategoryEnum;
  allocation_slot_count: number;
  allocation_sort_order: number;
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

/**
 * Whether a PostgREST error is the "the `podcasts` enum value does not exist yet" signal —
 * i.e. migration 0010 has not been applied to this DB. Postgres raises SQLSTATE `22P02`
 * ("invalid input value for enum feed_category: \"podcasts\"") on an unknown enum literal.
 * We match on the code AND the value name so an unrelated `22P02` (some other bad literal)
 * is NOT mistaken for the pre-migration podcasts gap (it would re-throw and surface).
 *
 * @param error - The PostgrestError from a failed upsert.
 * @returns `true` only when the failure is specifically the missing `podcasts` enum value.
 */
function isPodcastsEnumMissingError(error: PostgrestError): boolean {
  const code = error.code ?? "";
  const message = (error.message ?? "").toLowerCase();
  const isInvalidEnumInput = code === "22P02" || message.includes("invalid input value for enum");
  return isInvalidEnumInput && message.includes(PODCASTS_ENUM_VALUE);
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
 * saved; // [{ bucketId: "world", count: 5 }, { bucketId: "tech", count: 5 }, …]
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
  // mapping (defensive — should never happen, but never surface an unknown bucket).
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
    segments.push({ bucketId, count: row.allocation_slot_count });
  }

  logger.info("get_user_feed_allocation_completed", { user_id: authedUserId, returned: segments.length });
  return segments;
}

/** Typed outcome of a {@link saveUserFeedAllocation} run. */
export interface SaveAllocationResult {
  /** How many `user_feed_allocation` rows were upserted (the persisted buckets). */
  persisted_count: number;
  /**
   * Design bucket ids that could NOT be persisted because their enum value does not exist
   * in the live DB yet (today only `podcasts`, until migration 0010 is applied). Surfaced
   * (never silently dropped — Rule 12); the caller may show "Podcasts saved once we ship it".
   */
  deferred_buckets: DesignBucketId[];
}

/**
 * Persist a completed "Build your 30" allocation for the authed user, scoped to their
 * `auth.uid()`. Upserts one `user_feed_allocation` row per segment
 * (`allocation_category` = mapped enum, `allocation_slot_count` = count,
 * `allocation_sort_order` = the segment's index in the ordered list), then DELETES any
 * stale rows for buckets the user removed — so the table reflects EXACTLY the saved set.
 * Idempotent: re-saving the same allocation rides the `(follow_user_id, allocation_category)`
 * PK and the delete is a no-op when nothing was removed.
 *
 * The total SHOULD be {@link ALLOCATION_TOTAL} (the UI enforces it). We don't reject a
 * non-30 total here (that would crash the flow on a UI bug), but we LOG it loudly (Rule 12)
 * so a drifting invariant is visible rather than silently persisted.
 *
 * **`podcasts` graceful-degrade:** if the upsert fails with the "missing `podcasts` enum
 * value" signal (migration 0010 not yet applied), we DROP the podcasts row, re-upsert the
 * remaining 8, and surface `podcasts` in {@link SaveAllocationResult.deferred_buckets} —
 * the save SUCCEEDS for the other buckets and the flow continues (see module JSDoc).
 *
 * @param segments - The ordered `[{ bucketId, count }]` from the screen (index = sort order).
 * @param client - Optional Supabase client (injected in tests). Defaults to the shared browser client.
 * @returns A {@link SaveAllocationResult} — rows written + any deferred (pre-0010) buckets.
 * @throws If unauthenticated, or if a NON-podcasts upsert/delete fails (surfaced — Rule 12).
 *
 * @example
 * const result = await saveUserFeedAllocation([
 *   { bucketId: "world", count: 5 },
 *   { bucketId: "tech", count: 25 },
 * ]);
 * result.persisted_count;   // 2 (or 1 + deferred_buckets:["podcasts"] pre-0010)
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

  // Build one upsert row per segment, mapping the design bucket id → enum value and the
  // list index → the user's manual sort order.
  const upsertRows: FeedAllocationUpsertRow[] = segments.map((segment, index) => ({
    follow_user_id: authedUserId,
    allocation_category: DESIGN_BUCKET_TO_ENUM[segment.bucketId],
    allocation_slot_count: segment.count,
    allocation_sort_order: index,
  }));

  // Track which design buckets are in this save so we can DELETE the rest (removed buckets).
  const savedEnumValues = new Set<FeedCategoryEnum>(upsertRows.map((row) => row.allocation_category));
  const deferredBuckets: DesignBucketId[] = [];

  // 1. Upsert the allocation rows (idempotent on the (follow_user_id, allocation_category) PK).
  if (upsertRows.length > 0) {
    const { error: upsertError } = await client
      .from(USER_FEED_ALLOCATION_TABLE)
      .upsert(upsertRows, { onConflict: "follow_user_id,allocation_category" });

    if (upsertError) {
      // Graceful-degrade ONLY for the specific "podcasts enum value missing" (pre-0010) case.
      if (isPodcastsEnumMissingError(upsertError)) {
        logger.warn("feed_allocation_podcasts_enum_missing", {
          user_id: authedUserId,
          error_message: upsertError.message,
          fix_suggestion:
            "Apply migration 0010_feed_category_podcasts.sql (adds the 'podcasts' feed_category enum value); " +
            "until then the Podcasts bucket is not persisted — the other 8 buckets save fine.",
        });
        deferredBuckets.push("podcasts");

        // Re-upsert WITHOUT the podcasts row(s) so the other 8 buckets persist.
        const rowsWithoutPodcasts = upsertRows.filter((row) => row.allocation_category !== PODCASTS_ENUM_VALUE);
        savedEnumValues.delete(PODCASTS_ENUM_VALUE);
        if (rowsWithoutPodcasts.length > 0) {
          const { error: retryError } = await client
            .from(USER_FEED_ALLOCATION_TABLE)
            .upsert(rowsWithoutPodcasts, { onConflict: "follow_user_id,allocation_category" });
          if (retryError) {
            logger.error("save_user_feed_allocation_retry_failed", {
              error_message: retryError.message,
              fix_suggestion:
                "Confirm migration 0008 applied and user_feed_allocation owner-all RLS permits the authed upsert.",
            });
            throw new Error(
              `Failed to persist feed allocation (after dropping podcasts): ${retryError.message}. ` +
                "fix_suggestion: confirm migration 0008 applied and RLS permits the owner upsert.",
            );
          }
        }
      } else {
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
  }

  // 2. Delete any rows for buckets NOT in this save (the user removed them). Scoped to the
  // authed user (also pinned by RLS) so the table reflects EXACTLY the saved set. A `.not.in`
  // with an empty saved set would delete everything — guard that (clear-all save deletes all).
  const savedEnumList = Array.from(savedEnumValues);
  let deleteQuery = client.from(USER_FEED_ALLOCATION_TABLE).delete().eq("follow_user_id", authedUserId);
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
    deferred_buckets: deferredBuckets,
  };
  logger.info("save_user_feed_allocation_completed", {
    user_id: authedUserId,
    persisted_count: result.persisted_count,
    deferred_count: result.deferred_buckets.length,
  });
  return result;
}
