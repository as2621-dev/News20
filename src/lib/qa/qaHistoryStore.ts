/**
 * Per-story typed-Q&A thread persistence (Bug 5) — localStorage, best-effort.
 *
 * The ask sheet is UNMOUNTED whenever it closes (BlipReel renders it behind a
 * ternary), so the thread must live outside component state to survive a
 * close→reopen — and the product call is that it also survives full app
 * restarts, hence localStorage (not sessionStorage).
 *
 * Storage shape: ONE key ({@link QA_HISTORY_STORAGE_KEY}) holding a
 * `Record<story_id, StoredQaThread>`. Bounded two ways so it can never bloat
 * the ~5 MB quota: each story keeps at most
 * {@link MAX_PERSISTED_TURNS_PER_STORY} turns, and at most
 * {@link MAX_PERSISTED_STORIES} stories are kept (oldest `updated_at_ms`
 * evicted first).
 *
 * Every read/write is SSR-guarded and try/caught (the
 * `src/lib/onboardingProfile.ts` pattern): corrupt or blocked storage degrades
 * to "no history", never an error the sheet can crash on.
 */

import { logger } from "@/lib/logger";
import type { CompletedQaTurn } from "@/types/qa";

/** The single localStorage key holding every story's persisted Q&A thread. */
export const QA_HISTORY_STORAGE_KEY = "blip-qa-history-v1";
/** Per-story turn cap — older turns are dropped on save. */
export const MAX_PERSISTED_TURNS_PER_STORY = 20;
/** Story cap — least-recently-updated threads are evicted on save. */
export const MAX_PERSISTED_STORIES = 20;

/** One story's persisted thread: completed turns + the unsent composer draft. */
export interface StoredQaThread {
  /** Completed question→answer exchanges, oldest first. */
  completed_turns: CompletedQaTurn[];
  /** The typed-but-unsent composer text at last save ("" when none). */
  draft_question_text: string;
  /** Last-save timestamp (ms epoch) — the story-eviction sort key. */
  updated_at_ms: number;
}

/** Narrow an unknown parsed JSON value to the full storage record, or null. */
function parseStorageRecord(raw: string): Record<string, StoredQaThread> | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return null;
  }
  const record: Record<string, StoredQaThread> = {};
  for (const [story_id, candidate] of Object.entries(parsed as Record<string, unknown>)) {
    if (typeof candidate !== "object" || candidate === null) {
      continue;
    }
    const thread = candidate as Record<string, unknown>;
    if (
      !Array.isArray(thread.completed_turns) ||
      typeof thread.draft_question_text !== "string" ||
      typeof thread.updated_at_ms !== "number"
    ) {
      continue;
    }
    record[story_id] = {
      completed_turns: thread.completed_turns as CompletedQaTurn[],
      draft_question_text: thread.draft_question_text,
      updated_at_ms: thread.updated_at_ms,
    };
  }
  return record;
}

/** Read the full storage record ({} on SSR / missing / corrupt). */
function readStorageRecord(): Record<string, StoredQaThread> {
  if (typeof window === "undefined" || !window.localStorage) {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(QA_HISTORY_STORAGE_KEY);
    if (raw === null) {
      return {};
    }
    const record = parseStorageRecord(raw);
    if (record === null) {
      logger.warn("qa_history_malformed", {
        fix_suggestion: "Stored Q&A history was not valid JSON; it will be overwritten on the next save.",
      });
      return {};
    }
    return record;
  } catch (error: unknown) {
    logger.warn("qa_history_read_failed", {
      error_message: error instanceof Error ? error.message : "unknown",
      fix_suggestion: "localStorage read failed (private mode?); the thread starts empty (harmless).",
    });
    return {};
  }
}

/**
 * Load one story's persisted Q&A thread.
 *
 * @param story_id - The reel `Story.digest_id` (= `stories.story_id`).
 * @returns The stored thread, or `null` when none exists / storage unavailable.
 *
 * @example
 * const thread = loadQaThreadForStory(story.digest_id);
 * const initialTurns = thread?.completed_turns ?? [];
 */
export function loadQaThreadForStory(story_id: string): StoredQaThread | null {
  return readStorageRecord()[story_id] ?? null;
}

/**
 * Save one story's Q&A thread (turn-capped) and evict the oldest stories
 * beyond {@link MAX_PERSISTED_STORIES}. Best-effort: failures are logged and
 * swallowed.
 *
 * @param story_id - The reel `Story.digest_id`.
 * @param thread - The turns + composer draft to persist.
 */
export function saveQaThreadForStory(
  story_id: string,
  thread: { completed_turns: CompletedQaTurn[]; draft_question_text: string },
): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  try {
    const record = readStorageRecord();
    record[story_id] = {
      completed_turns: thread.completed_turns.slice(-MAX_PERSISTED_TURNS_PER_STORY),
      draft_question_text: thread.draft_question_text,
      updated_at_ms: Date.now(),
    };
    const storyIdsByAge = Object.keys(record).sort((a, b) => record[a].updated_at_ms - record[b].updated_at_ms);
    while (storyIdsByAge.length > MAX_PERSISTED_STORIES) {
      const evictedStoryId = storyIdsByAge.shift();
      if (evictedStoryId !== undefined) {
        delete record[evictedStoryId];
      }
    }
    window.localStorage.setItem(QA_HISTORY_STORAGE_KEY, JSON.stringify(record));
  } catch (error: unknown) {
    logger.warn("qa_history_save_failed", {
      story_id,
      error_message: error instanceof Error ? error.message : "unknown",
      fix_suggestion: "localStorage write failed (quota/private mode); the thread lives only in memory this session.",
    });
  }
}

/**
 * Remove one story's persisted thread (e.g. a future "clear chat" affordance).
 *
 * @param story_id - The reel `Story.digest_id`.
 */
export function clearQaThreadForStory(story_id: string): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  try {
    const record = readStorageRecord();
    if (!(story_id in record)) {
      return;
    }
    delete record[story_id];
    window.localStorage.setItem(QA_HISTORY_STORAGE_KEY, JSON.stringify(record));
  } catch (error: unknown) {
    logger.warn("qa_history_clear_failed", {
      story_id,
      error_message: error instanceof Error ? error.message : "unknown",
      fix_suggestion: "localStorage write failed; the stale thread will be evicted by the story cap eventually.",
    });
  }
}
