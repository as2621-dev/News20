import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * State-machine tests for {@link InterviewChat} (FSR slice #4), mirroring the
 * createRoot + act prior art in `onboardingFlowSessionSkip.test.tsx` (no
 * @testing-library — not a project dependency). The turn fetcher is injected via
 * the `fetchTurn` prop (no network, no worker); jsdom supplies `localStorage`.
 *
 * Rule 9 — WHY these behaviors matter (each fails on a real regression):
 *   - Tap-through must reach a confirm screen and persist ONLY on confirm (PRD #1/#8).
 *   - Skip + type-your-own affordances must render on every question, and free text
 *     must round-trip through the turn request (PRD #2, acceptance criteria).
 *   - A failed turn must show an in-place retry, never a dead-end (spec §2).
 *   - Back-navigation must invalidate the last answer and re-fetch the prior turn.
 *   - A double-tap must not fire two turns (would skip a question / double-persist).
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { InterviewChat } from "@/components/onboarding/InterviewChat";
import type { InterviewExchange, InterviewTurn } from "@/types/interview";

const Q0: InterviewTurn = {
  response_kind: "question",
  turn_index: 0,
  question_text: "What are you into?",
  bubbles: [
    { bubble_label: "Sport", bubble_kind: "option" },
    { bubble_label: "not really / skip", bubble_kind: "skip" },
    { bubble_label: "something else — type it", bubble_kind: "type_your_own" },
  ],
};

const Q1: InterviewTurn = {
  response_kind: "question",
  turn_index: 1,
  question_text: "Which sport?",
  bubbles: [
    { bubble_label: "Cricket", bubble_kind: "option" },
    { bubble_label: "not really / skip", bubble_kind: "skip" },
    { bubble_label: "something else — type it", bubble_kind: "type_your_own" },
  ],
};

const TERMINAL: InterviewTurn = {
  response_kind: "terminal",
  turn_index: 2,
  micro_interests: [
    {
      display_label: "IPL auctions",
      canonical_slug: "sport.cricket.ipl",
      ladder: ["sport", "sport.cricket"],
      search_anchor_terms: ["IPL auction", "player transfer"],
      strict: false,
    },
  ],
  roots_only_fallback: false,
  mute_terms: [],
  angle_preferences: [],
};

const TERMINAL_EMPTY: InterviewTurn = {
  response_kind: "terminal",
  turn_index: 1,
  micro_interests: [],
  roots_only_fallback: true,
  mute_terms: [],
  angle_preferences: [],
};

const RETRY: InterviewTurn = {
  response_kind: "retry",
  turn_index: 1,
  error_code: "transport_error",
  error_message: "We couldn't reach the interview just now.",
  retry_hint: "Tap retry to try again.",
};

let container: HTMLDivElement;
let root: Root;

/**
 * A minimal in-memory localStorage stub (mirrors tests/lib/onboardingProfile.test.ts):
 * the jsdom/node build's native localStorage lacks a usable `.clear()`.
 */
function installLocalStorageStub(): void {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
      clear: () => store.clear(),
    },
    configurable: true,
    writable: true,
  });
}

