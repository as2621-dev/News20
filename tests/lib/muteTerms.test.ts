import { describe, expect, it } from "vitest";
import { persistMuteTerms } from "@/lib/interviewProfile";
import type { TerminalMuteTerm } from "@/types/interview";

/**
 * These tests encode WHY SKIP-TUNE mute persistence matters (Rule 9, issue #17): mutes
 * become HARD FILTERS at feed assembly, so a mute persisted for one user must be owner-
 * scoped and, on rebuild-my-feed, the mute set must end EQUAL to the re-interview's list
 * with NO ORPHANED mutes surviving (AC #5) — a stale mute would keep silently erasing
 * stories the user no longer wants gone. Mocks the Supabase client at the upsert/delete
 * boundary (no live DB).
 */

interface CapturedUpsert {
  table: string;
  rows: Array<Record<string, unknown>>;
  options: unknown;
}
interface CapturedDelete {
  table: string;
  eqColumn: string;
  eqValue: unknown;
}

function makeFakeClient(
  config: { upsertError?: { message: string } | null; deleteError?: { message: string } | null } = {},
) {
  const upserts: CapturedUpsert[] = [];
  const deletes: CapturedDelete[] = [];
  const writeOrder: string[] = [];

  const client = {
    from: (table: string) => ({
      upsert: (rows: Array<Record<string, unknown>>, options: unknown) => {
        upserts.push({ table, rows, options });
        writeOrder.push(`upsert:${table}`);
        return Promise.resolve({ error: config.upsertError ?? null });
      },
      delete: () => ({
        eq(eqColumn: string, eqValue: unknown) {
          deletes.push({ table, eqColumn, eqValue });
          writeOrder.push(`delete:${table}`);
          return Promise.resolve({ error: config.deleteError ?? null });
        },
      }),
    }),
  };
  return { client: client as never, upserts, deletes, writeOrder };
}

const USER_ID = "00000000-0000-0000-0000-000000000abc";

function mute(category: string, term: string): TerminalMuteTerm {
  return { mute_category: category, mute_term: term };
}

describe("persistMuteTerms", () => {
  it("first-run upserts owner-scoped mute rows without deleting anything", async () => {
    const { client, upserts, deletes } = makeFakeClient();

    const result = await persistMuteTerms(USER_ID, [mute("sport", "transfer rumours"), mute("ai", "hype")], {}, client);

    expect(result.persisted_mute_count).toBe(2);
    expect(deletes).toHaveLength(0); // no replace → no destructive step
    const rows = upserts.find((u) => u.table === "user_mute_terms")?.rows ?? [];
    // Every row is scoped to the caller's id (owner-scoped write; RLS enforces it too).
    expect(rows.every((r) => r.mute_user_id === USER_ID)).toBe(true);
    expect(rows.map((r) => r.mute_term).sort()).toEqual(["hype", "transfer rumours"]);
    // The upsert targets the composite PK so re-persisting the same mute never duplicates.
    expect(upserts[0].options).toEqual({ onConflict: "mute_user_id,mute_category,mute_term" });
  });

  it("clean-replace deletes the whole prior set BEFORE inserting, leaving no orphans", async () => {
    const { client, upserts, deletes, writeOrder } = makeFakeClient();

    await persistMuteTerms(USER_ID, [mute("sport", "cricket gossip")], { replace_existing: true }, client);

    // The delete is owner-scoped and precedes the insert → the mute set ends EQUAL to the
    // new list; nothing from a prior run can survive (AC #5: no orphaned mutes).
    expect(deletes).toEqual([{ table: "user_mute_terms", eqColumn: "mute_user_id", eqValue: USER_ID }]);
    expect(writeOrder).toEqual(["delete:user_mute_terms", "upsert:user_mute_terms"]);
    const rows = upserts.find((u) => u.table === "user_mute_terms")?.rows ?? [];
    expect(rows).toHaveLength(1);
  });

  it("clean-replace with an EMPTY set clears mutes (valid) and inserts nothing", async () => {
    const { client, upserts, deletes } = makeFakeClient();

    const result = await persistMuteTerms(USER_ID, [], { replace_existing: true }, client);

    // Clearing mutes is legitimate (unlike interests) — delete happens, no insert, no orphans.
    expect(result.persisted_mute_count).toBe(0);
    expect(deletes).toHaveLength(1);
    expect(upserts).toHaveLength(0);
  });

  it("dedups case-insensitively and drops empty terms (no dead rows, no PK conflict)", async () => {
    const { client, upserts } = makeFakeClient();

    await persistMuteTerms(
      USER_ID,
      [mute("sport", "Crypto"), mute("sport", "crypto"), mute("sport", "   "), mute("", "orphan")],
      {},
      client,
    );

    const rows = upserts.find((u) => u.table === "user_mute_terms")?.rows ?? [];
    expect(rows).toHaveLength(1); // "Crypto"/"crypto" collapse; blank term + blank category dropped
    expect(rows[0].mute_term).toBe("Crypto");
  });

  it("surfaces an upsert failure loudly (Rule 12), never swallows it", async () => {
    const { client } = makeFakeClient({ upsertError: { message: "rls denied" } });

    await expect(persistMuteTerms(USER_ID, [mute("sport", "gossip")], {}, client)).rejects.toThrow(/rls denied/);
  });
});
