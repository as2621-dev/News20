import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Rendered-behavior tests for the one-scrollback {@link InterviewChat} (FSR slice #18),
 * using the createRoot + act harness (no @testing-library — not a project dependency).
 * The turn fetcher is injected via `fetchTurn` (no network, no worker); jsdom supplies
 * `localStorage`.
 *
 * Rule 9 — WHY these behaviors matter (each fails on a real regression tied to an
 * acceptance criterion of the slice):
 *   - One scrollback, zero screen swaps: prior Q + A stay in the DOM after advancing
 *     (fails the instant a turn replaces the screen).
 *   - Answered steps collapse into user bubbles; history is scrollable back.
 *   - Multi-select chips + a confirm send ALL taps on one exchange; a typed interest is
 *     captured ALONGSIDE the taps (one exchange carries both).
 *   - Skip on every question commits an empty answer (drives the engine skip fast-forward).
 *   - Editing an EARLIER answer invalidates + re-asks downstream.
 *   - A failed turn shows an in-scrollback retry, never a dead-end.
 *   - Persistence fires ONLY on terminal confirm, and forwards the TUNE outputs.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { InterviewChat } from "@/components/onboarding/InterviewChat";
import type { InterviewExchange, InterviewTurn } from "@/types/interview";

/** Build a question turn with the given option labels plus the two mandated affordances. */
function question(turn_index: number, question_text: string, options: string[]): InterviewTurn {
  return {
    response_kind: "question",
    turn_index,
    question_text,
    bubbles: [
      ...options.map((bubble_label) => ({ bubble_label, bubble_kind: "option" as const })),
      { bubble_label: "not really / skip", bubble_kind: "skip" },
      { bubble_label: "something else — type it", bubble_kind: "type_your_own" },
    ],
  };
}

const Q_ROOTS = question(0, "What do you follow most closely?", ["Sport", "Tech"]);
const Q_SUBNICHE = question(1, "Which sport pulls you in?", ["Cricket", "Football"]);

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
  mute_terms: [{ mute_category: "sport", mute_term: "transfers" }],
  angle_preferences: [{ angle_category: "sport", angle_label: "tactics" }],
  deferred_questions: [],
};

