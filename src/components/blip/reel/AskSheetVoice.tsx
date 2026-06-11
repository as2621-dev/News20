"use client";

/**
 * AskSheetVoice — the VOICE body of the ask sheet (Sub-phase 4c).
 *
 * Renders the `.sheet-body` (and `.vs-foot` end button) only — the shared
 * `sheet-grab` + `ask-head` header is owned by {@link AskSheet}. Implements a
 * 3-state machine mirroring the prototype's `voicePermission()` →
 * `voiceListening()` → `voiceResponding()` flow, wired to the REAL
 * {@link useGeminiLive} hook grounded on the active story via
 * {@link storyQaTool}.
 *
 * **State machine:**
 * 1. `permission` — mic-ring CTA; if `localStorage("blip-voice-granted")` is
 *    already set (or `getMicPermissionState()` returns `"granted"`), skips
 *    straight to `listening` on mount.
 * 2. `listening` — live orb in LISTENING state + spoken-turn transcript thread +
 *    END button. Maps to `status === "connecting" | "live"` (before model audio).
 * 3. `responding` — orb in RESPONDING state + answer bubble + "Read the full
 *    story" link. Maps to model producing audio/transcript (tracked via
 *    `lastModelTextRef` delta).
 * 4. `error` — inline friendly error + END button. Shown when `connect()` throws
 *    or `status === "error"`.
 *
 * **LISTENING vs RESPONDING distinction:**
 * - `responding` flips on when a `"model"` transcript arrives AND the session is
 *   `live`; it resets to `listening` when a new `"user"` turn begins (the user
 *   is speaking again). This mirrors how the model's audio/transcript delta is
 *   the signal rather than a separate event.
 *
 * **Graceful failure:** If `connect()` throws or the hook reaches `"error"`,
 * renders a calm inline error message + END button. Never crashes; logs with
 * `fix_suggestion` via {@link logger}.
 *
 * @example
 * <AskSheetVoice story={activeStory} onClose={handleClose} onOpenArticle={handleArticle} />
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ic } from "@/components/blip/reel/icons";
import { logger } from "@/lib/logger";
import { getMicPermissionState, requestMicPermission } from "@/lib/voice/micPermission";
import {
  askAboutStoryDeclaration,
  buildAskAboutStoryHandler,
  STORY_QA_TOOL_GROUNDING_CLAUSE,
} from "@/lib/voice/storyQaTool";
import { buildGreetingNudge, buildInNewsSystemInstruction } from "@/lib/voice/storyVoicePrompts";
import { GEMINI_LIVE_DEFAULT_VOICE, useGeminiLive } from "@/lib/voice/useGeminiLive";
import type { Story } from "@/types/feed";

/** `localStorage` key the prototype uses to remember mic grant. */
const VOICE_GRANTED_KEY = "blip-voice-granted";

/**
 * The 4 render states of this component. `permission` = before grant;
 * `listening` = socket live, awaiting user or model audio; `responding` = model
 * is producing its answer; `error` = connect failed or hook in error.
 */
type VoiceViewState = "permission" | "listening" | "responding" | "error";

/** One turn in the spoken thread — user question or model answer. */
interface VoiceTurn {
  /** Whether this turn was produced by the user or the model. */
  role: "user" | "model";
  /** The transcribed text for this turn (may grow as it streams). */
  text: string;
}

export interface AskSheetVoiceProps {
  /** The active story to ground the voice session in. */
  story: Story;
  /** Close the sheet (the "END VOICE · BACK TO REEL" action). */
  onClose: () => void;
  /** Hand off to the full-article layer ("read the full story"). */
  onOpenArticle: () => void;
}

/**
 * Read the `blip-voice-granted` localStorage flag, guarded for SSR.
 *
 * @returns `true` if the user has previously granted mic via the voice sheet.
 */
function readVoiceGrantedFlag(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return localStorage.getItem(VOICE_GRANTED_KEY) === "1";
}

/**
 * Persist the `blip-voice-granted` localStorage flag, guarded for SSR.
 */
function writeVoiceGrantedFlag(): void {
  if (typeof window === "undefined") {
    return;
  }
  localStorage.setItem(VOICE_GRANTED_KEY, "1");
}

/**
 * The static vq-wave bars that indicate voiced speech (prototype `vqWave()`).
 * Heights match the prototype: [6, 11, 14, 9, 5].
 */
