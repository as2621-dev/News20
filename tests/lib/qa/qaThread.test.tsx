import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QaComposer } from "@/components/detail/QaComposer";
import { QaThread, type QaTurn } from "@/components/detail/QaThread";
import { SuggestedQuestionChips } from "@/components/detail/SuggestedQuestionChips";
import type { SuggestedQuestion } from "@/types/detail";
import type { QuestionAnswer } from "@/types/qa";

/**
 * Component tests for the Phase 2b SP3 Q&A frontend (QaThread + QaComposer +
 * SuggestedQuestionChips).
 *
 * Rule 9 — these encode WHY the behaviour matters, not just WHAT renders. The
 * grounded-vs-refusal split is the system's TRUST GUARANTEE (port-map §7,
 * Decision #5): `answer_is_grounded` is the single switch, and the contract is
 * absolute:
 *   - `answer_is_grounded === false` MUST render the `.qa-refusal` blush card with
 *     the `⌀ CAN'T ANSWER FROM SOURCE` header and MUST NEVER render an answer
 *     bubble or a citation chip. A regression that surfaced a refusal as a normal
 *     answer (or leaked a chip onto it) would present an ungrounded guess as fact —
 *     the exact failure this test exists to catch.
 *   - a grounded answer MUST render exactly one `.cite-chip` per `answer_citations`
 *     entry (no more, no fewer), because each chip is a provenance claim a user
 *     trusts; a wrong chip count is a wrong trust claim.
 *
 * Rendering uses React 19's `react-dom/client` + `react`'s `act` directly (no
 * @testing-library — not a project dependency; matches the existing
 * `tests/lib/detail/*.test.tsx` idiom). No network is touched — these components
 * are pure-prop; `askQuestion` is exercised in the StoryDetail flow test.
 */

/** A grounded answer with two distinct citations (two chips expected). */
const GROUNDED_ANSWER: QuestionAnswer = {
  answer_text: "The Strait of Hormuz carries roughly a fifth of global seaborne oil.",
  answer_is_grounded: true,
  answer_citations: [
    {
      source_url: "https://reuters.com/world/hormuz",
      source_quote: "about 20% of the world's oil passes through the strait",
      source_outlet_name: "Reuters",
      passage_id: "detail_chunk:0",
    },
    {
      source_url: "https://apnews.com/hormuz",
      source_quote: "tankers reroute amid rising tension",
      source_outlet_name: "AP",
      passage_id: "detail_chunk:1",
    },
  ],
};

/** A refusal: not grounded, fixed copy, ZERO citations (the trust guarantee). */
const REFUSAL_ANSWER: QuestionAnswer = {
  answer_text: "I can only answer from this story's source — that isn't covered here.",
  answer_is_grounded: false,
  answer_citations: [],
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.clearAllMocks();
});

/** Render any element into the test container and flush effects. */
function render(node: React.ReactElement): void {
  act(() => {
    root.render(node);
  });
}

/**
 * Type into a React-controlled input. Setting `.value` directly bypasses React's
 * tracked value, so React's `onChange` never fires; we go through the native value
 * setter and dispatch a bubbling `input` event so the controlled state updates.
 */
