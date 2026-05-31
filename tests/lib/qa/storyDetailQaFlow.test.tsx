import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StoryDetail as StoryDetailPayload } from "@/types/detail";
import type { Story } from "@/types/feed";
import type { QuestionAnswer } from "@/types/qa";

/**
 * Flow test for the Phase 2b SP3 Q&A mount inside `StoryDetail`: tapping a
 * suggested chip (or submitting the composer) shows the `.dot-typing` THINKING
 * state, then flips that turn to the grounded bubble OR the refusal card.
 *
 * Rule 9 — this encodes WHY the transition matters: the thinking→answer beat is
 * the trust ritual (port-map §7), and the refusal path MUST land on the
 * `.qa-refusal` card and NEVER an answer bubble. Both `fetchStoryDetail` (the
 * Detail payload) and `askQuestion` (the worker call) are mocked at the module
 * boundary (CLAUDE.md mocking) — no Supabase, no network.
 *
 * Rendering uses React 19's `react-dom/client` + `react`'s `act` directly (no
 * @testing-library — not a project dependency; matches `tests/lib/detail/*`).
 */

const { mockFetchStoryDetail, mockAskQuestion } = vi.hoisted(() => ({
  mockFetchStoryDetail: vi.fn(),
  mockAskQuestion: vi.fn(),
}));

vi.mock("@/lib/detail/fetchStoryDetail", () => ({ fetchStoryDetail: mockFetchStoryDetail }));
vi.mock("@/lib/qa/askQuestion", () => ({ askQuestion: mockAskQuestion }));

// Imported AFTER the mocks are registered so the component picks them up.
const { StoryDetail } = await import("@/components/detail/StoryDetail");

const ACTIVE_STORY: Story = {
  digest_id: "s1",
  headline: "Hormuz tensions spike",
  segment_key: "geopolitics",
  segment_label: "Geopolitics",
  segment_accent_hex: "#EF4444",
  anchors: ["ALEX", "JORDAN"],
  digest_audio_url: "https://example.test/s1.mp3",
  audio_duration_ms: 13000,
  speech_end_ms: 13000,
  poster_url: "https://example.test/s1.jpg",
  caption_sentences: [],
};

/** A Detail payload with two suggested-question chips so a tap can start a turn. */
const DETAIL_WITH_QUESTIONS: StoryDetailPayload = {
  story_id: "s1",
  detail_chunks: [{ chunk_index: 0, chunk_text: "A paragraph of body." }],
  trust_summary: {
    coverage_left_count: 9,
    coverage_center_count: 7,
    coverage_right_count: 3,
    coverage_outlet_count: 19,
    blindspot_lean: "right",
    opposing_view_text: null,
  },
  key_figure: { key_figure_value: null, key_figure_label: null },
  sources: [],
  timeline: [],
  suggested_questions: [
    { question_index: 0, question_text: "Why does Hormuz matter?" },
    { question_index: 1, question_text: "What's the weather?" },
  ],
};

const GROUNDED_ANSWER: QuestionAnswer = {
  answer_text: "Roughly a fifth of global seaborne oil transits the strait.",
  answer_is_grounded: true,
  answer_citations: [
    {
      source_url: "https://reuters.com/world/hormuz",
      source_quote: "about 20% of the world's oil",
      source_outlet_name: "Reuters",
      passage_id: "detail_chunk:0",
    },
  ],
};

const REFUSAL_ANSWER: QuestionAnswer = {
  answer_text: "I can only answer from this story's source — that isn't covered here.",
  answer_is_grounded: false,
  answer_citations: [],
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  mockFetchStoryDetail.mockReset();
  mockAskQuestion.mockReset();
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

/** Mount StoryDetail with the detail payload resolved + effects flushed. */
async function mountWithDetail(payload: StoryDetailPayload): Promise<void> {
  mockFetchStoryDetail.mockResolvedValue(payload);
  const scrollContainerRef = { current: null as HTMLDivElement | null };
  await act(async () => {
    root.render(<StoryDetail story={ACTIVE_STORY} scrollContainerRef={scrollContainerRef} />);
  });
  await act(async () => {
    await Promise.resolve();
  });
}

/** Click the suggested-question chip whose text matches. */
function tapChip(questionText: string): void {
  const chips = Array.from(container.querySelectorAll<HTMLButtonElement>("[data-qa-chip]"));
  const chip = chips.find((node) => node.textContent === questionText);
  if (!chip) {
    throw new Error(`suggested chip "${questionText}" not rendered`);
  }
  act(() => {
    chip.click();
  });
}

describe("StoryDetail Q&A flow — thinking → grounded answer (Rule 9)", () => {
  it("shows the dot-typing thinking state, then a grounded bubble with citation chips", async () => {
    // A deferred answer lets us observe the THINKING state before it resolves.
    let resolveAnswer: (answer: QuestionAnswer) => void = () => undefined;
    mockAskQuestion.mockReturnValue(
      new Promise<QuestionAnswer>((resolve) => {
        resolveAnswer = resolve;
      }),
    );

    await mountWithDetail(DETAIL_WITH_QUESTIONS);
    tapChip("Why does Hormuz matter?");

    // WHY: tapping a chip asks that exact question via the worker endpoint.
    expect(mockAskQuestion).toHaveBeenCalledWith("s1", "Why does Hormuz matter?");

    // While in flight: the thinking beat shows, no answer yet.
    expect(container.querySelector("[data-qa-thinking]")).not.toBeNull();
    expect(container.querySelector("[data-qa-bubble-a]")).toBeNull();

    // Resolve the answer → the turn flips to the grounded bubble + chips.
    await act(async () => {
      resolveAnswer(GROUNDED_ANSWER);
      await Promise.resolve();
    });

    expect(container.querySelector("[data-qa-thinking]")).toBeNull();
    expect(container.querySelector("[data-qa-bubble-a]")).not.toBeNull();
    expect(container.querySelectorAll("[data-cite-chip]")).toHaveLength(1);
    expect(container.textContent).toContain("Reuters");
  });
});

describe("StoryDetail Q&A flow — thinking → refusal never an answer bubble (Rule 9)", () => {
  it("renders the refusal card and NEVER an answer bubble for an off-source question", async () => {
    mockAskQuestion.mockResolvedValue(REFUSAL_ANSWER);

    await mountWithDetail(DETAIL_WITH_QUESTIONS);
    tapChip("What's the weather?");
    expect(mockAskQuestion).toHaveBeenCalledWith("s1", "What's the weather?");

    // Flush the resolved refusal answer.
    await act(async () => {
      await Promise.resolve();
    });

    // WHY (the trust guarantee): a not-grounded answer MUST land on the refusal
    // card and MUST NEVER surface as an answer bubble or carry a citation chip.
    expect(container.querySelector("[data-qa-refusal]")).not.toBeNull();
    expect(container.querySelector("[data-qa-bubble-a]")).toBeNull();
    expect(container.querySelectorAll("[data-cite-chip]")).toHaveLength(0);
    expect(container.textContent).toContain("ANSWER FROM SOURCE");
  });
});
