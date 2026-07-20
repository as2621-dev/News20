import { describe, expect, it } from "vitest";
import { persistDeferredQuestions } from "@/lib/interviewProfile";
import type { InterviewDeferredQuestion } from "@/types/interview";

/**
 * WHY these tests exist (Rule 9, #30): a deferred record is a SKIPPED question kept for later
 * in-app resurfacing. It must be owner-scoped, and on a rebuild-my-feed re-run the set must end
 * EQUAL to the re-interview's list with NO orphans (a stale record would re-ask a question the
 * user already answered on the second pass). Deferred records carry no natural conflict key, so
 * the clean-replace is delete-all-then-insert. Mocks the Supabase client at the insert/delete
 * boundary (mirrors tests/lib/muteTerms.test.ts).
 */

interface CapturedInsert {
  table: string;
  rows: Array<Record<string, unknown>>;
}
interface CapturedDelete {
  table: string;
  eqColumn: string;
  eqValue: unknown;
}

function makeFakeClient(
  config: { insertError?: { message: string } | null; deleteError?: { message: string } | null } = {},
) {
  const inserts: CapturedInsert[] = [];
  const deletes: CapturedDelete[] = [];
  const writeOrder: string[] = [];

  const client = {
    from: (table: string) => ({
      insert: (rows: Array<Record<string, unknown>>) => {
        inserts.push({ table, rows });
        writeOrder.push(`insert:${table}`);
        return Promise.resolve({ error: config.insertError ?? null });
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
  return { client: client as never, inserts, deletes, writeOrder };
}

const USER_ID = "00000000-0000-0000-0000-000000000abc";

function deferred(
  deferral_kind: InterviewDeferredQuestion["deferral_kind"],
  question_text: string,
  root_slug: string | null = null,
  subniche_label: string | null = null,
): InterviewDeferredQuestion {
  return { deferral_kind, question_text, root_slug, subniche_label };
}

describe("persistDeferredQuestions", () => {
  it("first-run non-empty write deletes-first-then-inserts (idempotent) with owner-scoped rows", async () => {
    // WHY: deferred rows have a surrogate PK and NO natural conflict key, so a bare insert is not
    // idempotent — a lost-response retry would double-insert. Deleting first (even first-run) makes
    // a retry converge to the SAME set. We assert the owner-scoped delete precedes the insert.
    const { client, inserts, deletes, writeOrder } = makeFakeClient();

    const result = await persistDeferredQuestions(
      USER_ID,
      [deferred("category_skip", "Which sports?", "sport"), deferred("who_drill_skip", "Who in AI?", "ai", "llms")],
      {},
      client,
    );

    expect(result.persisted_deferred_count).toBe(2);
    expect(deletes).toEqual([{ table: "user_deferred_questions", eqColumn: "deferred_user_id", eqValue: USER_ID }]);
    expect(writeOrder).toEqual(["delete:user_deferred_questions", "insert:user_deferred_questions"]);
    const rows = inserts.find((i) => i.table === "user_deferred_questions")?.rows ?? [];
    expect(rows.every((r) => r.deferred_user_id === USER_ID)).toBe(true);
    expect(rows.map((r) => r.deferred_kind)).toEqual(["category_skip", "who_drill_skip"]);
    // Nullable columns map to null (never an empty string) and the WHO drill keeps its subniche.
    expect(rows[0].deferred_root_slug).toBe("sport");
    expect(rows[0].deferred_subniche_label).toBeNull();
    expect(rows[1].deferred_subniche_label).toBe("llms");
  });

  it("clean-replace deletes the whole prior set BEFORE inserting, leaving no orphans", async () => {
    const { client, inserts, deletes, writeOrder } = makeFakeClient();

    await persistDeferredQuestions(
      USER_ID,
      [deferred("roots_skip", "Pick some topics?")],
      { replace_existing: true },
      client,
    );

    expect(deletes).toEqual([{ table: "user_deferred_questions", eqColumn: "deferred_user_id", eqValue: USER_ID }]);
    expect(writeOrder).toEqual(["delete:user_deferred_questions", "insert:user_deferred_questions"]);
    expect(inserts).toHaveLength(1);
  });

  it("clean-replace with an EMPTY set clears deferred records (valid) and inserts nothing", async () => {
    const { client, inserts, deletes } = makeFakeClient();

    const result = await persistDeferredQuestions(USER_ID, [], { replace_existing: true }, client);

    expect(result.persisted_deferred_count).toBe(0);
    expect(deletes).toHaveLength(1);
    expect(inserts).toHaveLength(0);
  });

  it("first-run EMPTY set is a pure no-op (no delete, no insert)", async () => {
    const { client, inserts, deletes } = makeFakeClient();

    const result = await persistDeferredQuestions(USER_ID, [], {}, client);

    expect(result.persisted_deferred_count).toBe(0);
    expect(deletes).toHaveLength(0);
    expect(inserts).toHaveLength(0);
  });

  it("drops a record with an unknown deferral_kind (migration 0030 CHECK would reject it)", async () => {
    const { client, inserts } = makeFakeClient();

    const result = await persistDeferredQuestions(
      USER_ID,
      [
        deferred("category_skip", "kept"),
        {
          deferral_kind: "not_a_kind" as InterviewDeferredQuestion["deferral_kind"],
          question_text: "dropped",
          root_slug: null,
          subniche_label: null,
        },
      ],
      {},
      client,
    );

    expect(result.persisted_deferred_count).toBe(1);
    const rows = inserts.find((i) => i.table === "user_deferred_questions")?.rows ?? [];
    expect(rows).toHaveLength(1);
    expect(rows[0].deferred_question_text).toBe("kept");
  });

  it("surfaces an insert failure loudly (Rule 12), never swallows it", async () => {
    const { client } = makeFakeClient({ insertError: { message: "rls denied" } });

    await expect(persistDeferredQuestions(USER_ID, [deferred("roots_skip", "q")], {}, client)).rejects.toThrow(
      /rls denied/,
    );
  });
});