function typeIntoInput(input: HTMLInputElement, value: string): void {
  const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  act(() => {
    nativeSetter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("QaThread — grounded answer renders one .cite-chip per citation (Rule 9)", () => {
  it("renders the answer text and exactly one citation chip per answer_citations entry", () => {
    const turns: QaTurn[] = [
      { turn_id: 1, question_text: "Why does Hormuz matter?", phase: "answered", answer: GROUNDED_ANSWER },
    ];
    render(<QaThread turns={turns} />);

    // The grounded bubble renders, and NO refusal card.
    expect(container.querySelector("[data-qa-bubble-a]")).not.toBeNull();
    expect(container.querySelector("[data-qa-refusal]")).toBeNull();

    // WHY: exactly one chip per citation — a wrong count is a wrong trust claim.
    const chips = container.querySelectorAll("[data-cite-chip]");
    expect(chips).toHaveLength(GROUNDED_ANSWER.answer_citations.length);
    // Each chip's label is its citation outlet name.
    const chipLabels = Array.from(chips).map((chip) => chip.textContent);
    expect(chipLabels).toEqual(["Reuters", "AP"]);

    // The question + answer text both render.
    expect(container.textContent).toContain("Why does Hormuz matter?");
    expect(container.textContent).toContain("a fifth of global seaborne oil");
  });
});

describe("QaThread — refusal renders the .qa-refusal card and NEVER an answer bubble (Rule 9)", () => {
  it("renders the ⌀ CAN'T ANSWER FROM SOURCE card with no answer bubble and no chips", () => {
    const turns: QaTurn[] = [
      { turn_id: 1, question_text: "What's the weather?", phase: "answered", answer: REFUSAL_ANSWER },
    ];
    render(<QaThread turns={turns} />);

    // WHY (the trust guarantee): a not-grounded answer MUST be the refusal card.
    const refusal = container.querySelector("[data-qa-refusal]");
    expect(refusal).not.toBeNull();
    expect(refusal?.textContent).toContain("CAN");
    expect(refusal?.textContent).toContain("ANSWER FROM SOURCE");

    // WHY: it MUST NEVER be rendered as a normal answer bubble, and MUST carry NO
    // citation chip — a refusal that surfaced as an answer, or leaked a chip, would
    // present an ungrounded guess as fact.
    expect(container.querySelector("[data-qa-bubble-a]")).toBeNull();
    expect(container.querySelectorAll("[data-cite-chip]")).toHaveLength(0);
  });

  it("never leaks a citation chip even if a refusal answer carried citations (defense in depth)", () => {
    // Construct an INVALID refusal that wrongly carries citations; the renderer
    // must still show the refusal card and NOT the chips (is_grounded is the gate).
    const dirtyRefusal: QuestionAnswer = { ...REFUSAL_ANSWER, answer_citations: GROUNDED_ANSWER.answer_citations };
    const turns: QaTurn[] = [{ turn_id: 1, question_text: "Off topic?", phase: "answered", answer: dirtyRefusal }];
    render(<QaThread turns={turns} />);

    expect(container.querySelector("[data-qa-refusal]")).not.toBeNull();
    expect(container.querySelector("[data-qa-bubble-a]")).toBeNull();
    // The refusal branch renders no chips regardless of the (invalid) payload.
    expect(container.querySelectorAll("[data-cite-chip]")).toHaveLength(0);
  });
});

describe("QaThread — thinking state (the dot-typing beat before every answer)", () => {
  it("renders the .dot-typing thinking bubble while a turn is in flight", () => {
    const turns: QaTurn[] = [{ turn_id: 1, question_text: "Asking…", phase: "thinking", answer: null }];
    render(<QaThread turns={turns} />);

    // WHY: every answer is preceded by the thinking beat (port-map §7).
    expect(container.querySelector("[data-qa-thinking]")).not.toBeNull();
    expect(container.querySelectorAll(".dot-typing")).toHaveLength(3);
    // No answer state yet.
    expect(container.querySelector("[data-qa-bubble-a]")).toBeNull();
    expect(container.querySelector("[data-qa-refusal]")).toBeNull();
  });
});

describe("SuggestedQuestionChips — tapping a chip asks that question", () => {
  const SUGGESTED: SuggestedQuestion[] = [
    { question_index: 0, question_text: "Why does Hormuz matter?" },
    { question_index: 1, question_text: "Who controls the strait?" },
  ];

  it("renders one chip per suggested question and calls onAsk with the chip text on tap", () => {
    const onAsk = vi.fn();
    render(<SuggestedQuestionChips suggestedQuestions={SUGGESTED} onAsk={onAsk} />);

    const chips = container.querySelectorAll<HTMLButtonElement>("[data-qa-chip]");
    expect(chips).toHaveLength(2);

    act(() => {
      chips[1].click();
    });
    // WHY: tapping a chip is equivalent to typing that exact question.
    expect(onAsk).toHaveBeenCalledWith("Who controls the strait?");
  });

  it("renders nothing when there are no suggested questions (no empty row)", () => {
    render(<SuggestedQuestionChips suggestedQuestions={[]} onAsk={vi.fn()} />);
    expect(container.querySelector("[data-qa-chips]")).toBeNull();
  });
});

describe("QaComposer — submitting asks the trimmed question", () => {
  it("calls onAsk with the trimmed text on submit and clears the field", () => {
    const onAsk = vi.fn();
    render(<QaComposer onAsk={onAsk} />);

    const input = container.querySelector<HTMLInputElement>("[data-qa-input]");
    const form = container.querySelector<HTMLFormElement>("[data-qa-composer]");
    expect(input).not.toBeNull();
    expect(form).not.toBeNull();

    if (input) {
      typeIntoInput(input, "  How did it start?  ");
    }
    act(() => {
      form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    // WHY: leading/trailing whitespace is trimmed (matches the prototype guard).
    expect(onAsk).toHaveBeenCalledWith("How did it start?");
    // Field clears after a successful ask.
    expect(input?.value).toBe("");
  });

  it("ignores an empty/whitespace-only submit (no blank turn)", () => {
    const onAsk = vi.fn();
    render(<QaComposer onAsk={onAsk} />);
    const form = container.querySelector<HTMLFormElement>("[data-qa-composer]");
    act(() => {
      form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(onAsk).not.toHaveBeenCalled();
  });
});
