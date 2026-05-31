import { describe, expect, it } from "vitest";
import type { InterestSelection } from "@/components/onboarding/InterestChips";
import { PROFILE_WEIGHT_BY_DEPTH, persistInterestProfile } from "@/lib/onboardingProfile";

/**
 * These tests encode WHY persistInterestProfile matters (Rule 9), not just that it
 * runs: each one fails when a specific business guarantee from the phase DoD is
 * broken — strict preserved, depth-weighted, customs canonicalized, NO orphan
 * rows, onboarded stamped. They mock at the Supabase client boundary (CLAUDE.md
 * mocking strategy), mirroring `tests/lib/feed/supabaseFeed.test.ts`.
 */

/** A captured `from(table)` call and the rows/filters it received. */
interface CapturedUpsert {
  table: string;
  rows: unknown;
  options: unknown;
}
interface CapturedUpdate {
  table: string;
  values: Record<string, unknown>;
  eqColumn: string;
  eqValue: unknown;
}

/**
 * Build a fake Supabase client that:
 *  - resolves `from("interests").select().or().eq().limit().returns()` from a
 *    label→match lookup map (canonicalization), and
 *  - records every `upsert(...)` and `update(...).eq(...)` so assertions can prove
 *    what was (and was NOT) written.
 *
 * `interestMatches` maps a lowercased custom label to the row the lookup returns;
 * an absent label resolves to `{ data: [], ... }` (NO match → must not be written).
 */
function makeFakeClient(
  interestMatches: Record<string, { interest_id: string; depth_level: number }> = {},
  interestsError: { message: string } | null = null,
) {
  const upserts: CapturedUpsert[] = [];
  const updates: CapturedUpdate[] = [];
  // The label the in-flight interests lookup is filtering on (captured from `.or`).
  let pendingLookupLabel = "";

  function from(table: string) {
    if (table === "interests") {
      return {
        select: () => ({
          or: (filter: string) => {
            // filter is `interest_label.ilike.<label>,interest_slug.ilike.<label>`
            const match = filter.match(/interest_label\.ilike\.(.*?),interest_slug/);
            pendingLookupLabel = (match?.[1] ?? "").toLowerCase();
            return {
              eq: () => ({
                limit: () => ({
                  returns: () => {
                    if (interestsError) {
                      return Promise.resolve({ data: null, error: interestsError });
                    }
                    const hit = interestMatches[pendingLookupLabel];
                    return Promise.resolve({ data: hit ? [hit] : [], error: null });
                  },
                }),
              }),
            };
          },
        }),
      };
    }

    // Write tables: user_interest_profile, user_interest_traits, users.
    return {
      upsert: (rows: unknown, options: unknown) => {
        upserts.push({ table, rows, options });
        return Promise.resolve({ error: null });
      },
      update: (values: Record<string, unknown>) => ({
        eq: (eqColumn: string, eqValue: unknown) => {
          updates.push({ table, values, eqColumn, eqValue });
          return Promise.resolve({ error: null });
        },
      }),
    };
  }

  return { client: { from } as never, upserts, updates };
}

const USER_ID = "00000000-0000-0000-0000-000000000abc";

