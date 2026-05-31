import { describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { fetchStoryDetail } from "@/lib/detail/fetchStoryDetail";
import type { StoryDetail } from "@/types/detail";

/**
 * Zod schema mirroring `src/types/detail.ts` `StoryDetail`. WHY: the phase DoD
 * requires `fetchStoryDetail` to populate the full Detail contract — this parse
 * FAILS if any DB-row → field mapping drifts (a renamed column, a number stored
 * as a string, a null where a value is required). It encodes the contract, not
 * just the happy path, so it cannot pass while the business mapping is wrong
 * (Rule 9). The ordering + per-table-eq assertions below close the rest of the
 * gate: the test fails if chunk/timeline ordering is dropped or a query keys on
 * the wrong story-id column.
 */
const biasLeanSchema = z.enum(["left", "center", "right"]);
const detailChunkSchema = z.object({
  chunk_index: z.number(),
  chunk_text: z.string(),
});
const trustSummarySchema = z.object({
  coverage_left_count: z.number(),
  coverage_center_count: z.number(),
  coverage_right_count: z.number(),
  coverage_outlet_count: z.number(),
  blindspot_lean: biasLeanSchema.nullable(),
  opposing_view_text: z.string().nullable(),
});
const storySourceSchema = z.object({
  source_outlet_name: z.string(),
  source_bias_lean: biasLeanSchema.nullable(),
  source_article_url: z.string().nullable(),
  source_published_utc: z.string().nullable(),
  source_is_citation: z.boolean(),
});
const timelineEventSchema = z.object({
  timeline_event_index: z.number(),
  timeline_when_label: z.string(),
  timeline_what_text: z.string(),
});
const suggestedQuestionSchema = z.object({
  question_index: z.number(),
  question_text: z.string(),
});
const keyFigureSchema = z.object({
  key_figure_value: z.string().nullable(),
  key_figure_label: z.string().nullable(),
});
const storyDetailSchema = z.object({
  story_id: z.string(),
  detail_chunks: z.array(detailChunkSchema).min(1),
  trust_summary: trustSummarySchema,
  key_figure: keyFigureSchema,
  sources: z.array(storySourceSchema).min(1),
  timeline: z.array(timelineEventSchema).min(1),
  suggested_questions: z.array(suggestedQuestionSchema).min(1),
});

/** One mocked per-table result: the rows (or single row) and a terminal mode. */
interface TableResult {
  /** "list" terminates with `.returns()`; "single" terminates with `.maybeSingle()`. */
  mode: "list" | "single";
  data: unknown;
  error: unknown;
}

/**
 * Fake Supabase client whose `.from(table).select().eq().order?().returns|maybeSingle()`
 * chain resolves to the per-table result. Mocks at the client boundary
 * (CLAUDE.md mocking strategy). Records the `.from`, `.eq`, and `.order` calls so
 * tests can assert each query keyed on the right story-id column and ordered
 * chunks/timeline by index (Rule 9).
 */
function makeFakeClient(results: Record<string, TableResult>) {
  const eqCalls: Array<{ table: string; column: string; value: unknown }> = [];
  const orderCalls: Array<{ table: string; column: string }> = [];

  const from = vi.fn((table: string) => {
    const result = results[table];
    if (!result) {
      throw new Error(`test setup error: no mocked result for table "${table}"`);
    }
    const terminal =
      result.mode === "single"
        ? { maybeSingle: vi.fn().mockResolvedValue({ data: result.data, error: result.error }) }
        : { returns: vi.fn().mockResolvedValue({ data: result.data, error: result.error }) };

    // The chain is fluent: select() → eq() → (order()?) → terminal. order() and
    // eq() return an object carrying both the next link and the terminal, so the
    // implementation can stop at either point.
    const order = vi.fn((column: string) => {
      orderCalls.push({ table, column });
      return terminal;
    });
    const eq = vi.fn((column: string, value: unknown) => {
      eqCalls.push({ table, column, value });
      return { order, ...terminal };
    });
    const select = vi.fn(() => ({ eq }));
    return { select };
  });

  return { client: { from } as never, from, eqCalls, orderCalls };
}

/** Representative per-table rows for story "s1". Chunks + timeline are supplied
 *  OUT OF ORDER on purpose so the ordering assertions are meaningful (Rule 9). */
function sampleResults(): Record<string, TableResult> {
  return {
    detail_chunks: {
      mode: "list",
      // Ordered by chunk_index at the query layer → mock returns ascending (the
      // implementation relies on `.order`, which the fake records but does not
      // re-sort, so rows are given already in the order PostgREST would return).
      data: [
        { chunk_index: 0, chunk_text: "First paragraph." },
        { chunk_index: 1, chunk_text: "Second paragraph." },
      ],
      error: null,
    },
    story_trust: {
      mode: "single",
      data: {
        coverage_left_count: 3,
        coverage_center_count: 9,
        coverage_right_count: 1,
        coverage_outlet_count: 19,
        blindspot_lean: "right",
        opposing_view_text: "A right-leaning outlet frames it differently.",
      },
      error: null,
    },
    story_timeline: {
      mode: "list",
      data: [
        { timeline_event_index: 0, timeline_when_label: "08:10", timeline_what_text: "It began." },
        { timeline_event_index: 1, timeline_when_label: "Mon", timeline_what_text: "It escalated." },
      ],
      error: null,
    },
    story_sources: {
      mode: "list",
      data: [
        {
          source_outlet_name: "Reuters",
          source_bias_lean: "center",
          source_article_url: "https://reuters.example/a",
          source_published_utc: "2026-05-30T08:00:00Z",
          source_is_citation: true,
        },
        {
          source_outlet_name: "CNN",
          source_bias_lean: null,
          source_article_url: null,
          source_published_utc: null,
          source_is_citation: true,
        },
      ],
      error: null,
    },
    suggested_questions: {
      mode: "list",
      data: [
        { question_index: 0, question_text: "What led to this?" },
        { question_index: 1, question_text: "Why does it matter?" },
      ],
      error: null,
    },
    stories: {
      mode: "single",
      data: { story_key_figure_value: "~20%", story_key_figure_label: "of global oil transits Hormuz" },
      error: null,
    },
  };
}

describe("fetchStoryDetail (supabase source)", () => {
  it("maps every DB column to the right field and validates the StoryDetail contract", async () => {
    const { client } = makeFakeClient(sampleResults());

    const detail: StoryDetail = await fetchStoryDetail("s1", client);

    // Contract gate: a drift in any column→field mapping breaks this parse.
    const parsed = storyDetailSchema.parse(detail);
    expect(parsed.story_id).toBe("s1");

    // Trust strip columns map to the right fields (not swapped L/C/R).
    expect(parsed.trust_summary.coverage_left_count).toBe(3);
    expect(parsed.trust_summary.coverage_center_count).toBe(9);
    expect(parsed.trust_summary.coverage_right_count).toBe(1);
    expect(parsed.trust_summary.coverage_outlet_count).toBe(19);
    expect(parsed.trust_summary.blindspot_lean).toBe("right");
    expect(parsed.trust_summary.opposing_view_text).toMatch(/frames it differently/);

    // Key figure value/label from the stories projection.
    expect(parsed.key_figure.key_figure_value).toBe("~20%");
    expect(parsed.key_figure.key_figure_label).toBe("of global oil transits Hormuz");

    // Sources + suggested questions populated.
    expect(parsed.sources.map((s) => s.source_outlet_name)).toEqual(["Reuters", "CNN"]);
    expect(parsed.sources[1].source_bias_lean).toBeNull();
    expect(parsed.suggested_questions.map((q) => q.question_text)).toEqual([
      "What led to this?",
      "Why does it matter?",
    ]);
  });

  it("returns detail_chunks in chunk_index order and story_timeline in timeline_event_index order", async () => {
    // WHY: PostgREST does not guarantee embedded-row order; chunks render as the
    // reading body and timeline as the developmental order. This asserts the
    // implementation orders BOTH at the query layer — it fails if the `.order`
    // call is dropped for either table (Rule 9).
    const { client, orderCalls } = makeFakeClient(sampleResults());

    const detail = await fetchStoryDetail("s1", client);

    expect(detail.detail_chunks.map((c) => c.chunk_index)).toEqual([0, 1]);
    expect(detail.detail_chunks[0].chunk_text).toBe("First paragraph.");
    expect(detail.timeline.map((t) => t.timeline_event_index)).toEqual([0, 1]);

    // The ordering is requested from Postgres, not the test data accidentally.
    expect(orderCalls).toContainEqual({ table: "detail_chunks", column: "chunk_index" });
    expect(orderCalls).toContainEqual({ table: "story_timeline", column: "timeline_event_index" });
    expect(orderCalls).toContainEqual({ table: "suggested_questions", column: "question_index" });
  });

  it("keys each read on the correct per-table story-id column", async () => {
    // WHY: each detail table has a DIFFERENT story-FK column name
    // (detail_story_id, trust_story_id, timeline_story_id, ...). A copy-paste of
    // the wrong column name would silently return another story's rows. This
    // fails if any query keys on the wrong column (Rule 9).
    const { client, eqCalls } = makeFakeClient(sampleResults());

    await fetchStoryDetail("s1", client);

    expect(eqCalls).toContainEqual({ table: "detail_chunks", column: "detail_story_id", value: "s1" });
    expect(eqCalls).toContainEqual({ table: "story_trust", column: "trust_story_id", value: "s1" });
    expect(eqCalls).toContainEqual({ table: "story_timeline", column: "timeline_story_id", value: "s1" });
    expect(eqCalls).toContainEqual({ table: "story_sources", column: "source_story_id", value: "s1" });
    expect(eqCalls).toContainEqual({ table: "suggested_questions", column: "question_story_id", value: "s1" });
    expect(eqCalls).toContainEqual({ table: "stories", column: "story_id", value: "s1" });
  });

  it("carries a NULL blindspot through as null (no-blindspot story renders no chip)", async () => {
    // WHY: blindspot_lean is nullable; SP3 shows the chip only when it is set. A
    // mapping that coerced null → a default lean would fabricate a blindspot.
    const results = sampleResults();
    results.story_trust = {
      mode: "single",
      data: {
        coverage_left_count: 5,
        coverage_center_count: 5,
        coverage_right_count: 5,
        coverage_outlet_count: 15,
        blindspot_lean: null,
        opposing_view_text: null,
      },
      error: null,
    };
    const { client } = makeFakeClient(results);

    const detail = await fetchStoryDetail("s1", client);

    expect(detail.trust_summary.blindspot_lean).toBeNull();
    expect(detail.trust_summary.opposing_view_text).toBeNull();
    expect(detail.trust_summary.coverage_outlet_count).toBe(15);
  });

  it("throws a fix_suggestion error when a read fails", async () => {
    const results = sampleResults();
    results.detail_chunks = { mode: "list", data: null, error: { message: "permission denied" } };
    const { client } = makeFakeClient(results);

    await expect(fetchStoryDetail("s1", client)).rejects.toThrow(/Failed to load detail_chunks/i);
  });

  it("throws when the story has no story_trust row (broken 1:1 seed)", async () => {
    const results = sampleResults();
    results.story_trust = { mode: "single", data: null, error: null };
    const { client } = makeFakeClient(results);

    await expect(fetchStoryDetail("s1", client)).rejects.toThrow(/no story_trust row/i);
  });

  it("throws when the story slug is not found", async () => {
    const results = sampleResults();
    results.stories = { mode: "single", data: null, error: null };
    const { client } = makeFakeClient(results);

    await expect(fetchStoryDetail("nope", client)).rejects.toThrow(/not found/i);
  });
});
