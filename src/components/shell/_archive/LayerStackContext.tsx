"use client";

/**
 * LayerStackContext — the React context that lets the reel (deep in the tree)
 * open the lateral Detail layer and lets a future Detail panel close itself,
 * without prop-drilling through `Reel` / `ReelStory` / `ReelChrome`.
 *
 * **Why a context (port-map §1).** The prototype stacks `.layer-reel` (base) and
 * `.layer-detail` (pushes in from the right) as siblings under one `#app` node;
 * opening Detail is a single shared piece of state. {@link LayerStack} owns that
 * state and publishes this context so any descendant can call
 * {@link LayerStackContextValue.openDetail} (the reel, on a swipe-right
 * affordance) or {@link LayerStackContextValue.closeDetail} (the Detail panel's
 * back-swipe / close button — wired in SP2).
 *
 * This file only declares the context + a typed `useLayerStack()` hook. The
 * provider + the actual open/close state machine live in {@link LayerStack} so
 * the state and its consumers stay colocated.
 *
 * **Voice members (phase-3b SP2).** The left lateral Voice layer mirrors Detail
 * exactly — `openVoice(story)` / `closeVoice()` plus `isVoiceOpen` /
 * `openVoiceStory` — so the same shell drives both lateral directions (Detail
 * right, Voice left) off one shared context (port-map §1, §3.2).
 */

import { createContext, useContext } from "react";
import type { Story } from "@/types/feed";

/**
 * The shape published to descendants of {@link LayerStack}.
 *
 * `openDetail` receives the full {@link Story} (not just its id) so SP2's Detail
 * panel can render immediately from the in-memory story while it fetches the
 * heavier `detail_chunks` / `story_trust` / `story_timeline` rows keyed on
 * `story.digest_id`.
 */
export interface LayerStackContextValue {
  /** Whether the lateral Detail layer is currently open. */
  isDetailOpen: boolean;
  /** The story the Detail layer is showing (null while the layer is closed). */
  openDetailStory: Story | null;
  /**
   * The reel's currently-active (snapped) story, surfaced upward so the shell —
   * and SP2's swipe-right trigger — can open Detail for whatever the user is
   * looking at WITHOUT prop-drilling through the reel. The reel keeps this in
   * sync as the active index changes; it is `null` before the feed resolves.
   */
  activeStory: Story | null;
  /**
   * Set the reel's active story (called by the reel as its active index changes).
   * Not for general descendants — it is the reel's one upward seam.
   */
  setActiveStory: (story: Story | null) => void;
  /** Open the Detail layer for `story` (the reel calls this with its active story). */
  openDetail: (story: Story) => void;
  /** Close the Detail layer (the Detail panel calls this on back-swipe / close). */
  closeDetail: () => void;
  /** Whether the lateral Voice layer is currently open (phase-3b SP2). */
  isVoiceOpen: boolean;
  /**
   * The story the Voice layer is conversing about (null while the layer is
   * closed). Mirrors {@link openDetailStory}: SP2's `VoiceMode` reads the full
   * {@link Story} so it can scope the Gemini Live session to that `digest_id`.
   */
  openVoiceStory: Story | null;
  /**
   * Open the Voice layer for `story` (a left-drag over the reel calls this with
   * its active story — prototype `attachGestures`: `dx < 0 → openVoice`).
   */
  openVoice: (story: Story) => void;
  /** Close the Voice layer (the Voice panel calls this on back-swipe / close). */
  closeVoice: () => void;
}

/**
 * The Detail-layer context. `undefined` outside a {@link LayerStack} provider so
 * {@link useLayerStack} can fail loud (Rule 12) instead of silently handing back
 * a no-op.
 */
export const LayerStackContext = createContext<LayerStackContextValue | undefined>(undefined);

/**
 * Read the {@link LayerStackContextValue} from the nearest {@link LayerStack}.
 *
 * Throws if called outside a `LayerStack` provider — a missing provider is a
 * wiring bug, not a runtime condition to paper over.
 *
 * @returns The lateral-layer open/close controls.
 *
 * @example
 * const { openDetail } = useLayerStack();
 * // on a swipe-right affordance over the active story:
 * openDetail(currentStory);
 */
export function useLayerStack(): LayerStackContextValue {
  const layerStackContextValue = useContext(LayerStackContext);
  if (layerStackContextValue === undefined) {
    throw new Error(
      "useLayerStack must be used inside a <LayerStack>. Wrap the reel (and any lateral-layer consumer) in <LayerStack> first.",
    );
  }
  return layerStackContextValue;
}
