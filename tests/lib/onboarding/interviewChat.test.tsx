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

/** Click a button by its `data-testid` exactly once. Throws if absent. */
async function clickTestId(id: string): Promise<void> {
  const el = container.querySelector<HTMLButtonElement>(`[data-testid='${id}']`);
  if (!el) {
    throw new Error(`[data-testid='${id}'] not found. Buttons: ${bodyButtons()}`);
  }
  await act(async () => {
    el.click();
  });
}

/** Click a `data-testid` button `times` times (drives the ± budget steppers). */
async function clickTestIdN(id: string, times: number): Promise<void> {
  for (let i = 0; i < times; i += 1) {
    await clickTestId(id);
  }
}

/** Read the current numeric value shown for one budget axis (e.g. "news"). */
function budgetValue(axis: "news" | "youtube" | "x"): string {
  const label = axis === "news" ? "news" : axis === "youtube" ? "youtube" : "x";
  return container.querySelector(`[data-testid='budget-value-${label}']`)?.textContent ?? "";
}

/** Drive a fresh chat to the budget card (one interest tapped, interests accepted). */
async function reachBudgetCard(onComplete = vi.fn()): Promise<ReturnType<typeof vi.fn>> {
  const { fn } = scriptedFetch([Q_ROOTS, TERMINAL]);
  const res = await renderChat(fn, onComplete);
  await toggleChip("Sport");
  await clickText("Done →");
  await clickText("Looks good");
  return res.onComplete;
}

/**
 * Skip through the in-chat YOUTUBE + X CLUSTERS pickers (#20) with zero picks — the closing arc
 * now runs budget → youtube → x_clusters → summary. Each picker's confirm is present immediately
 * (independent of the catalog load), so this advances without awaiting the loaders.
 */
async function advanceThroughSourcePickers(): Promise<void> {
  await clickTestId("youtube-confirm");
  await clickTestId("x-cluster-confirm");
}

const TERMINAL_INTERESTS = (TERMINAL as Extract<InterviewTurn, { response_kind: "terminal" }>).micro_interests;

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

  it("nothing persists until the WHOLE closing arc completes; forwards interests + TUNE + deferred + top_split", async () => {
    // WHY (AC1/AC2): "Looks good" only ACCEPTS the interests → the budget card; the single persist
    // trigger is "Build my 30" at the end of the closing arc. The payload carries the full profile
    // plus the client-computed default 20/7/3 split (deferred skips included) in ONE handoff.
    const { fn } = scriptedFetch([Q_ROOTS, TERMINAL]);
    const { onComplete } = await renderChat(fn);

    await toggleChip("Sport");
    await clickText("Done →");
    expect(container.textContent).toContain("IPL auctions");
    expect(onComplete).not.toHaveBeenCalled();

    // Accept interests → the budget card renders; STILL nothing persisted.
    await clickText("Looks good");
    expect(container.querySelector("[data-testid='budget-card']")).not.toBeNull();
    expect(onComplete).not.toHaveBeenCalled();

    // Default split already sums to 30 → review → source pickers → YOUR-30 summary → build.
    await clickText("Review my 30");
    await advanceThroughSourcePickers();
    expect(container.querySelector("[data-testid='your-30-summary']")).not.toBeNull();
    expect(onComplete).not.toHaveBeenCalled();

    await clickText("Build my 30");
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0]).toEqual({
      micro_interests: TERMINAL_INTERESTS,
      roots_only_fallback: false,
      mute_terms: [{ mute_category: "sport", mute_term: "transfers" }],
      angle_preferences: [{ angle_category: "sport", angle_label: "tactics" }],
      deferred_questions: [],
      top_split: { news: 20, youtube: 7, x: 3 },
      source_follows: { youtube_source_ids: [], clusters: [] },
    });
  });

  it("YOUR-30 summary shows the news/YouTube/X split before Build my 30 (AC2)", async () => {
    await reachBudgetCard();
    await clickText("Review my 30");
    await advanceThroughSourcePickers();
    expect(container.querySelector("[data-testid='summary-news']")?.textContent).toContain("20");
    expect(container.querySelector("[data-testid='summary-youtube']")?.textContent).toContain("7");
    expect(container.querySelector("[data-testid='summary-x']")?.textContent).toContain("3");
  });

  it("the budget card pins the total at 30 — Review is gated until it sums to 30 (AC4)", async () => {
    await reachBudgetCard();
    const review = () => container.querySelector<HTMLButtonElement>("[data-testid='budget-review']");
    // Default 20/7/3 = 30 → review enabled.
    expect(review()?.disabled).toBe(false);
    // Incrementing an axis while the pool is full is a no-op — the total can never exceed 30.
    await clickTestId("budget-inc-news");
    expect(budgetValue("news")).toBe("20");
    // Drop below 30 → review gated until the user re-allocates the freed slot.
    await clickTestId("budget-dec-news");
    expect(budgetValue("news")).toBe("19");
    expect(review()?.disabled).toBe(true);
    await clickTestId("budget-inc-youtube");
    expect(review()?.disabled).toBe(false);
  });

  it("remixes the budget to 30/0/0 and forwards it (news maxed, sources zeroed) (AC3)", async () => {
    const onComplete = await reachBudgetCard();
    await clickTestIdN("budget-dec-youtube", 7); // youtube 7 → 0
    await clickTestIdN("budget-dec-x", 3); // x 3 → 0
    await clickTestIdN("budget-inc-news", 10); // news 20 → 30
    expect(budgetValue("news")).toBe("30");
    await clickText("Review my 30");
    await advanceThroughSourcePickers();
    await clickText("Build my 30");
    expect(onComplete.mock.calls[0][0]).toMatchObject({ top_split: { news: 30, youtube: 0, x: 0 } });
  });

  it("remixes the budget to 0/0/30 and forwards it (all X) (AC3)", async () => {
    const onComplete = await reachBudgetCard();
    await clickTestIdN("budget-dec-news", 20); // news 20 → 0
    await clickTestIdN("budget-dec-youtube", 7); // youtube 7 → 0
    await clickTestIdN("budget-inc-x", 27); // x 3 → 30
    expect(budgetValue("x")).toBe("30");
    await clickText("Review my 30");
    await advanceThroughSourcePickers();
    await clickText("Build my 30");
    expect(onComplete.mock.calls[0][0]).toMatchObject({ top_split: { news: 0, youtube: 0, x: 30 } });
  });

  it("skip-everything reaches a roots-only confirm and completes through the budget (skipping not punished)", async () => {
    const { onComplete } = await renderChat(scriptedFetch([Q_ROOTS, TERMINAL_EMPTY]).fn);

    await clickText("not really / skip");
    expect(container.textContent).toContain("big picture");

    // The budget card runs for the skip path too, so a roots-only allocation still persists.
    await clickText("Continue");
    expect(container.querySelector("[data-testid='budget-card']")).not.toBeNull();
    await clickText("Review my 30");
    await advanceThroughSourcePickers();
    await clickText("Build my 30");
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0]).toMatchObject({
      micro_interests: [],
      roots_only_fallback: true,
      top_split: { news: 20, youtube: 7, x: 3 },
    });
  });

  it("ignores a double-tap on Build my 30 so persistence fires once", async () => {
    const onComplete = await reachBudgetCard();
    await clickText("Review my 30");
    await advanceThroughSourcePickers();

    const build = container.querySelector<HTMLButtonElement>("[data-testid='build-my-30']");
    await act(async () => {
      build?.click();
      build?.click();
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