beforeEach(() => {
  installLocalStorageStub();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/**
 * Build a `fetchTurn` stub that returns the given turns in call order (the last
 * repeats), recording the conversation_state each call received.
 */
function scriptedFetch(turns: InterviewTurn[]): {
  fn: (state: InterviewExchange[]) => Promise<InterviewTurn>;
  calls: InterviewExchange[][];
} {
  const calls: InterviewExchange[][] = [];
  let index = 0;
  const fn = async (state: InterviewExchange[]): Promise<InterviewTurn> => {
    calls.push(state);
    const turn = turns[Math.min(index, turns.length - 1)];
    index += 1;
    return turn;
  };
  return { fn, calls };
}

/** Render the chat and flush the mount effect's async first turn. */
async function renderChat(
  fetchTurn: (state: InterviewExchange[]) => Promise<InterviewTurn>,
  onComplete = vi.fn(),
): Promise<{ onComplete: ReturnType<typeof vi.fn> }> {
  await act(async () => {
    root.render(<InterviewChat onComplete={onComplete} fetchTurn={fetchTurn as never} />);
  });
  return { onComplete };
}

/** Click the first button whose text contains `text`. Throws if not found. */
async function clickText(text: string): Promise<void> {
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((candidate) =>
    candidate.textContent?.includes(text),
  );
  if (!button) {
    throw new Error(`Button containing "${text}" not found. Buttons: ${bodyButtons()}`);
  }
  await act(async () => {
    button.click();
  });
}

/** Debug helper listing the currently rendered button labels. */
function bodyButtons(): string {
  return Array.from(container.querySelectorAll("button"))
    .map((button) => button.textContent)
    .join(" | ");
}

describe("InterviewChat — state machine (Rule 9)", () => {
  it("renders the first question with option, skip, and type-your-own bubbles", async () => {
    await renderChat(scriptedFetch([Q0]).fn);
    expect(container.textContent).toContain("What are you into?");
    expect(bodyButtons()).toContain("Sport");
    expect(bodyButtons()).toContain("not really / skip");
    expect(bodyButtons()).toContain("something else — type it");
  });

  it("taps through to a confirm screen and persists ONLY on confirm", async () => {
    const { fn, calls } = scriptedFetch([Q0, Q1, TERMINAL]);
    const { onComplete } = await renderChat(fn);

    await clickText("Sport");
    expect(container.textContent).toContain("Which sport?");
    // Persistence must NOT have fired yet (no terminal confirmed).
    expect(onComplete).not.toHaveBeenCalled();

    await clickText("Cricket");
    // Terminal → confirm screen shows the user-vocabulary label.
    expect(container.textContent).toContain("IPL auctions");
    expect(onComplete).not.toHaveBeenCalled();

    await clickText("Looks good");
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0]).toEqual({
      micro_interests: (TERMINAL as Extract<InterviewTurn, { response_kind: "terminal" }>).micro_interests,
      roots_only_fallback: false,
    });
    // The second turn request carried the tapped answer (the exchange).
    expect(calls[1][0].bubbles_tapped).toEqual(["Sport"]);
  });

  it("ignores a double-tap on confirm so persistence fires once", async () => {
    const { onComplete } = await renderChat(scriptedFetch([Q0, TERMINAL]).fn);
    await clickText("Sport"); // → TERMINAL → confirm screen

    // Two rapid taps on "Looks good" in one flush.
    const confirm = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((b) =>
      b.textContent?.includes("Looks good"),
    );
    await act(async () => {
      confirm?.click();
      confirm?.click();
    });

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("skip-everything reaches a roots-only confirm and completes (skipping not punished)", async () => {
    const { onComplete } = await renderChat(scriptedFetch([Q0, TERMINAL_EMPTY]).fn);

    await clickText("not really / skip");
    expect(container.textContent).toContain("big picture");

    await clickText("Continue");
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0]).toEqual({ micro_interests: [], roots_only_fallback: true });
  });

  it("round-trips free text through the turn request", async () => {
    const { fn, calls } = scriptedFetch([Q0, TERMINAL]);
    await renderChat(fn);

    await clickText("something else — type it");
    const input = container.querySelector<HTMLInputElement>("input");
    if (!input) {
      throw new Error("free-text input did not appear");
    }
    await act(async () => {
      // Set via the native value setter so React's controlled onChange actually fires.
      const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
      nativeSetter?.call(input, "Formula 1 strategy");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await clickText("Send");

    expect(calls[1][0].free_text_entered).toBe("Formula 1 strategy");
    expect(calls[1][0].bubbles_tapped).toEqual([]);
  });

  it("shows an in-place retry on a failed turn and re-fetches on Retry", async () => {
    // Mount question, then a failed turn, then a real question on retry.
    const { fn, calls } = scriptedFetch([Q0, RETRY, Q1]);
    await renderChat(fn);

    await clickText("Sport"); // → RETRY
    expect(container.textContent).toContain("couldn't reach the interview");
    expect(bodyButtons()).toContain("Retry");

    await clickText("Retry"); // → Q1
    expect(container.textContent).toContain("Which sport?");
    // The retry re-sent the SAME state the failed turn was fetching.
    expect(calls[2]).toEqual(calls[1]);
  });

  it("back-navigation drops the last answer and re-fetches the prior question", async () => {
    const { fn, calls } = scriptedFetch([Q0, Q1, Q0]);
    await renderChat(fn);

    await clickText("Sport"); // now on Q1, conversation has 1 exchange
    expect(container.textContent).toContain("Which sport?");

    await clickText("BACK");
    // Back re-fetched with the EMPTY prior state (the last answer invalidated).
    expect(calls[2]).toEqual([]);
  });

  it("ignores a double-tap so only one turn fires", async () => {
    // A fetch that stays pending until we release it, to race two taps.
    let release: (turn: InterviewTurn) => void = () => {};
    const calls: InterviewExchange[][] = [];
    let served = 0;
    const fn = (state: InterviewExchange[]): Promise<InterviewTurn> => {
      calls.push(state);
      served += 1;
      if (served === 1) {
        return Promise.resolve(Q0); // mount question resolves immediately
      }
      return new Promise<InterviewTurn>((resolve) => {
        release = resolve;
      });
    };
    await renderChat(fn as never);

    // Two rapid taps while the second turn is in flight.
    const sport = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((b) =>
      b.textContent?.includes("Sport"),
    );
    await act(async () => {
      sport?.click();
      sport?.click();
    });
    await act(async () => {
      release(Q1);
    });

    // mount (1) + exactly ONE answer turn (2) — the double-tap did not fire a third.
    expect(calls).toHaveLength(2);
  });

  it("offers resume when a transcript is cached, and resumes on Resume", async () => {
    window.localStorage.setItem(
      "n20-interview-session",
      JSON.stringify({
        version: 1,
        conversation_state: [
          {
            question_text: "What are you into?",
            bubbles_offered: ["Sport"],
            bubbles_tapped: ["Sport"],
            free_text_entered: null,
          },
        ],
        saved_at_ms: Date.now(),
      }),
    );
    const { fn, calls } = scriptedFetch([Q1]);
    await renderChat(fn);

    expect(container.textContent).toContain("Pick up where you left off?");
    await clickText("Resume");
    // Resume re-fetched the next question for the cached (non-empty) state.
    expect(calls[0]).toHaveLength(1);
    expect(container.textContent).toContain("Which sport?");
  });

  it("forceRestart skips the resume prompt, clears the cached transcript, and starts fresh (issue #9)", async () => {
    // WHY (issue #9): the rebuild-my-feed entry must never surface a stale onboarding
    // transcript's "Pick up where you left off?" — it clears the cache and fetches turn 0.
    // FAILS if the resume prompt renders or the first fetch carries the cached exchanges.
    window.localStorage.setItem(
      "n20-interview-session",
      JSON.stringify({
        version: 1,
        conversation_state: [
          {
            question_text: "What are you into?",
            bubbles_offered: ["Sport"],
            bubbles_tapped: ["Sport"],
            free_text_entered: null,
          },
        ],
        saved_at_ms: Date.now(),
      }),
    );
    const { fn, calls } = scriptedFetch([Q0]);
    await act(async () => {
      root.render(<InterviewChat onComplete={vi.fn()} fetchTurn={fn as never} forceRestart />);
    });

    expect(container.textContent).not.toContain("Pick up where you left off?");
    expect(container.textContent).toContain("What are you into?");
    // The first turn was fetched for an EMPTY state, and the stale cache is gone.
    expect(calls[0]).toHaveLength(0);
    expect(window.localStorage.getItem("n20-interview-session")).toBeNull();
  });
});
