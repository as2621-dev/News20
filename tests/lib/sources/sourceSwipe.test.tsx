import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Deck-logic tests for the Phase 5c source-swipe (`SourceSwipe`) — the testable
 * core of the Tinder-style source-onboarding deck.
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (NO @testing-library — not a project dependency; the scope lock forbids
 * adding one), mirroring `tests/lib/sources/sourceCard.test.tsx`.
 *
 * The data hook (`loadSourceSwipeDeck`) + the persistence boundary (`followSource`/
 * `unfollowSource` in `@/lib/sources`) are MOCKED at the module boundary, per
 * CLAUDE.md mocking strategy — no real Supabase, no real recommender. The curtain
 * is dismissed via its Skip button so each test drives the deck deterministically.
 *
 * Rule 9 — these encode WHY the deck behaviour matters, not just WHAT renders:
 *   - Swipe-right / Follow is the WHOLE POINT: it must persist the LEAD source
 *     (followSource) AND advance AND bump the "followed this set" count. A deck that
 *     advanced without following (or followed the wrong card) silently loses the
 *     user's choice — the test asserts the followed source_id + the count + advance.
 *   - Skip must advance WITHOUT following — a skip that persisted would follow
 *     everything the user rejected. The test asserts no followSource call.
 *   - Undo must revert the position AND unfollow a prior follow — an undo that left
 *     the follow persisted is a broken promise ("I took that back"). The test
 *     asserts unfollowSource fired with the undone source_id.
 *   - Finishing the LAST set must call onDone(total) with the right total — the
 *     hand-off to the reel depends on it. (Marking source-onboarding complete +
 *     routing is OnboardingFlow's `onDone` handler, asserted at that layer; here we
 *     assert the SourceSwipe→onDone contract.)
 *   - A broken thumbnail must degrade to the initials-gradient fallback (the card's
 *     resilience contract) — the test fires onError and asserts the initials show.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import type { SourceSwipeCardModel, SourceSwipeDeck } from "@/lib/sourceSwipeData";
import type { ContentSourceType } from "@/types/source";

// ── Mock the data hook + the persistence boundary (hoisted by Vitest) ──────────
const loadSourceSwipeDeckMock = vi.fn();
vi.mock("@/lib/sourceSwipeData", async () => {
  // Keep the real platform constants/types; only stub the async loader.
  const actual = await vi.importActual<typeof import("@/lib/sourceSwipeData")>("@/lib/sourceSwipeData");
  return { ...actual, loadSourceSwipeDeck: (...args: unknown[]) => loadSourceSwipeDeckMock(...args) };
});

const followSourceMock = vi.fn();
const unfollowSourceMock = vi.fn();
vi.mock("@/lib/sources", () => ({
  followSource: (...args: unknown[]) => followSourceMock(...args),
  unfollowSource: (...args: unknown[]) => unfollowSourceMock(...args),
}));

// Import the SUT AFTER the mocks register so it binds the mocked deps.
const { SourceSwipe } = await import("@/components/sources/SourceSwipe");

/** Build a card view-model, overriding only the fields a case cares about. */
function makeCard(overrides: Partial<SourceSwipeCardModel> = {}): SourceSwipeCardModel {
  return {
    source_id: "src-1",
    platform_kind: "youtube_channel" as ContentSourceType,
    source_name: "Two Minute Papers",
    thumbnail_url: null,
    follower_label: "1.6M",
    coverage_tags: ["ai"],
    why_text: "A top AI pick.",
    match_pct: 94,
    accent_color: "#22D3EE",
    is_already_added: false,
    ...overrides,
  };
}

/** Build a deck. By default every platform but the named one is EMPTY (so a set finishes fast). */
function makeDeck(cardsByPlatform: Partial<SourceSwipeDeck["cards_by_platform"]>): SourceSwipeDeck {
  return {
    cards_by_platform: {
      yt: cardsByPlatform.yt ?? [],
      pod: cardsByPlatform.pod ?? [],
      x: cardsByPlatform.x ?? [],
      people: cardsByPlatform.people ?? [],
    },
    archetype_label: "AI-Frontier Tech",
    curtain_picks: ["Ai", "Tech"],
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  followSourceMock.mockResolvedValue(undefined);
  unfollowSourceMock.mockResolvedValue(undefined);
  vi.useFakeTimers();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.useRealTimers();
});

