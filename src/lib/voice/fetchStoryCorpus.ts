/**
 * Voice grounding-corpus client (voice-latency-hybrid-grounding SP2) for the live
 * ask sheet of the audio-first karaoke reel (blip / "News20").
 *
 * Calls the SP1 worker endpoint `GET /api/story/{story_id}/corpus` and returns the
 * `context_block` string — the newline-joined `[passage_id] text` lines the live
 * Voice model embeds in its `systemInstruction` so it can answer corpus-answerable
 * questions DIRECTLY (no Railway hop, grounded by construction). Mirrors
 * {@link askQuestion}: same base-URL resolution, same injectable `fetchImpl`, same
 * graceful HTTP-200-or-bust posture.
 *
 * **Graceful by contract (Rule 12 boundary).** The server itself always returns
 * HTTP 200, even on failure, with an empty `context_block`. This client mirrors
 * that: ANY failure (network throw, non-200, malformed body, missing/non-string
 * `context_block`) returns `""` (empty string) and logs a structured warning. An
 * empty corpus is the single graceful-degradation seam — the caller falls back to
 * the tool-only voice instruction. This function NEVER throws.
 *
 * @example
 * const corpus = await fetchStoryCorpus("s1");
 * // corpus === ""  → caller uses buildInNewsSystemInstruction (tool-only)
 * // corpus !== ""  → caller uses buildInNewsSystemInstructionWithCorpus(corpus)
 */

import { logger } from "@/lib/logger";

/**
 * Resolve the Q&A worker base URL. Empty string (the default) makes the request a
 * same-origin relative path (`/api/story/...`), which is the right behaviour when a
 * reverse-proxy/dev rewrite fronts the worker; set `NEXT_PUBLIC_QA_API_BASE_URL` to
 * the deployed worker origin (e.g. `https://worker.example.com`) for the Capacitor
 * static build, which has no same-origin server.
 *
 * Replicated from {@link askQuestion}'s private `getQaApiBaseUrl` (not exported
 * there) so the two clients resolve identically without SP2 modifying SP3's file.
 *
 * @returns The base URL with any trailing slash stripped, or `""` for same-origin.
 */
function getQaApiBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_QA_API_BASE_URL ?? "";
  return base.replace(/\/+$/, "");
}

/**
 * Narrow an unknown JSON body to its `context_block` string.
 *
 * The SP1 contract is `{ context_block: string, approx_token_count: number }`; we
 * only consume `context_block`. A malformed body (not an object, or a non-string
 * `context_block`) yields `null` so the caller degrades to `""`.
 *
 * @param body - The parsed JSON response body (unknown shape).
 * @returns The `context_block` string, or `null` when the shape is invalid.
 */
function parseContextBlock(body: unknown): string | null {
  if (typeof body !== "object" || body === null) {
    return null;
  }
  const candidate = body as Record<string, unknown>;
  if (typeof candidate.context_block !== "string") {
    return null;
  }
  return candidate.context_block;
}

/**
 * Fetch one story's grounding corpus as a `[passage_id] text` context block.
 *
 * GETs `GET /api/story/{story_id}/corpus` and returns the `context_block`. Every
 * failure mode (network error, non-200, malformed JSON, missing `context_block`)
 * degrades to `""` — the voice session never breaks; an empty corpus simply routes
 * the caller to the tool-only system instruction (Rule 12).
 *
 * @param story_id - The `stories.story_id` slug (the reel `Story.digest_id`).
 * @param fetchImpl - Injectable fetch (defaults to the global `fetch`; tests pass a mock).
 * @returns The corpus context block on success, or `""` on any failure.
 *
 * @example
 * const corpus = await fetchStoryCorpus("s1");
 * const instruction = corpus
 *   ? buildInNewsSystemInstructionWithCorpus(headline, "s1", corpus, clause)
 *   : buildInNewsSystemInstruction(headline, "s1", clause);
 */
export async function fetchStoryCorpus(story_id: string, fetchImpl: typeof fetch = fetch): Promise<string> {
  const endpoint = `${getQaApiBaseUrl()}/api/story/${encodeURIComponent(story_id)}/corpus`;
  logger.info("fetch_story_corpus_started", { story_id });

  try {
    const response = await fetchImpl(endpoint, { method: "GET" });

    if (!response.ok) {
      logger.warn("fetch_story_corpus_non_200", {
        story_id,
        status: response.status,
        fix_suggestion:
          "Confirm the Q&A worker is deployed and NEXT_PUBLIC_QA_API_BASE_URL points at it; the corpus endpoint should return HTTP 200 even on failure.",
      });
      return "";
    }

    const body: unknown = await response.json();
    const context_block = parseContextBlock(body);
    if (context_block === null) {
      logger.warn("fetch_story_corpus_malformed_body", {
        story_id,
        fix_suggestion: "Endpoint must return { context_block: string, approx_token_count: number }.",
      });
      return "";
    }

    logger.info("fetch_story_corpus_completed", {
      story_id,
      context_block_length: context_block.length,
    });
    return context_block;
  } catch (error: unknown) {
    logger.warn("fetch_story_corpus_failed", {
      story_id,
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion: "Check network connectivity and that the Q&A worker corpus endpoint is reachable over HTTPS.",
    });
    return "";
  }
}
