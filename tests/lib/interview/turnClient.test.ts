import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Unit tests for `fetchInterviewTurn` (FSR slice #4 turn client).
 *
 * The Supabase session boundary is mocked (CLAUDE.md mocking rule); `fetch` is
 * passed as an injectable stub so no real network happens.
 *
 * Rule 9 — WHY each behavior matters:
 *   - The worker's `question` / `terminal` / `retry` shapes must round-trip
 *     unchanged, or the chat renders the wrong phase.
 *   - EVERY failure mode (no session, non-200, network error, malformed body) must
 *     degrade to a SYNTHETIC `retry` turn, never a throw — a thrown error would crash
 *     the interview into a dead-end screen (spec §2 forbids that).
 */

import { fetchInterviewTurn } from "@/lib/interview/turnClient";
import { getCurrentSession } from "@/lib/supabase/auth";

vi.mock("@/lib/supabase/auth", () => ({
  getCurrentSession: vi.fn(),
}));

const mockGetCurrentSession = vi.mocked(getCurrentSession);

/** A live session stub carrying an access token (the only field the client reads). */
function signedIn(): void {
  mockGetCurrentSession.mockResolvedValue({
    access_token: "jwt-abc",
    user: { id: "user-1" },
  } as Awaited<ReturnType<typeof getCurrentSession>>);
}

/** Build a fetch stub returning one JSON body with HTTP 200. */
function okFetch(body: unknown): typeof fetch {
  return vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })) as unknown as typeof fetch;
}

beforeEach(() => {
  mockGetCurrentSession.mockReset();
});

describe("fetchInterviewTurn", () => {
  it("returns a question turn with parsed bubbles on a question response", async () => {
    signedIn();
    const fetchStub = okFetch({
      response_kind: "question",
      turn_index: 0,
      question_text: "What are you into?",
      bubbles: [
        { bubble_label: "Sport", bubble_kind: "option" },
        { bubble_label: "not really / skip", bubble_kind: "skip" },
        { bubble_label: "something else — type it", bubble_kind: "type_your_own" },
      ],
    });

    const turn = await fetchInterviewTurn([], fetchStub);

    expect(turn.response_kind).toBe("question");
    if (turn.response_kind === "question") {
      expect(turn.question_text).toBe("What are you into?");
      expect(turn.bubbles).toHaveLength(3);
      expect(turn.bubbles[1].bubble_kind).toBe("skip");
    }
  });

  it("sends the JWT bearer header and the conversation_state body", async () => {
    signedIn();
    const fetchStub = okFetch({ response_kind: "terminal", turn_index: 1, micro_interests: [] });
    const state = [{ question_text: "q", bubbles_offered: ["a"], bubbles_tapped: ["a"], free_text_entered: null }];

    await fetchInterviewTurn(state, fetchStub);

    const [, init] = (fetchStub as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer jwt-abc");
    expect(JSON.parse(init.body as string)).toEqual({ conversation_state: state });
  });

  it("returns a terminal turn (micro-interests + roots_only_fallback)", async () => {
    signedIn();
    const fetchStub = okFetch({
      response_kind: "terminal",
      turn_index: 3,
      micro_interests: [
        {
          display_label: "IPL auctions",
          canonical_slug: "sport.cricket.ipl.auctions",
          ladder: ["sport", "sport.cricket", "sport.cricket.ipl"],
          search_anchor_terms: ["IPL auction", "player transfer"],
          strict: false,
        },
      ],
      roots_only_fallback: false,
    });

    const turn = await fetchInterviewTurn([], fetchStub);

    expect(turn.response_kind).toBe("terminal");
    if (turn.response_kind === "terminal") {
      expect(turn.micro_interests[0].canonical_slug).toBe("sport.cricket.ipl.auctions");
    }
  });

  it("narrows deferred_questions off the terminal body, dropping unknown-kind records", async () => {
    // WHY (#30): the engine already emits skipped-question records; the client dropped them.
    // We keep only well-formed records (known deferral_kind + string question_text) so a
    // malformed one never reaches the persister — and top_split is NEVER read off the worker.
    signedIn();
    const fetchStub = okFetch({
      response_kind: "terminal",
      turn_index: 4,
      micro_interests: [],
      roots_only_fallback: true,
      deferred_questions: [
        { deferral_kind: "category_skip", question_text: "Which sports?", root_slug: "sport", subniche_label: null },
        { deferral_kind: "who_drill_skip", question_text: "Who in AI?", root_slug: "ai", subniche_label: "llms" },
        { deferral_kind: "not_a_real_kind", question_text: "dropped", root_slug: null, subniche_label: null },
        { question_text: "no kind → dropped" },
      ],
      // A worker that leaks a top_split must NOT taint the client-computed field.
      top_split: { news: 99, youtube: 99, x: 99 },
    });

    const turn = await fetchInterviewTurn([], fetchStub);

    expect(turn.response_kind).toBe("terminal");
    if (turn.response_kind === "terminal") {
      expect(turn.deferred_questions).toEqual([
        { deferral_kind: "category_skip", question_text: "Which sports?", root_slug: "sport", subniche_label: null },
        { deferral_kind: "who_drill_skip", question_text: "Who in AI?", root_slug: "ai", subniche_label: "llms" },
      ]);
      // top_split is client-computed — the terminal turn type never carries it.
      expect((turn as unknown as Record<string, unknown>).top_split).toBeUndefined();
    }
  });

  it("passes a worker retry body through as a retry turn", async () => {
    signedIn();
    const fetchStub = okFetch({
      response_kind: "retry",
      turn_index: 2,
      error_code: "llm_unavailable",
      error_message: "Try again",
      retry_hint: "hint",
    });

    const turn = await fetchInterviewTurn([], fetchStub);

    expect(turn.response_kind).toBe("retry");
    if (turn.response_kind === "retry") {
      expect(turn.error_code).toBe("llm_unavailable");
    }
  });

  it("synthesizes a retry turn when there is no session (never throws)", async () => {
    mockGetCurrentSession.mockResolvedValue(null);
    const fetchStub = okFetch({ response_kind: "question", turn_index: 0, question_text: "x", bubbles: [] });

    const turn = await fetchInterviewTurn([], fetchStub);

    expect(turn.response_kind).toBe("retry");
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("synthesizes a retry turn on a non-200 response", async () => {
    signedIn();
    const fetchStub = vi.fn(async () => new Response("nope", { status: 500 })) as unknown as typeof fetch;

    const turn = await fetchInterviewTurn([], fetchStub);

    expect(turn.response_kind).toBe("retry");
    if (turn.response_kind === "retry") {
      expect(turn.error_code).toBe("http_error");
    }
  });

  it("synthesizes a retry turn on a network error (fetch rejects)", async () => {
    signedIn();
    const fetchStub = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;

    const turn = await fetchInterviewTurn([], fetchStub);

    expect(turn.response_kind).toBe("retry");
    if (turn.response_kind === "retry") {
      expect(turn.error_code).toBe("transport_error");
    }
  });

  it("synthesizes a retry turn on a malformed/unknown body shape", async () => {
    signedIn();
    const fetchStub = okFetch({ response_kind: "who_knows", turn_index: 0 });

    const turn = await fetchInterviewTurn([], fetchStub);

    expect(turn.response_kind).toBe("retry");
    if (turn.response_kind === "retry") {
      expect(turn.error_code).toBe("malformed_body");
    }
  });
});