/** Render SourceSwipe and flush the deck load (a resolved promise + the curtain skip). */
async function renderDeck(deck: SourceSwipeDeck, onDone = vi.fn()): Promise<{ onDone: ReturnType<typeof vi.fn> }> {
  loadSourceSwipeDeckMock.mockResolvedValue(deck);
  await act(async () => {
    root.render(<SourceSwipe onDone={onDone} />);
  });
  // Flush the load promise (microtasks) so the deck renders past `loading`.
  await act(async () => {
    await Promise.resolve();
  });
  // Dismiss the curtain to reach the interactive deck.
  const skip = container.querySelector<HTMLButtonElement>("[data-curtain-skip]");
  if (skip) {
    act(() => {
      skip.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }
  return { onDone };
}

/** Click an action button by its data-action, then flush microtasks + the commit timer. */
async function clickAction(action: "skip" | "undo" | "follow" | "see-briefing"): Promise<void> {
  const button = container.querySelector<HTMLButtonElement>(`[data-action="${action}"]`);
  if (!button) {
    throw new Error(`action button "${action}" not found`);
  }
  act(() => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  // Advance the commit fly-off timer (340ms) so the next card renders.
  await act(async () => {
    vi.advanceTimersByTime(360);
    await Promise.resolve();
  });
}

/** The lead card's name (the topmost interactive card). */
function leadName(): string | undefined {
  return container.querySelector('[data-source-swipe-card="lead"] .nm')?.textContent ?? undefined;
}

/** The "<n> FOLLOWED THIS SET" count. */
function setCount(): string | undefined {
  return container.querySelector(".sw-count b")?.textContent ?? undefined;
}

describe("SourceSwipe deck logic — follow / skip / undo / done", () => {
  it("Follow persists the LEAD source, advances, and increments the set count", async () => {
    const ytCards = [
      makeCard({ source_id: "yt-a", source_name: "Channel A" }),
      makeCard({ source_id: "yt-b", source_name: "Channel B" }),
    ];
    await renderDeck(makeDeck({ yt: ytCards }));

    expect(leadName()).toBe("Channel A");
    expect(setCount()).toBe("0");

    await clickAction("follow");

    // Persisted the LEAD source (not the one behind it).
    expect(followSourceMock).toHaveBeenCalledTimes(1);
    expect(followSourceMock).toHaveBeenCalledWith("yt-a");
    // Advanced to the next card + bumped the count.
    expect(leadName()).toBe("Channel B");
    expect(setCount()).toBe("1");
  });

  it("Skip advances WITHOUT following", async () => {
    const ytCards = [
      makeCard({ source_id: "yt-a", source_name: "Channel A" }),
      makeCard({ source_id: "yt-b", source_name: "Channel B" }),
    ];
    await renderDeck(makeDeck({ yt: ytCards }));

    await clickAction("skip");

    expect(followSourceMock).not.toHaveBeenCalled();
    expect(leadName()).toBe("Channel B");
    expect(setCount()).toBe("0");
  });

  it("Undo reverts the position AND unfollows a prior follow", async () => {
    const ytCards = [
      makeCard({ source_id: "yt-a", source_name: "Channel A" }),
      makeCard({ source_id: "yt-b", source_name: "Channel B" }),
    ];
    await renderDeck(makeDeck({ yt: ytCards }));

    await clickAction("follow"); // follow Channel A → now on Channel B
    expect(leadName()).toBe("Channel B");

    await clickAction("undo"); // take it back

    // Reverted to Channel A AND unfollowed the source it had persisted.
    expect(leadName()).toBe("Channel A");
    expect(unfollowSourceMock).toHaveBeenCalledTimes(1);
    expect(unfollowSourceMock).toHaveBeenCalledWith("yt-a");
    expect(setCount()).toBe("0");
  });

  it("finishing the LAST set calls onDone(total) with the all-sets total", async () => {
    // Only the LAST platform (people) has a card; yt/pod/x are empty → auto-advance
    // chains straight to the people set, leaving one swipe to finish the whole deck.
    const onDone = vi.fn();
    await renderDeck(
      makeDeck({ people: [makeCard({ source_id: "p-a", source_name: "Jensen Huang", platform_kind: "personality" })] }),
      onDone,
    );

    // Drain the chained per-set auto-advance handoffs (yt→pod→x→people, 1.7s each).
    // Each advance is a state update that schedules the NEXT handoff only after a
    // re-render, so step the clock per hop (with a microtask flush between).
    for (let hop = 0; hop < 3; hop += 1) {
      await act(async () => {
        vi.advanceTimersByTime(1700);
        await Promise.resolve();
      });
    }

    // On the final (people) set: follow the one card to complete it.
    expect(leadName()).toBe("Jensen Huang");
    await clickAction("follow");

    // The final set is complete → the "See my briefing" CTA appears.
    const cta = container.querySelector<HTMLButtonElement>('[data-action="see-briefing"]');
    expect(cta).not.toBeNull();
    act(() => {
      cta?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith(1); // one source followed across all sets
  });

  it("a broken thumbnail degrades to the initials-gradient fallback", async () => {
    await renderDeck(
      makeDeck({
        yt: [makeCard({ source_id: "yt-a", source_name: "Lex Fridman", thumbnail_url: "https://broken/404.jpg" })],
      }),
    );

    const img = container.querySelector<HTMLImageElement>('[data-source-swipe-card="lead"] .logo-img');
    expect(img).not.toBeNull();

    // Fire the browser's onError on the 404'd thumbnail.
    act(() => {
      img?.dispatchEvent(new Event("error", { bubbles: false }));
    });

    // The <img> is gone and the initials tile (portraitBg) is shown instead.
    expect(container.querySelector('[data-source-swipe-card="lead"] .logo-img')).toBeNull();
    expect(container.querySelector('[data-source-swipe-card="lead"] .mono')?.textContent).toBe("LF");
  });
});