describe("persistInterestProfile", () => {
  it("writes ≥1 user_interest_profile row scoped to the user with source 'typed' and STRICT preserved", async () => {
    // WHY: the core DoD — a completed onboarding must persist the picks scoped to
    // auth.uid() AND keep the per-interest strict flag. This FAILS if strict is
    // dropped/flattened or the row is written for the wrong user.
    const selection: InterestSelection = {
      taxonomy_selections: [
        {
          selection_kind: "taxonomy",
          interest_id: "int-cricket",
          interest_label: "Cricket",
          depth_level: 1,
          profile_is_strict: true,
        },
      ],
      custom_selections: [],
    };
    const { client, upserts } = makeFakeClient();

    const result = await persistInterestProfile(USER_ID, selection, {}, client);

    expect(result.persisted_count).toBe(1);
    const profileUpsert = upserts.find((u) => u.table === "user_interest_profile");
    expect(profileUpsert).toBeDefined();
    const rows = profileUpsert?.rows as Array<Record<string, unknown>>;
    expect(rows).toHaveLength(1);
    expect(rows[0].profile_user_id).toBe(USER_ID);
    expect(rows[0].profile_interest_id).toBe("int-cricket");
    expect(rows[0].profile_source).toBe("typed");
    // The load-bearing assertion: strict survived.
    expect(rows[0].profile_is_strict).toBe(true);
    // Upsert targets the unique constraint, not a blind insert.
    expect(profileUpsert?.options).toEqual({ onConflict: "profile_user_id,profile_interest_id" });
  });

  it("weights each pick by the depth map (fails if weights are flattened)", async () => {
    // WHY: Open Q1 — deeper picks start heavier. This FAILS if every row gets the
    // same default weight instead of the per-depth value.
    const selection: InterestSelection = {
      taxonomy_selections: [
        {
          selection_kind: "taxonomy",
          interest_id: "int-sport",
          interest_label: "Sport",
          depth_level: 0,
          profile_is_strict: false,
        },
        {
          selection_kind: "taxonomy",
          interest_id: "int-india",
          interest_label: "India",
          depth_level: 2,
          profile_is_strict: false,
        },
      ],
      custom_selections: [],
    };
    const { client, upserts } = makeFakeClient();

    await persistInterestProfile(USER_ID, selection, {}, client);

    const rows = upserts.find((u) => u.table === "user_interest_profile")?.rows as Array<Record<string, unknown>>;
    const byId = Object.fromEntries(rows.map((row) => [row.profile_interest_id, row.profile_weight]));
    expect(byId["int-sport"]).toBe(PROFILE_WEIGHT_BY_DEPTH[0]);
    expect(byId["int-india"]).toBe(PROFILE_WEIGHT_BY_DEPTH[2]);
    // Guard against a flattened map: the two weights must differ.
    expect(byId["int-sport"]).not.toBe(byId["int-india"]);
  });

  it("canonicalizes a custom that MATCHES an existing node and persists to that id", async () => {
    // WHY: Open Q2 v1 — a free-text custom that names an existing topic is written
    // against that taxonomy node (not orphaned, not skipped). FAILS if the match
    // path doesn't upsert a profile row to the matched interest_id.
    const selection: InterestSelection = {
      taxonomy_selections: [],
      custom_selections: [{ selection_kind: "custom", interest_kind: "custom", custom_label: "Cricket" }],
    };
    const { client, upserts } = makeFakeClient({ cricket: { interest_id: "int-cricket", depth_level: 1 } });

    const result = await persistInterestProfile(USER_ID, selection, {}, client);

    expect(result.persisted_count).toBe(1);
    expect(result.unpersisted_customs).toEqual([]);
    const rows = upserts.find((u) => u.table === "user_interest_profile")?.rows as Array<Record<string, unknown>>;
    expect(rows[0].profile_interest_id).toBe("int-cricket");
    expect(rows[0].profile_weight).toBe(PROFILE_WEIGHT_BY_DEPTH[1]);
  });

  it("returns a NO-MATCH custom as unpersisted and NEVER writes it as an orphan row", async () => {
    // WHY: the hard Rule-12 guarantee — RLS forbids client-side `interests` inserts,
    // so an unmatched custom must be surfaced, NOT written as a dangling profile row
    // pointing at a non-existent interest. FAILS if any user_interest_profile row is
    // written for the unmatched custom.
    const selection: InterestSelection = {
      taxonomy_selections: [],
      custom_selections: [
        { selection_kind: "custom", interest_kind: "custom", custom_label: "Underwater Basket Weaving" },
      ],
    };
    const { client, upserts } = makeFakeClient(); // no matches

    const result = await persistInterestProfile(USER_ID, selection, {}, client);

    expect(result.persisted_count).toBe(0);
    expect(result.unpersisted_customs).toEqual(["Underwater Basket Weaving"]);
    // No profile rows at all (the orphan-prevention assertion).
    expect(upserts.find((u) => u.table === "user_interest_profile")).toBeUndefined();
  });

  it("stamps user_onboarded_at on the user's own row", async () => {
    // WHY: completion must mark the user onboarded (the gate the route + Phase 1c
    // read). FAILS if the users update is dropped or scoped to the wrong column.
    const selection: InterestSelection = {
      taxonomy_selections: [
        {
          selection_kind: "taxonomy",
          interest_id: "int-tech",
          interest_label: "Tech",
          depth_level: 0,
          profile_is_strict: false,
        },
      ],
      custom_selections: [],
    };
    const { client, updates } = makeFakeClient();

    await persistInterestProfile(USER_ID, selection, {}, client);

    const onboardedUpdate = updates.find((u) => u.table === "users");
    expect(onboardedUpdate).toBeDefined();
    expect(onboardedUpdate?.eqColumn).toBe("user_id");
    expect(onboardedUpdate?.eqValue).toBe(USER_ID);
    expect(typeof onboardedUpdate?.values.user_onboarded_at).toBe("string");
  });

  it("surfaces a canonicalization lookup error instead of swallowing it (Rule 12)", async () => {
    // WHY: a failed interests read must not silently drop the custom; it throws.
    const selection: InterestSelection = {
      taxonomy_selections: [],
      custom_selections: [{ selection_kind: "custom", interest_kind: "custom", custom_label: "Cricket" }],
    };
    const { client } = makeFakeClient({}, { message: "permission denied" });

    await expect(persistInterestProfile(USER_ID, selection, {}, client)).rejects.toThrow(/canonicalize/i);
  });
});