const TERMINAL_EMPTY: InterviewTurn = {
  response_kind: "terminal",
  turn_index: 1,
  micro_interests: [],
  roots_only_fallback: true,
  mute_terms: [],
  angle_preferences: [],
  deferred_questions: [],
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

/** A minimal in-memory localStorage stub (the jsdom build lacks a usable `.clear()`). */
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

/** A `fetchTurn` stub returning turns in call order (last repeats), recording each state. */
function scriptedFetch(turns: InterviewTurn[]): {
  fn: (state: InterviewExchange[]) => Promise<InterviewTurn>;
  calls: InterviewExchange[][];
} {
  const calls: InterviewExchange[][] = [];
  let index = 0;
  const fn = async (state: InterviewExchange[]): Promise<InterviewTurn> => {
    calls.push(state.map((exchange) => ({ ...exchange })));
    const turn = turns[Math.min(index, turns.length - 1)];
    index += 1;
    return turn;
  };
  return { fn, calls };
}

async function renderChat(
  fetchTurn: (state: InterviewExchange[]) => Promise<InterviewTurn>,
  onComplete = vi.fn(),
): Promise<{ onComplete: ReturnType<typeof vi.fn> }> {
  await act(async () => {
    root.render(<InterviewChat onComplete={onComplete} fetchTurn={fetchTurn as never} />);
  });
  return { onComplete };
}

/** Click the first element (any tag) whose text contains `text`. Throws if not found. */
async function clickText(text: string): Promise<void> {
  const el = Array.from(container.querySelectorAll<HTMLElement>("button")).find((candidate) =>
    candidate.textContent?.includes(text),
  );
  if (!el) {
    throw new Error(`Button containing "${text}" not found. Buttons: ${bodyButtons()}`);
  }
  await act(async () => {
    el.click();
  });
}

/** Toggle an option chip by its label (only the multi-select chips, not skip/confirm). */
async function toggleChip(label: string): Promise<void> {
  const chip = Array.from(container.querySelectorAll<HTMLButtonElement>("[data-testid='option-chip']")).find(
    (candidate) => candidate.textContent?.includes(label),
  );
  if (!chip) {
    throw new Error(`Chip "${label}" not found. Chips: ${bodyButtons()}`);
  }
  await act(async () => {
    chip.click();
  });
}

function bodyButtons(): string {
  return Array.from(container.querySelectorAll("button"))
    .map((button) => button.textContent)
    .join(" | ");
}

function testId(id: string): string[] {
  return Array.from(container.querySelectorAll(`[data-testid='${id}']`)).map((node) => node.textContent ?? "");
}

/** Type into the composer via the native setter so React's controlled onChange fires. */
async function typeComposer(text: string): Promise<void> {
  const input = container.querySelector<HTMLInputElement>("[data-testid='composer-input']");
  if (!input) {
    throw new Error("composer input not found");
  }
  await act(async () => {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
    nativeSetter?.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("InterviewChat — one-scrollback interview (Rule 9)", () => {
  it("renders the first turn as chips + skip + a live composer", async () => {
    await renderChat(scriptedFetch([Q_ROOTS]).fn);
    expect(container.textContent).toContain("What do you follow most closely?");
    expect(testId("option-chip")).toEqual(["Sport", "Tech"]);
    expect(container.querySelector("[data-testid='skip-turn']")).not.toBeNull();
    const composer = container.querySelector<HTMLInputElement>("[data-testid='composer-input']");
    expect(composer?.disabled).toBe(false);
  });

  it("multi-selects chips and sends ALL taps on one exchange, keeping history visible", async () => {
    const { fn, calls } = scriptedFetch([Q_ROOTS, Q_SUBNICHE, TERMINAL]);
    await renderChat(fn);

    await toggleChip("Sport");
    await toggleChip("Tech");
    await clickText("Done →");

    // The one exchange carried BOTH selected roots (multi-select).
    expect(calls[1][0].bubbles_tapped).toEqual(["Sport", "Tech"]);
    // Zero screen swaps: the answered question AND its collapsed answer stay on screen
    // alongside the new question — the whole conversation is one scrollback.
    expect(container.textContent).toContain("What do you follow most closely?");
    expect(testId("user-bubble")).toContain("Sport · Tech");
    expect(container.textContent).toContain("Which sport pulls you in?");
  });

  it("captures a typed interest ALONGSIDE the tapped chips (composer mid-chips)", async () => {
    const { fn, calls } = scriptedFetch([Q_SUBNICHE, TERMINAL]);
    await renderChat(fn);

    await toggleChip("Cricket");
    await typeComposer("Formula 1 strategy");
    await clickText("Send");

    expect(calls[1][0].bubbles_tapped).toEqual(["Cricket"]);
    expect(calls[1][0].free_text_entered).toBe("Formula 1 strategy");
  });

  it("skip commits an empty answer on every question (drives skip fast-forward)", async () => {
    const { fn, calls } = scriptedFetch([Q_ROOTS, Q_SUBNICHE]);
    const { onComplete } = await renderChat(fn);

    await clickText("not really / skip");

    expect(calls[1][0].bubbles_tapped).toEqual([]);
    expect(calls[1][0].free_text_entered).toBeNull();
    // Nothing persisted on a skip (no terminal confirmed).
    expect(onComplete).not.toHaveBeenCalled();
    // The skipped turn collapses to a "Skipped" bubble; history stays.
    expect(testId("user-bubble")).toContain("Skipped");
  });

  it("editing an EARLIER answer re-asks from that point, invalidating downstream", async () => {
    const { fn, calls } = scriptedFetch([Q_ROOTS, Q_SUBNICHE, Q_ROOTS]);
    await renderChat(fn);

    await toggleChip("Sport");
    await clickText("Done →"); // now on Q_SUBNICHE, conversation has 1 exchange

    await clickText("EDIT ›"); // edit the first (index 0) answer
    // Re-fetched with the EMPTY prior state — the first answer and everything after invalidated.
    expect(calls[2]).toEqual([]);
  });

  it("drives a skip fast-forward offer: tapping the accept OPTION sends its label to the engine", async () => {
    // WHY (slice #18 AC #5 + the UI↔engine seam): the category-skip / build-my-feed offers arrive
    // as ordinary question turns whose ACCEPT is an `option` (engine matches it by label in
    // bubbles_tapped). The generic skip affordance sends [] (a true skip), so the accept MUST be a
    // tappable chip — this locks that tapping it + Done delivers the accept label the engine matches.
    const offer: InterviewTurn = {
      response_kind: "question",
      turn_index: 1,
      question_text: "No rush on Sport — see a few options, or skip it for now?",
      bubbles: [
        { bubble_label: "Show me some options", bubble_kind: "option" },
        { bubble_label: "Skip this topic for now", bubble_kind: "option" },
        { bubble_label: "not really / skip", bubble_kind: "skip" },
      ],
    };
    const { fn, calls } = scriptedFetch([Q_ROOTS, offer, TERMINAL]);
    await renderChat(fn);

    await toggleChip("Sport");
    await clickText("Done →"); // → offer turn
    expect(container.textContent).toContain("skip it for now");

    await toggleChip("Skip this topic for now"); // the ACCEPT, a tappable option chip
    await clickText("Done →");
    expect(calls[2][1].bubbles_tapped).toEqual(["Skip this topic for now"]);
  });

  it("shows an in-scrollback retry on a failed turn and re-sends the same state on Retry", async () => {
    const { fn, calls } = scriptedFetch([Q_ROOTS, RETRY, Q_SUBNICHE]);
    await renderChat(fn);

    await toggleChip("Sport");
    await clickText("Done →"); // → RETRY
    expect(container.textContent).toContain("couldn't reach the interview");

    await clickText("Retry"); // → Q_SUBNICHE
    expect(container.textContent).toContain("Which sport pulls you in?");
    // The retry re-sent the EXACT state the failed turn was fetching.
    expect(calls[2]).toEqual(calls[1]);
  });

  it("persists ONLY on terminal confirm and forwards interests + TUNE outputs", async () => {
    const { fn } = scriptedFetch([Q_ROOTS, TERMINAL]);
    const { onComplete } = await renderChat(fn);

    await toggleChip("Sport");
    await clickText("Done →");
    // Terminal card is inline in the scrollback; nothing persisted until confirm.
    expect(container.textContent).toContain("IPL auctions");
    expect(onComplete).not.toHaveBeenCalled();

    await clickText("Looks good");
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0]).toEqual({
      micro_interests: (TERMINAL as Extract<InterviewTurn, { response_kind: "terminal" }>).micro_interests,
      roots_only_fallback: false,
      mute_terms: [{ mute_category: "sport", mute_term: "transfers" }],
      angle_preferences: [{ angle_category: "sport", angle_label: "tactics" }],
    });
  });

  it("skip-everything reaches a roots-only confirm and completes (skipping not punished)", async () => {
    const { onComplete } = await renderChat(scriptedFetch([Q_ROOTS, TERMINAL_EMPTY]).fn);

    await clickText("not really / skip");
    expect(container.textContent).toContain("big picture");

    await clickText("Continue");
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0]).toMatchObject({ micro_interests: [], roots_only_fallback: true });
  });

  it("ignores a double-tap on confirm so persistence fires once", async () => {
    const { onComplete } = await renderChat(scriptedFetch([Q_ROOTS, TERMINAL]).fn);
    await toggleChip("Sport");
    await clickText("Done →"); // → TERMINAL card

    const confirm = container.querySelector<HTMLButtonElement>("[data-testid='confirm-terminal']");
    await act(async () => {
      confirm?.click();
      confirm?.click();
    });

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("offers resume when a transcript is cached, and resumes on Resume", async () => {
    window.localStorage.setItem(
      "n20-interview-session",
      JSON.stringify({
        version: 1,
        conversation_state: [
          {
            question_text: "What do you follow most closely?",
            bubbles_offered: ["Sport"],
            bubbles_tapped: ["Sport"],
            free_text_entered: null,
          },
        ],
        saved_at_ms: Date.now(),
      }),
    );
    const { fn, calls } = scriptedFetch([Q_SUBNICHE]);
    await renderChat(fn);

    expect(container.textContent).toContain("Pick up where you left off?");
    await clickText("Resume");
    expect(calls[0]).toHaveLength(1);
    expect(container.textContent).toContain("Which sport pulls you in?");
  });

  it("forceRestart skips the resume prompt, clears the cache, and starts fresh (issue #9)", async () => {
    window.localStorage.setItem(
      "n20-interview-session",
      JSON.stringify({
        version: 1,
        conversation_state: [
          {
            question_text: "What do you follow most closely?",
            bubbles_offered: ["Sport"],
            bubbles_tapped: ["Sport"],
            free_text_entered: null,
          },
        ],
        saved_at_ms: Date.now(),
      }),
    );
    const { fn, calls } = scriptedFetch([Q_ROOTS]);
    await act(async () => {
      root.render(<InterviewChat onComplete={vi.fn()} fetchTurn={fn as never} forceRestart />);
    });

    expect(container.textContent).not.toContain("Pick up where you left off?");
    expect(container.textContent).toContain("What do you follow most closely?");
    expect(calls[0]).toHaveLength(0);
    expect(window.localStorage.getItem("n20-interview-session")).toBeNull();
  });
});
