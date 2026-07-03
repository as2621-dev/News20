/**
 * Interview resume state (FSR slice #4) — best-effort `localStorage` persistence of
 * the in-progress conversation so a mid-interview abandon (kill the app / navigate
 * away) offers resume-or-restart on next open (acceptance criterion: abandonment
 * never strands the user, and `user_onboarded_at` stays unset until the true flow
 * end — this stores ONLY the transcript, never a completion stamp).
 *
 * Mechanism choice (Rule 7 — same call the source-step marker made): this is
 * transient UX state, NOT security- or profile-bearing (the real profile is minted
 * RLS-scoped only at confirm). So it uses `localStorage`, mirroring
 * `markSourceOnboardingComplete` / `signals.ts` — SSR-safe, try/catch, best-effort.
 * A write/read failure degrades to "no resume" (a clean restart), never a hard error.
 *
 * NOTE: only the raw exchanges (the turn transcript) are stored — never the terminal
 * micro-interest list (spec §4: the raw chat is not persisted server-side; the client
 * cache is likewise transcript-only and is cleared the moment the interview confirms).
 */

import { logger } from "@/lib/logger";
import type { InterviewExchange } from "@/types/interview";

/** The `localStorage` key holding the in-progress interview transcript. */
const INTERVIEW_SESSION_STORAGE_KEY = "n20-interview-session";

/** The persisted resume shape — versioned so a schema change can invalidate cleanly. */
interface StoredInterviewSession {
  /** Schema version; a mismatch on read is treated as "no resume" (safe restart). */
  version: 1;
  /** The ordered exchanges completed so far (the stateless-worker conversation state). */
  conversation_state: InterviewExchange[];
  /** When it was last written (epoch ms) — surfaced for the resume prompt / debugging. */
  saved_at_ms: number;
}

/** The current resume schema version. */
const CURRENT_SESSION_VERSION = 1 as const;

/**
 * Persist the in-progress interview transcript so it can be resumed after an
 * abandon/reload. Best-effort: a `localStorage` failure (private mode / quota) is
 * logged and swallowed — losing resume must never break the live interview.
 *
 * An EMPTY `conversationState` clears any stored session (the interview is back at
 * the very first turn — nothing meaningful to resume).
 *
 * @param conversationState - The exchanges completed so far.
 */
export function saveInterviewSession(conversationState: InterviewExchange[]): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  if (conversationState.length === 0) {
    clearInterviewSession();
    return;
  }
  try {
    const payload: StoredInterviewSession = {
      version: CURRENT_SESSION_VERSION,
      conversation_state: conversationState,
      saved_at_ms: Date.now(),
    };
    window.localStorage.setItem(INTERVIEW_SESSION_STORAGE_KEY, JSON.stringify(payload));
  } catch (error: unknown) {
    logger.warn("interview_session_save_failed", {
      error_message: error instanceof Error ? error.message : "unknown",
      fix_suggestion:
        "localStorage write failed (private mode / quota); resume is unavailable (harmless — restart works).",
    });
  }
}

/**
 * Load a resumable interview transcript from THIS device, or `null` when there is
 * none (nothing stored, wrong version, corrupt JSON, empty transcript, or storage
 * unavailable). Defaulting to `null` (a clean restart) is the safe failure — a
 * corrupt cache must never trap the user in a broken interview.
 *
 * @returns The stored exchanges to resume from, or `null` for a fresh start.
 */
export function loadInterviewSession(): InterviewExchange[] | null {
  if (typeof window === "undefined" || !window.localStorage) {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(INTERVIEW_SESSION_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<StoredInterviewSession>;
    if (
      parsed.version !== CURRENT_SESSION_VERSION ||
      !Array.isArray(parsed.conversation_state) ||
      parsed.conversation_state.length === 0
    ) {
      return null;
    }
    return parsed.conversation_state;
  } catch {
    // Reason: corrupt/blocked storage must never trap the user — default to "no
    // resume" so the interview starts clean (worst case: they answer again).
    return null;
  }
}

/**
 * Clear any stored interview transcript. Called on terminal confirm (the profile is
 * now minted — the cache is stale) and on an explicit "start over". Best-effort.
 */
export function clearInterviewSession(): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.removeItem(INTERVIEW_SESSION_STORAGE_KEY);
  } catch {
    // A failed clear is harmless: the next load re-validates and a stale-but-valid
    // transcript only offers a resume the user can decline (never a hard error).
  }
}
