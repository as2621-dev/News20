import { describe, expect, it } from "vitest";
import { persistInterviewInterests } from "@/lib/interviewProfile";
import { PROFILE_WEIGHT_BY_DEPTH } from "@/lib/onboardingProfile";
import type { InterviewTerminalPayload, TerminalMicroInterest } from "@/types/interview";

/**
 * These tests encode WHY interview persistence matters (Rule 9), each failing when a
 * specific acceptance criterion from issue #2 breaks: niches mint REAL nodes (via the RPC)
 * and deep, per-user-labelled profile rows; the ladder retraces the slug; two users
 * converge on ONE node keeping their own label; skip-everything is a valid roots-only
 * profile; invalid items are rejected loudly while the rest persist; a mid-write failure
 * stamps nothing and blocks no retry; and completion is NEVER stamped here. They mock at
 * the Supabase client boundary — the `rpc()` mint seam plus `upsert`/`update` — mirroring
 * `tests/lib/onboardingProfile.test.ts`.
 */

/** A captured `from(table).upsert(rows, options)` call. */
interface CapturedUpsert {
  table: string;
  rows: unknown;
  options: unknown;
}
/** A captured `from(table).update(values).eq(col, val)` call. */
interface CapturedUpdate {
  table: string;
  values: Record<string, unknown>;
  eqColumn: string;
  eqValue: unknown;
}
/** A captured `rpc(name, args)` call (the mint seam). */
interface CapturedRpc {
  name: string;
  args: Record<string, unknown>;
}

/**
 * Build a fake Supabase client that:
 *  - resolves `rpc("mint_interest_ladder", { p_canonical_slug, ... })` from a slug→id
 *    map (defaulting to a deterministic `node:<slug>` so the SAME slug always returns the
 *    SAME id — the convergence property), or a configured error, and
 *  - records every `upsert(...)` and `update(...).eq(...)` so assertions can prove what
 *    was (and was NOT) written, with an optional per-table upsert error.
 */
function makeFakeClient(
  config: {
    mintBySlug?: Record<string, string>;
    mintError?: { message: string } | null;
    upsertErrorTable?: string | null;
  } = {},
) {
  const rpcCalls: CapturedRpc[] = [];
  const upserts: CapturedUpsert[] = [];
  const updates: CapturedUpdate[] = [];

  const client = {
    rpc: (name: string, args: Record<string, unknown>) => {
      rpcCalls.push({ name, args });
      if (config.mintError) {
        return Promise.resolve({ data: null, error: config.mintError });
      }
      const slug = String(args.p_canonical_slug);
      const id = config.mintBySlug?.[slug] ?? `node:${slug}`;
      return Promise.resolve({ data: id, error: null });
    },
    from: (table: string) => ({
      upsert: (rows: unknown, options: unknown) => {
        upserts.push({ table, rows, options });
        if (config.upsertErrorTable === table) {
          return Promise.resolve({ error: { message: `simulated ${table} write failure` } });
        }
        return Promise.resolve({ error: null });
      },
      update: (values: Record<string, unknown>) => ({
        eq: (eqColumn: string, eqValue: unknown) => {
          updates.push({ table, values, eqColumn, eqValue });
          return Promise.resolve({ error: null });
        },
      }),
    }),
  };

  return { client: client as never, rpcCalls, upserts, updates };
}

const USER_ID = "00000000-0000-0000-0000-000000000abc";
const OTHER_USER_ID = "00000000-0000-0000-0000-000000000def";

/** Build a spec-§4 terminal micro-interest; ladder defaults to the slug's derived parent chain. */
function microInterest(overrides: Partial<TerminalMicroInterest> & { canonical_slug: string }): TerminalMicroInterest {
  const segments = overrides.canonical_slug.split(".");
  const derivedLadder = segments.slice(0, -1).map((_, i) => segments.slice(0, i + 1).join("."));
  return {
    display_label: overrides.display_label ?? overrides.canonical_slug,
    canonical_slug: overrides.canonical_slug,
    ladder: overrides.ladder ?? derivedLadder,
    search_anchor_terms: overrides.search_anchor_terms ?? ["anchor one", "anchor two"],
    strict: overrides.strict ?? false,
  };
}

function payloadOf(...interests: TerminalMicroInterest[]): InterviewTerminalPayload {
  return { micro_interests: interests };
}

function profileRows(upserts: CapturedUpsert[]): Array<Record<string, unknown>> {
  return (upserts.find((u) => u.table === "user_interest_profile")?.rows as Array<Record<string, unknown>>) ?? [];
}

