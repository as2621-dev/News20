import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Phase 7e-1 — self-healing reel playback (retry play() on ready).
 *
 * Rule 9 — these encode WHY the behaviour matters, not just what is called:
 *   - The ACTIVE reel must become audible ON ITS OWN. The user-reported bug is a
 *     fast-scrolled reel that stays SILENT until you bounce away and back — that
 *     bounce is a manual retry. So when the first play() loses the media-load race
 *     (rejects not-ready), playback MUST self-heal by retrying once the element can
 *     play (a/b). Fails on pre-fix code, which called play() exactly once.
 *   - Playback must NEVER start on a reel the user has already LEFT — a retry that
 *     fires after scroll-away would play a second narration over the new active
 *     reel (c). The inactive effect must cancel the armed retry.
 *   - The iOS pre-unlock autoplay block (NotAllowedError) is owned by the tap /
 *     unlock path; retrying it would fight the autoplay policy, so it arms NO retry
 *     (d).
 *
 * jsdom cannot drive a real HTMLMediaElement.play()/readyState, so a hand-rolled
 * fake element is bound to the hook's `audioRef.current` (per the RCA test sketch).
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act` and a
 * tiny harness that captures the controller — the project does not depend on
 * `@testing-library/react`, so no `renderHook` (mirrors the other reel tests).
 */

// Tell React this is an act() environment so effects/state flush synchronously.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { type ReelAudioController, useReelAudio } from "@/lib/reel/useReelAudio";

/** A captured event listener registered on the fake element. */
type Listener = () => void;

/** The hand-rolled fake `<audio>` element + its test driver helpers. */
interface FakeAudio {
  /** The object bound to `audioRef.current` (a partial HTMLAudioElement). */
  el: HTMLAudioElement;
  /** Synchronously invoke every listener registered for `eventName`. */
  fire: (eventName: string) => void;
  /** How many listeners are currently registered for `eventName`. */
  listenerCount: (eventName: string) => number;
  /** The play() mock — `mock.calls.length` is the attempt count. */
  play: ReturnType<typeof vi.fn>;
  /** The removeEventListener mock — asserts clean teardown after a retry fires. */
  removeEventListener: ReturnType<typeof vi.fn>;
  /** Set what the NEXT play() call returns (resolve vs a specific rejection). */
  setNextPlayResult: (result: Promise<void>) => void;
}

/**
 * Build a fake audio element whose play() rejection is configurable per call and
 * whose canplay/loadeddata listeners can be fired on demand. `readyState` defaults
 * to 0 (HAVE_NOTHING) so the catch block classifies the failure as not-ready.
 */
function makeFakeAudio(initialPlayResult: Promise<void>): FakeAudio {
  const listeners: Record<string, Listener[]> = {};
  let nextPlayResult = initialPlayResult;

  const play = vi.fn(() => {
    const result = nextPlayResult;
    // Reason: once a play() result is consumed, default the next call to resolve,
    // so an un-reconfigured retry succeeds (the happy "now ready" path).
    nextPlayResult = Promise.resolve();
    return result;
  });
  const removeEventListener = vi.fn((eventName: string, callback: Listener) => {
    listeners[eventName] = (listeners[eventName] ?? []).filter((registered) => registered !== callback);
  });
  const addEventListener = vi.fn((eventName: string, callback: Listener) => {
    const registered = listeners[eventName] ?? [];
    registered.push(callback);
    listeners[eventName] = registered;
  });

  // Reason: only the members the hook touches are implemented; cast through unknown
  // because this is a deliberate partial stand-in for HTMLAudioElement under jsdom.
  const el = {
    readyState: 0,
    paused: true,
    currentTime: 0,
    load: vi.fn(),
    pause: vi.fn(),
    play,
    addEventListener,
    removeEventListener,
  } as unknown as HTMLAudioElement;

  return {
    el,
    fire: (eventName: string) => {
      for (const callback of listeners[eventName] ?? []) {
        callback();
      }
    },
    listenerCount: (eventName: string) => (listeners[eventName] ?? []).length,
    play,
    removeEventListener,
    setNextPlayResult: (result: Promise<void>) => {
      nextPlayResult = result;
    },
  };
}

let container: HTMLDivElement;
let root: Root;
/** The latest controller rendered by the harness (re-captured each render). */
let capturedController: ReelAudioController | null;

/** Minimal harness: renders the hook and stashes its controller for the test. */
function ReelAudioHarness({ isActive }: { isActive: boolean }): null {
  capturedController = useReelAudio({
    storyIndex: 8,
    storyCount: 15,
    isActive,
    onEnded: vi.fn(),
  });
  return null;
}

/** Render (or re-render) the harness with the given active flag. */
function renderHarness(isActive: boolean): void {
  act(() => {
    root.render(<ReelAudioHarness isActive={isActive} />);
  });
}

/** Bind the fake element to the captured controller's `audioRef`. */
function bindFakeAudio(fake: FakeAudio): void {
  // Reason: the hook owns a real useRef; the test drives play()/events through a
  // fake element by assigning it to `.current`, exactly as the RCA sketch does.
  (capturedController as unknown as { audioRef: { current: HTMLAudioElement | null } }).audioRef.current = fake.el;
}

beforeEach(() => {
  capturedController = null;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

describe("useReelAudio self-healing playback (Phase 7e-1)", () => {
  it("test_active_reel_retries_play_once_when_media_becomes_ready", async () => {
    // WHY: a fast-scrolled reel's first play() rejects because the media is not yet
    // buffered. The reel must NOT stay silent — once it can play, playback retries
    // itself, so audio starts WITHOUT the user bouncing away and back.
    renderHarness(true);
    const fake = makeFakeAudio(Promise.reject(new DOMException("not ready", "NotSupportedError")));
    bindFakeAudio(fake);

    await act(async () => {
      await capturedController?.playAudio();
    });
    // First attempt happened and rejected not-ready → exactly one play() so far.
    expect(fake.play).toHaveBeenCalledTimes(1);

    // The element becomes playable → the armed one-shot retry must fire play() again.
    await act(async () => {
      fake.fire("canplay");
    });
    expect(fake.play).toHaveBeenCalledTimes(2);
    // The retry must remove its own listeners (clean teardown, no orphan handlers).
    expect(fake.listenerCount("canplay")).toBe(0);
    expect(fake.listenerCount("loadeddata")).toBe(0);
    expect(fake.removeEventListener).toHaveBeenCalledWith("canplay", expect.any(Function));
    expect(fake.removeEventListener).toHaveBeenCalledWith("loadeddata", expect.any(Function));
  });

  it("test_repeated_not_ready_rejections_do_not_stack_multiple_retries", async () => {
    // WHY: more than one armed listener would fire play() multiple times on one
    // canplay — risking overlapping playback. Repeated not-ready rejections must
    // arm AT MOST one retry, so a single canplay produces exactly one extra play().
    renderHarness(true);
    const fake = makeFakeAudio(Promise.reject(new DOMException("not ready", "NotSupportedError")));
    bindFakeAudio(fake);

    // First rejection arms the retry.
    await act(async () => {
      await capturedController?.playAudio();
    });
    // Second attempt also rejects not-ready, but a retry is already armed → no-op.
    fake.setNextPlayResult(Promise.reject(new DOMException("still not ready", "NotSupportedError")));
    await act(async () => {
      await capturedController?.playAudio();
    });

    expect(fake.play).toHaveBeenCalledTimes(2);
    // Exactly one listener per event despite two rejections (no stacking).
    expect(fake.listenerCount("canplay")).toBe(1);
    expect(fake.listenerCount("loadeddata")).toBe(1);

    // One canplay → one retry only (3rd play total), then listeners are gone.
    await act(async () => {
      fake.fire("canplay");
    });
    expect(fake.play).toHaveBeenCalledTimes(3);
    expect(fake.listenerCount("canplay")).toBe(0);
  });

  it("test_going_inactive_before_canplay_cancels_the_retry", async () => {
    // WHY: if the user scrolls away while the reel is still buffering, a canplay
    // firing afterwards must NOT start audio — that would play a second narration
    // over the new active reel. Going inactive must cancel the armed retry.
    renderHarness(true);
    const fake = makeFakeAudio(Promise.reject(new DOMException("not ready", "NotSupportedError")));
    bindFakeAudio(fake);

    await act(async () => {
      await capturedController?.playAudio();
    });
    expect(fake.play).toHaveBeenCalledTimes(1);
    expect(fake.listenerCount("canplay")).toBe(1);

    // User scrolls away → the reel becomes inactive BEFORE the element can play.
    renderHarness(false);
    // The cancel must have removed the retry listeners.
    expect(fake.listenerCount("canplay")).toBe(0);
    expect(fake.listenerCount("loadeddata")).toBe(0);

    // A late canplay must NOT re-issue play() — still exactly one attempt.
    await act(async () => {
      fake.fire("canplay");
    });
    expect(fake.play).toHaveBeenCalledTimes(1);
  });

  it("test_not_allowed_error_arms_no_retry", async () => {
    // WHY: NotAllowedError is the iOS pre-unlock autoplay block, owned by the
    // tap/unlock path. Retrying it would fight the autoplay policy, so NO retry is
    // armed and a later canplay must not call play() again.
    renderHarness(true);
    const fake = makeFakeAudio(Promise.reject(new DOMException("autoplay blocked", "NotAllowedError")));
    bindFakeAudio(fake);

    await act(async () => {
      await capturedController?.playAudio();
    });
    expect(fake.play).toHaveBeenCalledTimes(1);
    // No listener was armed for the not-allowed path.
    expect(fake.listenerCount("canplay")).toBe(0);
    expect(fake.listenerCount("loadeddata")).toBe(0);

    await act(async () => {
      fake.fire("canplay");
    });
    expect(fake.play).toHaveBeenCalledTimes(1);
  });
});
