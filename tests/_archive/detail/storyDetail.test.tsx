import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StoryDetail as StoryDetailPayload } from "@/types/detail";
import type { Story } from "@/types/feed";

/**
 * Component test for `StoryDetail` (Phase 2 SP2).
 *
 * Rule 9 — this encodes WHY the chunk ordering matters, not just that text
 * renders: the Detail reading body is a CHUNKED narrative whose meaning depends on
 * paragraph order. `fetchStoryDetail` returns chunks already sorted by
 * `chunk_index`; the component MUST render them in that received order and never
 * re-sort or reverse them. The mock below returns chunks in a deliberate,
 * non-alphabetical, ascending-index order, and the test asserts the rendered
 * paragraphs appear in exactly that DOM order. If the component drops the order
 * (sorts by text, reverses, or shuffles), the rendered sequence diverges from the
 * fetched sequence and this test FAILS — which is the whole point.
 *
 * Rendering uses React 19's `react-dom/client` + `react`'s `act` directly (no
 * @testing-library — not a project dependency, and the scope lock forbids adding
 * one). `fetchStoryDetail` is mocked at the module boundary (CLAUDE.md mocking
 * strategy); no Supabase client is touched.
 */

const { mockFetchStoryDetail } = vi.hoisted(() => ({
  mockFetchStoryDetail: vi.fn(),
}));

vi.mock("@/lib/detail/fetchStoryDetail", () => ({
  fetchStoryDetail: mockFetchStoryDetail,
}));

// Imported AFTER the mock is registered so the component picks up the mock.
const { StoryDetail } = await import("@/components/detail/StoryDetail");

/** A minimal active story — only the fields `StoryDetail` reads need be real. */
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

/**
 * The mocked Detail payload. Chunks are in ascending `chunk_index` order (as the
 * real fetch returns them) but their TEXT is intentionally NOT alphabetical, so a
 * stray text-sort would reorder them and fail the assertion below.
 */
const ORDERED_DETAIL: StoryDetailPayload = {
  story_id: "s1",
  detail_chunks: [
    { chunk_index: 0, chunk_text: "Zulu — the opening paragraph sets the scene." },
    { chunk_index: 1, chunk_text: "Yankee — the second paragraph adds context." },
    { chunk_index: 2, chunk_text: "Xray — the third paragraph draws the conclusion." },
  ],
  trust_summary: {
    coverage_left_count: 9,
    coverage_center_count: 7,
    coverage_right_count: 3,
    coverage_outlet_count: 19,
    blindspot_lean: "right",
    opposing_view_text: "A dissenting read.",
  },
  key_figure: { key_figure_value: "~20%", key_figure_label: "of global oil transits Hormuz" },
  sources: [],
  timeline: [
    { timeline_event_index: 0, timeline_when_label: "08:10", timeline_what_text: "First report." },
    { timeline_event_index: 1, timeline_when_label: "Mon", timeline_what_text: "Escalation." },
  ],
  suggested_questions: [],
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  mockFetchStoryDetail.mockReset();
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

/** Render `StoryDetail` and flush the resolved fetch + effects. */
async function renderStoryDetail(payload: StoryDetailPayload): Promise<void> {
  mockFetchStoryDetail.mockResolvedValue(payload);
  const scrollContainerRef = { current: null as HTMLDivElement | null };
  await act(async () => {
    root.render(<StoryDetail story={ACTIVE_STORY} scrollContainerRef={scrollContainerRef} />);
  });
  // Reason: the fetch resolves on a microtask; a second act flush lets the
  // resolved state commit before assertions.
  await act(async () => {
    await Promise.resolve();
  });
}

describe("StoryDetail — renders the chunked body in chunk_index order (Rule 9)", () => {
  it("renders every chunk in the exact order fetchStoryDetail returned them", async () => {
    await renderStoryDetail(ORDERED_DETAIL);

    const renderedChunks = Array.from(container.querySelectorAll<HTMLElement>("[data-chunk-index]"));

    // WHY: one paragraph per fetched chunk, none dropped or duplicated.
    expect(renderedChunks).toHaveLength(ORDERED_DETAIL.detail_chunks.length);

    // WHY: the rendered DOM order must equal the fetched order. The fetch hands
    // back chunk_index ascending; the component must NOT re-sort or reverse. This
    // compares against the fetched sequence directly, so any reordering fails.
    const renderedTexts = renderedChunks.map((node) => node.textContent);
    const expectedTexts = ORDERED_DETAIL.detail_chunks.map((chunk) => chunk.chunk_text);
    expect(renderedTexts).toEqual(expectedTexts);

    // WHY: belt-and-braces — the data-chunk-index attributes must also ascend in
    // DOM order (a reversal would make these descend even if texts matched a
    // re-sort).
    const renderedIndices = renderedChunks.map((node) => Number(node.dataset.chunkIndex));
    expect(renderedIndices).toEqual([0, 1, 2]);

    // The chunks render as the Playfair reading body (port-map §4 — font-serif).
    for (const node of renderedChunks) {
      expect(node.className).toContain("font-serif");
    }

    expect(mockFetchStoryDetail).toHaveBeenCalledWith("s1");
  });

  it("renders the key figure for a story that has one", async () => {
    await renderStoryDetail(ORDERED_DETAIL);
    // WHY: the key-figure card is part of the Detail's value; a present figure
    // must show its value text.
    expect(container.textContent).toContain("~20%");
    expect(container.textContent).toContain("of global oil transits Hormuz");
  });

  it("omits the key figure card when the story has no key figure (null)", async () => {
    // WHY: nullable key figure — a null value must render NO card, not an empty
    // shell (the component returns null). Edge/failure-of-assumption case.
    const noKeyFigure: StoryDetailPayload = {
      ...ORDERED_DETAIL,
      key_figure: { key_figure_value: null, key_figure_label: null },
    };
    await renderStoryDetail(noKeyFigure);
    expect(container.textContent).not.toContain("~20%");
    // The chunked body still renders — the card omission must not drop the body.
    expect(container.querySelectorAll("[data-chunk-index]")).toHaveLength(3);
  });
});