describe("persistInterviewInterests", () => {
  it("happy path: mints each niche node and writes deep, per-user-labelled, strict-preserving profile rows", async () => {
    // WHY: the core DoD — a terminal payload of niches must MINT nodes (one RPC per slug)
    // and persist deep user_interest_profile rows carrying the user's OWN display label,
    // depth weight, strictness, and source. FAILS if a node is not minted, the label is
    // dropped/overwrites the node, the row lands on the wrong user, or strict is flattened.
    const payload = payloadOf(
      microInterest({
        canonical_slug: "sport.cricket.ipl.auctions",
        display_label: "IPL silly season",
        search_anchor_terms: ["IPL auction", "IPL retention"],
        strict: true,
      }),
      microInterest({ canonical_slug: "business.equities", display_label: "stocks I hold" }),
    );
    const { client, rpcCalls, upserts, updates } = makeFakeClient();

    const result = await persistInterviewInterests(USER_ID, payload, {}, client);

    expect(result.minted_interest_count).toBe(2);
    expect(result.rejected_interests).toEqual([]);

    // One mint RPC per micro-interest, each carrying the joined anchor terms as the query.
    expect(rpcCalls.map((call) => call.name)).toEqual(["mint_interest_ladder", "mint_interest_ladder"]);
    expect(rpcCalls[0].args.p_canonical_slug).toBe("sport.cricket.ipl.auctions");
    expect(rpcCalls[0].args.p_search_query).toBe("IPL auction, IPL retention");

    const rows = profileRows(upserts);
    expect(rows).toHaveLength(2);
    const deep = rows.find((row) => row.profile_interest_id === "node:sport.cricket.ipl.auctions");
    expect(deep?.profile_user_id).toBe(USER_ID);
    expect(deep?.profile_source).toBe("typed");
    // The load-bearing assertions: the user's own label rides the profile row, strict survives,
    // and the depth-3 slug is weighted by the depth map (not flattened).
    expect(deep?.profile_display_label).toBe("IPL silly season");
    expect(deep?.profile_is_strict).toBe(true);
    expect(deep?.profile_weight).toBe(PROFILE_WEIGHT_BY_DEPTH[3]);

    const shallow = rows.find((row) => row.profile_interest_id === "node:business.equities");
    expect(shallow?.profile_weight).toBe(PROFILE_WEIGHT_BY_DEPTH[1]);
    expect(shallow?.profile_is_strict).toBe(false);

    // Upsert targets the unique pair, and completion is NEVER stamped here (owner rule 2026-06-30).
    expect(upserts.find((u) => u.table === "user_interest_profile")?.options).toEqual({
      onConflict: "profile_user_id,profile_interest_id",
    });
    expect(updates.find((u) => u.table === "users")).toBeUndefined();
  });

  it("uses the interview voice source when asked (parameterized, not hardcoded)", async () => {
    // WHY: the M3 voice interview reuses this path with a different provenance. FAILS if
    // profile_source is hardcoded to 'typed'.
    const { client, upserts } = makeFakeClient();

    await persistInterviewInterests(
      USER_ID,
      payloadOf(microInterest({ canonical_slug: "ai.agents" })),
      {
        profile_source: "voice",
      },
      client,
    );

    expect(profileRows(upserts)[0].profile_source).toBe("voice");
  });

  it("convergence: two users with the SAME slug in different words land on ONE node, keeping their own labels", async () => {
    // WHY (criterion 3 / PRD #28): the canonical slug is the cross-user convergence key —
    // two users who typed the same niche differently must resolve to the SAME minted node id
    // (upsert-by-slug in the RPC) while each keeps their OWN display label on their own row.
    // FAILS if the shared node diverges or a user's label is lost/overwritten. NOTE: the
    // upsert-by-slug convergence itself lives in the SQL RPC and is verified live against prod
    // (B8 acceptance check recovered the same node for the same slug); this unit test pins the
    // TS-side contract — both users mint against the identical slug (a SHARED slug→id store
    // models the one node), and each keeps their own label.
    const sharedNodeBySlug = { "sport.formula-1.silly-season": "node:shared-f1-silly-season" };
    const first = makeFakeClient({ mintBySlug: sharedNodeBySlug });
    const second = makeFakeClient({ mintBySlug: sharedNodeBySlug });

    await persistInterviewInterests(
      USER_ID,
      payloadOf(microInterest({ canonical_slug: "sport.formula-1.silly-season", display_label: "F1 silly season" })),
      {},
      first.client,
    );
    await persistInterviewInterests(
      OTHER_USER_ID,
      payloadOf(microInterest({ canonical_slug: "sport.formula-1.silly-season", display_label: "driver market" })),
      {},
      second.client,
    );

    // Both mint against the identical slug (the convergence key) → the same node id.
    expect(first.rpcCalls[0].args.p_canonical_slug).toBe("sport.formula-1.silly-season");
    expect(second.rpcCalls[0].args.p_canonical_slug).toBe("sport.formula-1.silly-season");
    const firstRow = profileRows(first.upserts)[0];
    const secondRow = profileRows(second.upserts)[0];
    expect(firstRow.profile_interest_id).toBe("node:shared-f1-silly-season");
    expect(firstRow.profile_interest_id).toBe(secondRow.profile_interest_id);
    // Each user's own vocabulary is preserved on their own profile row.
    expect(firstRow.profile_display_label).toBe("F1 silly season");
    expect(secondRow.profile_display_label).toBe("driver market");
    expect(firstRow.profile_user_id).toBe(USER_ID);
    expect(secondRow.profile_user_id).toBe(OTHER_USER_ID);
  });

  it("roots-only payload writes depth-0 profile rows and mints no niche (single-segment slug)", async () => {
    // WHY (criterion 4): skip-everything is a VALID degenerate profile — the lit roots persist
    // as depth-0 rows (weight 1.0), the RPC is called with a single-segment root slug (which
    // mints nothing DB-side), and traits are still written so the feed is eligible. FAILS if a
    // root pick is rejected, weighted as a niche, or the traits row is skipped.
    const payload = payloadOf(
      microInterest({ canonical_slug: "sport", display_label: "Sport", search_anchor_terms: ["Sport", "Sport news"] }),
      microInterest({ canonical_slug: "ai", display_label: "AI", search_anchor_terms: ["AI", "AI news"] }),
    );
    const { client, rpcCalls, upserts } = makeFakeClient();

    const result = await persistInterviewInterests(USER_ID, payload, {}, client);

    expect(result.minted_interest_count).toBe(2);
    expect(result.rejected_interests).toEqual([]);
    // The RPC is invoked with root (single-segment) slugs — no dotted ladder to mint.
    expect(rpcCalls.every((call) => !String(call.args.p_canonical_slug).includes("."))).toBe(true);
    const rows = profileRows(upserts);
    expect(rows.every((row) => row.profile_weight === PROFILE_WEIGHT_BY_DEPTH[0])).toBe(true);
    expect(upserts.find((u) => u.table === "user_interest_traits")).toBeDefined();
  });

  it("skip-everything (empty payload) writes no profile/mint, still writes traits, and does not throw", async () => {
    // WHY (criterion 4, degenerate): a user who lit no roots persists nothing but stays
    // feed-eligible (traits row) and NOT onboarded. FAILS if it mints, writes a profile row,
    // stamps onboarded, or throws.
    const { client, rpcCalls, upserts, updates } = makeFakeClient();

    const result = await persistInterviewInterests(USER_ID, { micro_interests: [] }, {}, client);

    expect(result.minted_interest_count).toBe(0);
    expect(rpcCalls).toHaveLength(0);
    expect(upserts.find((u) => u.table === "user_interest_profile")).toBeUndefined();
    expect(upserts.find((u) => u.table === "user_interest_traits")).toBeDefined();
    expect(updates.find((u) => u.table === "users")).toBeUndefined();
  });

  it("rejects a non-root-anchored slug loudly while the rest persist (nothing silently invented)", async () => {
    // WHY (criterion 5): a slug whose root is not one of the 8 canonical roots must be
    // rejected with a reason (never minted), while valid siblings still persist. FAILS if
    // the bad item is minted/persisted or if it takes the valid ones down with it.
    const payload = payloadOf(
      microInterest({ canonical_slug: "sport.cricket", display_label: "Cricket" }),
      microInterest({ canonical_slug: "wizardry.spells", display_label: "Spells" }),
    );
    const { client, rpcCalls, upserts } = makeFakeClient();

    const result = await persistInterviewInterests(USER_ID, payload, {}, client);

    expect(result.minted_interest_count).toBe(1);
    expect(result.rejected_interests).toEqual([
      { canonical_slug: "wizardry.spells", reason: "slug_not_root_anchored" },
    ]);
    // Only the valid slug was minted; the bad one never reached the RPC.
    expect(rpcCalls.map((call) => call.args.p_canonical_slug)).toEqual(["sport.cricket"]);
    const rows = profileRows(upserts);
    expect(rows).toHaveLength(1);
    expect(rows[0].profile_interest_id).toBe("node:sport.cricket");
  });

  it("rejects a niche with fewer than two anchor terms", async () => {
    // WHY (criterion 5): a searchable niche needs >= 2 anchor terms; one term (or duplicates
    // collapsing to one) is rejected, not minted with a thin query.
    const payload = payloadOf(
      microInterest({ canonical_slug: "tech.ai.llms", display_label: "LLMs", search_anchor_terms: ["LLM", "llm"] }),
    );
    const { client, rpcCalls } = makeFakeClient();

    const result = await persistInterviewInterests(USER_ID, payload, {}, client);

    expect(result.minted_interest_count).toBe(0);
    expect(result.rejected_interests).toEqual([{ canonical_slug: "tech.ai.llms", reason: "under_two_anchor_terms" }]);
    expect(rpcCalls).toHaveLength(0);
  });

  it("rejects a payload whose ladder does not retrace its slug (criterion 2 integrity backstop)", async () => {
    // WHY (criterion 2): every persisted node's parent chain must retrace the drill path, so
    // the ladder MUST equal the chain derived from the slug. A mismatched ladder is a
    // malformed/tampered payload and is rejected, never persisted with a wrong path.
    const payload = payloadOf(
      microInterest({ canonical_slug: "sport.cricket.ipl", ladder: ["sport", "sport.tennis"] }),
    );
    const { client } = makeFakeClient();

    const result = await persistInterviewInterests(USER_ID, payload, {}, client);

    expect(result.rejected_interests).toEqual([
      { canonical_slug: "sport.cricket.ipl", reason: "ladder_slug_mismatch" },
    ]);
    expect(result.minted_interest_count).toBe(0);
  });

  it("rejects a malformed payload item (missing/wrong-typed fields) without crashing the batch", async () => {
    // WHY (criterion 5 / untrusted boundary): TS types are erased at runtime, so a tampered/
    // malformed item (e.g. a null canonical_slug) must yield a reject REASON, not throw a
    // TypeError that discards every valid sibling in the same payload. FAILS if the whole call
    // throws or a valid sibling is dropped.
    const malformed = { display_label: "junk", ladder: [], search_anchor_terms: ["a", "b"], strict: false } as never;
    const payload: InterviewTerminalPayload = {
      micro_interests: [malformed, microInterest({ canonical_slug: "sport.cricket", display_label: "Cricket" })],
    };
    const { client, upserts } = makeFakeClient();

    const result = await persistInterviewInterests(USER_ID, payload, {}, client);

    expect(result.rejected_interests).toEqual([{ canonical_slug: "<empty>", reason: "malformed_payload_item" }]);
    // The valid sibling still persisted — one bad item did not sink the batch.
    expect(result.minted_interest_count).toBe(1);
    expect(profileRows(upserts)[0].profile_interest_id).toBe("node:sport.cricket");
  });

  it("dedups two identical slugs into ONE profile row (no ON CONFLICT double-affect)", async () => {
    // WHY: a duplicate slug resolves to the same leaf id; two rows for one (user, interest)
    // pair in a single upsert would error in Postgres. The backstop keeps exactly one row.
    const payload = payloadOf(
      microInterest({ canonical_slug: "ai.agents", display_label: "agents" }),
      microInterest({ canonical_slug: "ai.agents", display_label: "agentic AI" }),
    );
    const { client, upserts } = makeFakeClient();

    const result = await persistInterviewInterests(USER_ID, payload, {}, client);

    expect(result.minted_interest_count).toBe(1);
    expect(profileRows(upserts)).toHaveLength(1);
  });

  it("mid-write mint failure throws, writes NO profile row, and stamps NOTHING (clean retry)", async () => {
    // WHY (criterion 6): a failed mint must surface (Rule 12), must NOT leave a partial
    // profile, and must NOT stamp onboarded — so a retry re-runs cleanly. FAILS if it swallows
    // the error, writes a profile row anyway, or stamps user_onboarded_at.
    const { client, upserts, updates } = makeFakeClient({ mintError: { message: "rpc boom" } });

    await expect(
      persistInterviewInterests(USER_ID, payloadOf(microInterest({ canonical_slug: "sport.cricket" })), {}, client),
    ).rejects.toThrow(/mint interest ladder/i);

    expect(upserts.find((u) => u.table === "user_interest_profile")).toBeUndefined();
    expect(updates.find((u) => u.table === "users")).toBeUndefined();
  });

  it("surfaces a profile-upsert failure instead of swallowing it (Rule 12)", async () => {
    // WHY: a failed profile write must throw so the caller never treats a half-run as success.
    const { client } = makeFakeClient({ upsertErrorTable: "user_interest_profile" });

    await expect(
      persistInterviewInterests(USER_ID, payloadOf(microInterest({ canonical_slug: "sport.cricket" })), {}, client),
    ).rejects.toThrow(/interview interest profile/i);
  });
});
