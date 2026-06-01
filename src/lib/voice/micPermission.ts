/**
 * Microphone-permission helper for in-news Voice mode (phase-3b SP1) of the
 * audio-first karaoke reel (blip / "News20").
 *
 * **Why this exists (port-map §5, §6).** Before opening the Gemini Live WSS, Voice
 * mode must hold a real mic grant. The prototype faked this with
 * `localStorage("n20-mic")`; production uses the live browser permission result.
 * This module wraps the **web-standard** browser APIs — `navigator.permissions`
 * (read current state without prompting) and `navigator.mediaDevices.getUserMedia`
 * (trigger the prompt inside a user gesture) — so it is portable: the same call
 * drives the permission prompt inside the future Capacitor iOS WebView once
 * phase-1c generates the native platform (no `@capacitor/*` here — that platform
 * does not exist yet).
 *
 * **Single responsibility.** This module only *checks / requests* the grant. It
 * never holds the mic open — {@link requestMicPermission} stops the obtained
 * `MediaStream`'s tracks immediately after confirming the grant, because the SP3
 * `useGeminiLive` hook opens (and owns) its own capture stream later.
 *
 * **Never throws.** SSR / unsupported-browser / blocked cases resolve to a typed
 * result, never an uncaught throw — the gate UI must always have a state to render.
 *
 * @example
 * const result = await requestMicPermission();
 * if (result.mic_permission_state === "granted") openVoiceMode();
 *
 * @example
 * // Read the current state WITHOUT prompting (e.g. to skip the CTA if already granted):
 * const state = await getMicPermissionState(); // "granted" | "denied" | "prompt" | "unsupported"
 */

import { logger } from "@/lib/logger";

/**
 * The mic-permission states this app reasons about.
 *
 * - `"granted"` — the user has allowed mic access; Voice mode may open the WSS.
 * - `"denied"` — the user blocked mic access; show the calm text-fallback CTA.
 * - `"prompt"` — not yet decided; the gate must request inside a user gesture.
 * - `"unsupported"` — no mic API available (SSR, insecure context, old WebView);
 *   treated as a hard "cannot capture", so the gate shows the same text fallback
 *   as `"denied"` (Voice mode is simply unavailable here).
 */
export type MicPermissionState = "granted" | "denied" | "prompt" | "unsupported";

/** The result of a permission check/request — a single typed field, room to grow. */
export interface MicPermissionResult {
  /** The resolved permission state. */
  mic_permission_state: MicPermissionState;
}

/**
 * Whether the live `getUserMedia` capture API is available in this runtime.
 *
 * False under SSR (no `navigator`), in insecure contexts where the browser hides
 * `mediaDevices`, and in WebViews too old to expose it. The gate uses this to
 * fall back to the text path instead of throwing.
 *
 * @returns `true` when `navigator.mediaDevices.getUserMedia` can be called.
 */
export function isMicCaptureSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices !== "undefined" &&
    typeof navigator.mediaDevices.getUserMedia === "function"
  );
}

/**
 * Read the current mic-permission state WITHOUT prompting the user.
 *
 * Uses the Permissions API (`navigator.permissions.query({ name: "microphone" })`)
 * which reports the current grant without opening a prompt — letting the gate skip
 * its CTA when already `"granted"`, or show the text fallback immediately when
 * already `"denied"`. The Permissions API is not universally implemented (notably
 * absent in some Safari/WebView versions); when it is missing but capture itself is
 * supported, we report `"prompt"` so the gate offers its enable-mic CTA (the only
 * way to learn the real state there is to call `getUserMedia` inside a gesture).
 *
 * Never throws — any failure resolves to a typed state.
 *
 * @returns The current {@link MicPermissionState}.
 *
 * @example
 * const state = await getMicPermissionState();
 * if (state === "granted") skipCta();
 */