function VqWave() {
  return (
    <span className="vq-wave">
      {[6, 11, 14, 9, 5].map((height, index) => (
        // Reason: static decoration bars — index as key is safe (fixed array).
        // biome-ignore lint/suspicious/noArrayIndexKey: static bar list, never reordered
        <i key={index} style={{ height: `${height}px` }} />
      ))}
    </span>
  );
}

/**
 * The live orb element (prototype `orbEl(responding)`).
 *
 * @param is_responding - Whether to apply the `.responding` modifier class.
 */
function OrbEl({ is_responding }: { is_responding: boolean }) {
  return (
    <div className={`orb story${is_responding ? " responding" : ""}`} style={{ width: 100, height: 100 }}>
      <i className="c1" />
      <i className="c2" />
      <i className="d1" />
      <i className="d2" />
      <i className="core" />
    </div>
  );
}

/**
 * The vs-orb wrapper: orb + state label (prototype `vsOrb(state)`).
 *
 * @param view_state - `"listening"` or `"responding"` to drive classes + label.
 */
function VsOrb({ view_state }: { view_state: "listening" | "responding" }) {
  const is_responding = view_state === "responding";
  const state_label = is_responding ? "RESPONDING" : "LISTENING";
  return (
    <div className="vs-orb" id="vsOrbWrap">
      <OrbEl is_responding={is_responding} />
      <div className={`vs-state ${is_responding ? "resp" : "live"}`}>{state_label}</div>
    </div>
  );
}

/**
 * The END VOICE footer button (prototype `vsFoot()`).
 *
 * @param on_end - Called when the user taps the end button.
 */
function VsFoot({ on_end }: { on_end: () => void }) {
  return (
    <div className="vs-foot">
      <button type="button" className="vs-end" onClick={on_end}>
        <span className="dotlive" />
        END VOICE · BACK TO REEL
      </button>
    </div>
  );
}

/**
 * Render the voice-ask body.
 *
 * Owns the mic-permission gate (checking `localStorage("blip-voice-granted")`
 * and the real browser permission API), the Gemini Live socket lifecycle, the
 * orb state machine, and the spoken-turn transcript thread. Disconnects the
 * socket on unmount (sheet close) and on the END button.
 */
