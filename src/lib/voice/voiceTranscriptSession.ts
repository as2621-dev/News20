/**
 * In-session voice transcript store (issue #40) — memory-only, per story.
 *
 * The voice ask sheet UNMOUNTS whenever it closes or the user switches to the
 * typed sheet, so its rolling transcript must live outside component state to
 * survive a switch-away-and-back WITHIN the app session. Unlike the typed
 * thread (`qaHistoryStore`, localStorage), this store is deliberately
 * memory-only: cross-session persistence of voice transcripts is explicitly
 * OUT of scope for this slice, and a spoken transcript is more ephemeral by
 * nature than a typed thread.
 *
 * Bounded two ways so a long session can never grow unbounded: each story
 * keeps at most {@link MAX_SESSION_TURNS_PER_STORY} turns (newest kept), and
 * at most {@link MAX_SESSION_STORIES} stories are retained (oldest-saved
 * evicted first — mirrors `qaHistoryStore`'s eviction posture).
 */

/** One turn in the spoken thread — a user utterance or a model answer. */
export interface VoiceTranscriptTurn {
  /** Whether this turn was spoken by the user or the model. */
  role: "user" | "model";
  /** The transcribed text for this turn (grows while the turn streams). */
  text: string;
}

/** Per-story turn cap — older turns are dropped on save (newest kept). */
export const MAX_SESSION_TURNS_PER_STORY = 40;
/** Story cap — least-recently-saved transcripts are evicted on save. */
export const MAX_SESSION_STORIES = 20;

/** Module-level session store: story_id → that story's spoken turns. */
const sessionTranscriptsByStoryId = new Map<string, VoiceTranscriptTurn[]>();

/**
 * Load one story's in-session voice transcript.
 *
 * @param story_id - The reel `Story.digest_id`.
 * @returns A copy of the stored turns, oldest first (`[]` when none).
 *
 * @example
 * const initialTurns = loadVoiceTranscriptForStory(story.digest_id);
 */
export function loadVoiceTranscriptForStory(story_id: string): VoiceTranscriptTurn[] {
  // Reason: return a copy so callers (React state) can never mutate the store.
  return [...(sessionTranscriptsByStoryId.get(story_id) ?? [])];
}

/**
 * Save one story's voice transcript (turn-capped, story-capped).
 *
 * @param story_id - The reel `Story.digest_id`.
 * @param turns - The full spoken thread, oldest first.
 */
export function saveVoiceTranscriptForStory(story_id: string, turns: VoiceTranscriptTurn[]): void {
  // Reason: Map iteration order is insertion order — delete-then-set moves this
  // story to the newest slot so eviction below drops the least-recently-saved.
  sessionTranscriptsByStoryId.delete(story_id);
  sessionTranscriptsByStoryId.set(story_id, turns.slice(-MAX_SESSION_TURNS_PER_STORY));
  while (sessionTranscriptsByStoryId.size > MAX_SESSION_STORIES) {
    const oldestStoryId = sessionTranscriptsByStoryId.keys().next().value;
    if (oldestStoryId === undefined) {
      break;
    }
    sessionTranscriptsByStoryId.delete(oldestStoryId);
  }
}

/**
 * Drop every stored transcript. Test hygiene + a future "clear chat" seam.
 */
export function clearAllVoiceTranscriptSessions(): void {
  sessionTranscriptsByStoryId.clear();
}
