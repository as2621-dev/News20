/**
 * Tests for the voice grounding-corpus client (voice-latency-hybrid-grounding SP2).
 *
 * WHY (Rule 9 + Rule 12): the live voice session embeds this corpus in its setup
 * frame, so the contract that MUST hold is "success → the context_block string;
 * EVERY failure → empty string". An empty string is the single seam that degrades
 * the session to tool-only voice — a thrown error or a non-string here would break
 * the session at connect time. These tests fail the moment any failure path stops
 * returning "" or starts throwing.
 *
 * `fetch` is mocked at the boundary (CLAUDE.md mocking rule): no real HTTP/worker.
 */

import { describe, expect, it, vi } from "vitest";
import { fetchStoryCorpus } from "@/lib/voice/fetchStoryCorpus";

/** A fetch mock that resolves to an HTTP 200 with the given JSON body. */
function buildOkFetchMock(body: unknown): typeof fetch {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => body,
  })) as unknown as typeof fetch;
}

const CONTEXT_BLOCK =
  "[detail_chunk:0] About 20% of global oil passes through Hormuz.\n[detail_chunk:1] Talks stalled.";

describe("fetchStoryCorpus — happy path", () => {
  it("returns the context_block string on an HTTP 200 well-formed body", async () => {
    const fetchMock = buildOkFetchMock({ context_block: CONTEXT_BLOCK, approx_token_count: 42 });

    const result = await fetchStoryCorpus("s1", fetchMock);

    expect(result).toBe(CONTEXT_BLOCK);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain("/api/story/s1/corpus");
    expect((init as RequestInit).method).toBe("GET");
  });

  it("returns an empty context_block verbatim (server's graceful empty) without inventing one", async () => {
    // WHY: the server returns 200 + "" on its own failures; the client must pass
    // that through untouched (it IS the tool-only fallback signal), not treat 200
    // as 'must have content'.
    const fetchMock = buildOkFetchMock({ context_block: "", approx_token_count: 0 });

    expect(await fetchStoryCorpus("s1", fetchMock)).toBe("");
  });
});

describe("fetchStoryCorpus — failure paths all degrade to empty string (Rule 12)", () => {
  it("returns '' on a non-200 response", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 500,
      json: async () => ({}),
    })) as unknown as typeof fetch;

    expect(await fetchStoryCorpus("s1", fetchMock)).toBe("");
  });

  it("returns '' when fetch itself throws (network down) — never rethrows", async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error("network down");
    }) as unknown as typeof fetch;

    await expect(fetchStoryCorpus("s1", fetchMock)).resolves.toBe("");
  });

  it("returns '' on a malformed body missing context_block", async () => {
    const fetchMock = buildOkFetchMock({ approx_token_count: 7 });

    expect(await fetchStoryCorpus("s1", fetchMock)).toBe("");
  });

  it("returns '' when context_block is a non-string", async () => {
    const fetchMock = buildOkFetchMock({ context_block: 123, approx_token_count: 7 });

    expect(await fetchStoryCorpus("s1", fetchMock)).toBe("");
  });

  it("returns '' when the body is not an object", async () => {
    const fetchMock = buildOkFetchMock("not-an-object");

    expect(await fetchStoryCorpus("s1", fetchMock)).toBe("");
  });
});