export function AskSheetVoice({ story, onClose, onOpenArticle }: AskSheetVoiceProps) {
  // Initialise view state: skip permission screen if already granted.
  const [viewState, setViewState] = useState<VoiceViewState>(() => {
    // Reason: check localStorage flag synchronously on init so already-granted
    // users skip the CTA without a flash. Browser permission state is checked
    // asynchronously in the mount effect and may override this to `listening`.
    return readVoiceGrantedFlag() ? "listening" : "permission";
  });

  // Accumulate the spoken turns so we can render the full conversation thread.
  const [turns, setTurns] = useState<VoiceTurn[]>([]);
  // Tracks whether a request is in flight (prevents double-click on CTA).
  const [isRequestingMic, setIsRequestingMic] = useState<boolean>(false);
  // Inline error message when connect fails.
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  /**
   * Transcript callback: append/update turns as they stream.
   *
   * A new `user` turn resets to LISTENING (user is speaking again).
   * A new `model` turn flips to RESPONDING.
   */
  const handleTranscript = useCallback((transcript: { role: "user" | "model"; text: string }): void => {
    setTurns((prev) => {
      const last = prev[prev.length - 1];
      // Reason: Gemini Live streams transcript deltas for the CURRENT turn — we
      // replace the last entry if it's the same role (streaming update), or
      // append a new entry when the role switches (new turn boundary).
      if (last && last.role === transcript.role) {
        return [...prev.slice(0, -1), { role: transcript.role, text: transcript.text }];
      }
      return [...prev, { role: transcript.role, text: transcript.text }];
    });

    // Drive orb state from transcript role transitions.
    if (transcript.role === "model") {
      setViewState((prev) => (prev === "listening" || prev === "responding" ? "responding" : prev));
    } else {
      setViewState((prev) => (prev === "responding" ? "listening" : prev));
    }
  }, []);

  // Reason: useMemo (not useCallback) because buildAskAboutStoryHandler returns a
  // function, not takes one — memoize the returned async handler by story id.
  const onToolCall = useMemo(() => buildAskAboutStoryHandler(story.digest_id), [story.digest_id]);

  // Mic failure after connect (gotcha 8 surfacing): specific copy + error view —
  // the hook has already disconnected, so the orb never fakes LISTENING.
  const handleMicError = useCallback((): void => {
    setErrorMessage("Couldn’t access your microphone. Check mic permission in Settings, or type your question.");
    setViewState("error");
  }, []);

  const { status, connect, disconnect } = useGeminiLive({
    systemInstruction: buildInNewsSystemInstruction(story.headline, story.digest_id, STORY_QA_TOOL_GROUNDING_CLAUSE),
    tools: [askAboutStoryDeclaration],
    onToolCall,
    onTranscript: handleTranscript,
    voiceName: GEMINI_LIVE_DEFAULT_VOICE,
    greetingNudge: buildGreetingNudge(story.headline),
    onMicError: handleMicError,
  });

  // Reason: useGeminiLive rebuilds connect/disconnect on every render because the
  // instruction string is recomputed. Store stable refs so the mount effect
  // doesn't thrash the socket on re-renders (mirrors VoiceConversation.tsx pattern).
  const connectRef = useRef(connect);
  connectRef.current = connect;
  const disconnectRef = useRef(disconnect);
  disconnectRef.current = disconnect;

  /**
   * Open the socket. Called from INSIDE user gesture (enable-mic button) and
   * from the mount effect when already granted. Guards double-connect via
   * `connectingRef` (the hook itself is idempotent per StrictMode guard, but we
   * also track our own attempt flag to avoid double-call in StrictMode).
   */
  const connectingRef = useRef<boolean>(false);

  const startVoiceSession = useCallback(async (): Promise<void> => {
    if (connectingRef.current) {
      return;
    }
    connectingRef.current = true;
    logger.info("ask_sheet_voice_connecting", {
      story_id: story.digest_id,
    });
    try {
      await connectRef.current();
      setViewState("listening");
      logger.info("ask_sheet_voice_connected", { story_id: story.digest_id });
    } catch (connect_error: unknown) {
      const error_message = connect_error instanceof Error ? connect_error.message : "Unknown error";
      logger.error("ask_sheet_voice_connect_failed", {
        story_id: story.digest_id,
        error_message,
        fix_suggestion:
          "Check that /api/voice/live-token is deployed and GEMINI_API_KEY is set on the worker. " +
          "This is expected in environments without the token endpoint.",
      });
      setErrorMessage("Voice isn't available right now.");
      setViewState("error");
    } finally {
      connectingRef.current = false;
    }
  }, [story.digest_id]);

  // Mirror hook error status → error view. The generic copy must NOT clobber a
  // more specific message (e.g. onMicError's) that landed first.
  useEffect(() => {
    if (status === "error") {
      logger.error("ask_sheet_voice_hook_error", {
        story_id: story.digest_id,
        fix_suggestion: "Inspect useGeminiLive status; the token endpoint or WSS handshake may have failed.",
      });
      setErrorMessage((previousMessage) => previousMessage ?? "Voice isn't available right now.");
      setViewState("error");
    }
  }, [status, story.digest_id]);

  // On mount: if the view is already `listening` (localStorage flag was set),
  // verify the real browser permission and auto-connect.
  // Reason: startVoiceSession is stable (useCallback keyed on story.digest_id);
  // including it satisfies the exhaustive-deps rule without causing extra runs
  // because its identity only changes when the story changes (keyed mount).
  useEffect(() => {
    if (viewState !== "listening") {
      return;
    }
    let isCurrent = true;
    getMicPermissionState().then((mic_state) => {
      if (!isCurrent) {
        return;
      }
      if (mic_state === "granted") {
        // Already granted — open the socket automatically (no gesture needed
        // because the user's prior grant covers this).
        void startVoiceSession();
      } else {
        // Flag is stale (permission was revoked). Reset to permission screen.
        localStorage.removeItem(VOICE_GRANTED_KEY);
        setViewState("permission");
      }
    });
    return () => {
      isCurrent = false;
    };
  }, [startVoiceSession, viewState]);

  // Disconnect on unmount (sheet close, navigation, or StrictMode cleanup).
  useEffect(() => {
    return () => {
      logger.info("ask_sheet_voice_disconnecting", { story_id: story.digest_id });
      disconnectRef.current();
    };
  }, [story.digest_id]);

  /**
   * Handle the "Enable microphone" button tap (INSIDE the user gesture so
   * `getUserMedia` is permitted by the browser).
   */
  const handleEnableMic = useCallback(async (): Promise<void> => {
    if (isRequestingMic) {
      return;
    }
    setIsRequestingMic(true);
    logger.info("ask_sheet_voice_mic_requested", { story_id: story.digest_id });

    try {
      const { mic_permission_state } = await requestMicPermission();
      if (mic_permission_state === "granted") {
        writeVoiceGrantedFlag();
        setViewState("listening");
        await startVoiceSession();
      } else {
        logger.warn("ask_sheet_voice_mic_denied", {
          story_id: story.digest_id,
          mic_permission_state,
          fix_suggestion: "User denied mic; show the NOT NOW fallback. No socket opened.",
        });
        setErrorMessage("Mic access was denied. Voice isn't available right now.");
        setViewState("error");
      }
    } catch (mic_error: unknown) {
      logger.error("ask_sheet_voice_mic_request_failed", {
        story_id: story.digest_id,
        error_message: mic_error instanceof Error ? mic_error.message : "Unknown error",
        fix_suggestion: "requestMicPermission threw unexpectedly; check the micPermission module.",
      });
      setErrorMessage("Couldn't access your microphone. Try again or type your question.");
      setViewState("error");
    } finally {
      setIsRequestingMic(false);
    }
  }, [isRequestingMic, story.digest_id, startVoiceSession]);

  /** END button: disconnect then close the sheet. */
  const handleEnd = useCallback((): void => {
    logger.info("ask_sheet_voice_ended", { story_id: story.digest_id });
    disconnectRef.current();
    onClose();
  }, [story.digest_id, onClose]);

  // ── STATE: permission ──────────────────────────────────────────────────────
  if (viewState === "permission") {
    return (
      <>
        <div className="sheet-body" style={{ alignItems: "center", justifyContent: "center", textAlign: "center" }}>
          <div className="v-mic-ring">{ic("voice")}</div>
          <h2 className="v-h2" style={{ marginTop: 18 }}>
            Ask out loud, hands-free.
          </h2>
          <p className="v-sub">
            Answers stay grounded in this story&apos;s source. blip only listens while this sheet is open.
          </p>
        </div>
        <div className="vs-foot" style={{ paddingTop: 0 }}>
          <button
            type="button"
            className="v-btn solid"
            id="vEnable"
            onClick={handleEnableMic}
            disabled={isRequestingMic}
          >
            {isRequestingMic ? "Enabling…" : "Enable microphone"}
          </button>
          <button type="button" className="v-end-link" onClick={onClose}>
            NOT NOW
          </button>
        </div>
      </>
    );
  }

  // ── STATE: error ───────────────────────────────────────────────────────────
  if (viewState === "error") {
    return (
      <>
        <div className="sheet-body" style={{ alignItems: "center", justifyContent: "center", textAlign: "center" }}>
          <p
            style={{
              color: "rgba(255,255,255,.72)",
              fontSize: "14.5px",
              lineHeight: 1.5,
              maxWidth: "300px",
            }}
          >
            {errorMessage ?? "Voice isn't available right now."}
          </p>
        </div>
        <VsFoot on_end={handleEnd} />
      </>
    );
  }

  // ── STATE: listening / responding ──────────────────────────────────────────
  // Split the turns into the user's LAST question and the model's last answer
  // so we can render the prototype's `.row-q` / `.row-a` structure.
  const last_user_turn = [...turns].reverse().find((t) => t.role === "user") ?? null;
  const last_model_turn = [...turns].reverse().find((t) => t.role === "model") ?? null;
  const has_model_answer = last_model_turn !== null;

  return (
    <>
      <div className="sheet-body">
        <VsOrb view_state={viewState === "responding" ? "responding" : "listening"} />
        <div className="vthread" id="vthread">
          {last_user_turn !== null && (
            <div className="row-q">
              <div className="bub-q voiced">
                <VqWave />
                <span>{last_user_turn.text}</span>
              </div>
            </div>
          )}
          {has_model_answer && (
            <>
              <div className="row-a">
                <div className="bub-a">
                  <p>{last_model_turn.text}</p>
                </div>
              </div>
              <div className="row-a">
                <button type="button" className="read-full" onClick={onOpenArticle}>
                  {ic("doc")}
                  Read the full story
                </button>
              </div>
            </>
          )}
        </div>
      </div>
      <VsFoot on_end={handleEnd} />
    </>
  );
}
