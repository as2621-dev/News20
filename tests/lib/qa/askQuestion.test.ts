/**
 * Tests for the grounded Q&A client's conversation-turns wiring (Bug 3).
 *
 * WHY (Rule 9 + multi-turn): a follow-up must ship the recent thread turns so
 * the worker can resolve references — and a FIRST question must omit the field
 * entirely (the old worker ignores it; the cache path stays warm). Failures
 * must still degrade to the safe refusal, never a thrown error.
 */

import { describe, expect, it, vi } from "vitest";
import { askQuestion, CLIENT_REFUSAL_ANSWER_TEXT, MAX_CONVERSATION_TURNS_SENT } from "@/lib/qa/askQuestion";
import type { QaConversationTurn } from "@/types/qa";

function buildFetchMock(body: unknown): typeof fetch {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => body,
  })) as unknown as typeof fetch;
}

const GROUNDED_BODY = {
  answer_text: "TSMC trades at approximately 24x forward earnings.",
  answer_citations: [],
  answer_is_grounded: true,
};

describe("askQuestion conversation turns", () => {
  it("includes conversation_turns in the body when turns are provided (happy path)", async () => {
    const fetchMock = buildFetchMock(GROUNDED_BODY);
    const turns: QaConversationTurn[] = [
      { role: "user", text: "What is the current PE of TSMC?" },
      { role: "model", text: "About 24x forward earnings." },
    ];

    await askQuestion("s1", "What about its margins?", turns, fetchMock);

    const requestInit = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0][1] as RequestInit;
    const parsedBody = JSON.parse(requestInit.body as string);
    expect(parsedBody.conversation_turns).toEqual(turns);
    expect(parsedBody.question_text).toBe("What about its margins?");
  });

  it("omits conversation_turns entirely on a first question", async () => {
    const fetchMock = buildFetchMock(GROUNDED_BODY);

    await askQuestion("s1", "Why does this matter?", [], fetchMock);

    const requestInit = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0][1] as RequestInit;
    const parsedBody = JSON.parse(requestInit.body as string);
    expect("conversation_turns" in parsedBody).toBe(false);
  });

  it("sends only the last MAX_CONVERSATION_TURNS_SENT turns (edge case)", async () => {
    const fetchMock = buildFetchMock(GROUNDED_BODY);
    const turns: QaConversationTurn[] = Array.from({ length: 10 }, (_unused, index) => ({
      role: "user" as const,
      text: `question number ${index}`,
    }));

    await askQuestion("s1", "Latest?", turns, fetchMock);

    const requestInit = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0][1] as RequestInit;
    const parsedBody = JSON.parse(requestInit.body as string);
    expect(parsedBody.conversation_turns).toHaveLength(MAX_CONVERSATION_TURNS_SENT);
    expect(parsedBody.conversation_turns[0].text).toBe("question number 4");
    expect(parsedBody.conversation_turns[5].text).toBe("question number 9");
  });

  it("still degrades to the safe refusal on network failure (failure case)", async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error("network down");
    }) as unknown as typeof fetch;

    const answer = await askQuestion("s1", "Anything?", [{ role: "user", text: "prior" }], fetchMock);

    expect(answer.answer_is_grounded).toBe(false);
    expect(answer.answer_text).toBe(CLIENT_REFUSAL_ANSWER_TEXT);
    expect(answer.answer_citations).toEqual([]);
  });
});