export async function getMicPermissionState(): Promise<MicPermissionState> {
  if (!isMicCaptureSupported()) {
    // Reason: no capture API at all → Voice mode cannot run here; the gate shows
    // the same text fallback as a denial.
    return "unsupported";
  }

  // The Permissions API is optional; when absent we can only learn the state by
  // prompting, so report "prompt" and let the gate's CTA drive the request.
  if (typeof navigator === "undefined" || typeof navigator.permissions?.query !== "function") {
    return "prompt";
  }

  try {
    // Reason: "microphone" is a valid PermissionName at runtime but missing from
    // the lib.dom union in this TS version, so the cast is required (not an `any`).
    const status = await navigator.permissions.query({ name: "microphone" as PermissionName });
    if (status.state === "granted" || status.state === "denied" || status.state === "prompt") {
      return status.state;
    }
    return "prompt";
  } catch (error: unknown) {
    // A query failure (some engines reject "microphone") is non-fatal — fall back
    // to prompting via the CTA rather than blocking Voice mode.
    logger.warn("mic_permission_query_failed", {
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion:
        "navigator.permissions.query rejected for 'microphone'; the gate will fall back to a getUserMedia prompt.",
    });
    return "prompt";
  }
}

/**
 * Request mic permission by triggering the browser prompt, then release the mic.
 *
 * MUST be called from inside a user gesture (a tap on the gate's CTA) — browsers
 * and the iOS WebView reject `getUserMedia` outside a gesture. On a grant we stop
 * every track of the obtained {@link MediaStream} immediately: this helper only
 * confirms the grant; the SP3 `useGeminiLive` hook opens its own capture stream
 * for the conversation (holding the mic open here would double-acquire it).
 *
 * Never throws — a denial (`NotAllowedError`), a missing API, or any other failure
 * resolves to a typed state so the gate always has something to render.
 *
 * @param getUserMediaImpl - Injectable capture fn (defaults to the platform
 *   `navigator.mediaDevices.getUserMedia` bound to `mediaDevices`; tests pass a
 *   mock to avoid touching a real mic).
 * @returns The resolved {@link MicPermissionResult}.
 *
 * @example
 * // inside an onClick handler (a user gesture):
 * const { mic_permission_state } = await requestMicPermission();
 */
export async function requestMicPermission(
  getUserMediaImpl?: (constraints: MediaStreamConstraints) => Promise<MediaStream>,
): Promise<MicPermissionResult> {
  if (!isMicCaptureSupported()) {
    logger.warn("mic_permission_unsupported", {
      fix_suggestion:
        "navigator.mediaDevices.getUserMedia is unavailable (SSR/insecure context/old WebView); Voice mode falls back to text Q&A.",
    });
    return { mic_permission_state: "unsupported" };
  }

  // Reason: bind to mediaDevices so the default impl keeps its `this` context;
  // tests inject a mock to avoid acquiring a real microphone (CLAUDE.md mocking).
  const getUserMedia =
    getUserMediaImpl ?? ((constraints: MediaStreamConstraints) => navigator.mediaDevices.getUserMedia(constraints));

  logger.info("mic_permission_request_started", {});
  try {
    const stream = await getUserMedia({ audio: true });
    // Release the mic right away — we only needed the grant, not an open capture.
    for (const track of stream.getTracks()) {
      track.stop();
    }
    logger.info("mic_permission_granted", {});
    return { mic_permission_state: "granted" };
  } catch (error: unknown) {
    // getUserMedia rejects with NotAllowedError on denial; any rejection here means
    // we did not obtain the mic → treat as denied so the gate shows the text path.
    logger.warn("mic_permission_denied", {
      error_name: error instanceof Error ? error.name : "Unknown",
      error_message: error instanceof Error ? error.message : "Unknown error",
      fix_suggestion:
        "User dismissed or blocked the mic prompt (NotAllowedError); show the read-and-ask-by-text fallback to Detail Q&A.",
    });
    return { mic_permission_state: "denied" };
  }
}
