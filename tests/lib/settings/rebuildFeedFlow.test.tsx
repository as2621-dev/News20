import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Flow tests for the "Rebuild my feed" entry point (issue #9): Settings row →
 * {@link RebuildFeedFlow} → InterviewChat (mocked) → replace-persist → done/error.
 * Uses the same createRoot + act harness as `interviewFlowComplete.test.tsx`.
 *
 * Rule 9 — WHY (each fails on a real regression):
 *   - Launch from Settings must open the interview WITHOUT persisting anything.
 *   - Completing must persist EXACTLY once with replace semantics ({ replace_existing: true })
 *     — anything else blends old and new profiles.
 *   - The rebuild must NOT re-trigger first-run onboarding side effects (no
 *     user_onboarded_at stamp, no source-step marker).
 *   - Abandoning at any point must leave the old profile untouched (persist never fires).
 *   - A persist failure must surface a RETRY that re-persists the SAME confirmed payload,
 *     and a bail-out that keeps the old profile live.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { SettingsLayer } from "@/components/blip/reel/SettingsLayer";
import type { InterviewChatProps } from "@/components/onboarding/InterviewChat";
import { clearInterviewSession } from "@/lib/interview/session";
import { persistInterviewInterests } from "@/lib/interviewProfile";
import { markOnboardingComplete, markSourceOnboardingComplete } from "@/lib/onboardingProfile";
import { getCurrentSession } from "@/lib/supabase/auth";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/supabase/auth", () => ({
  getCurrentSession: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("@/lib/profile", () => ({
  getProfileDisplayName: vi.fn(async () => null),
  saveProfileDisplayName: vi.fn(),
  PROFILE_DISPLAY_NAME_MAX_LENGTH: 40,
}));

vi.mock("@/lib/interviewProfile", () => ({
  persistInterviewInterests: vi.fn(),
  persistMuteTerms: vi.fn().mockResolvedValue({ persisted_mute_count: 0 }),
  REPLACE_PARTIAL_ERROR_NAME: "ReplacePartialError",
}));

vi.mock("@/lib/interview/session", () => ({
  clearInterviewSession: vi.fn(),
}));

vi.mock("@/lib/onboardingProfile", () => ({
  markOnboardingComplete: vi.fn(),
  markSourceOnboardingComplete: vi.fn(),
}));

// The interview stage: records its props, offers a single confirm button.
const CONFIRM_PAYLOAD = {
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
};
const interviewChatProps: Partial<InterviewChatProps>[] = [];
vi.mock("@/components/onboarding/InterviewChat", () => ({
  InterviewChat: (props: InterviewChatProps) => {
    interviewChatProps.push(props);
    return (
      <>
        <button type="button" data-testid="confirm-interview" onClick={() => props.onComplete(CONFIRM_PAYLOAD)}>
          confirm
        </button>
        <button
          type="button"
          data-testid="confirm-empty"
          onClick={() => props.onComplete({ micro_interests: [], roots_only_fallback: true })}
        >
          confirm-empty
        </button>
      </>
    );
  },
}));

const mockGetCurrentSession = vi.mocked(getCurrentSession);
const mockPersist = vi.mocked(persistInterviewInterests);
const mockClearSession = vi.mocked(clearInterviewSession);
const mockMarkOnboardingComplete = vi.mocked(markOnboardingComplete);
const mockMarkSourceOnboardingComplete = vi.mocked(markSourceOnboardingComplete);

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  interviewChatProps.length = 0;
  mockGetCurrentSession.mockResolvedValue({
    user: { id: "user-1", email: "u@example.com" },
  } as Awaited<ReturnType<typeof getCurrentSession>>);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** Click the first button whose text contains `text`. Throws if not found. */
async function clickText(text: string): Promise<void> {
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((candidate) =>
    candidate.textContent?.includes(text),
  );
  if (!button) {
    const labels = Array.from(container.querySelectorAll("button"))
      .map((b) => b.textContent)
      .join(" | ");
    throw new Error(`Button containing "${text}" not found. Buttons: ${labels}`);
  }
  await act(async () => {
    button.click();
  });
}

/** Render Settings and open the rebuild flow from its row. */
async function openRebuildFromSettings(): Promise<void> {
  await act(async () => {
    root.render(<SettingsLayer />);
  });
  await clickText("Rebuild my feed");
}

describe("Rebuild my feed — Settings entry + replace flow (issue #9)", () => {
  it("launches the interview from Settings (fresh start), persisting NOTHING on launch", async () => {
    await openRebuildFromSettings();

    // The interview stage is up, started FRESH (a stale onboarding transcript must not resume).
    expect(container.querySelector("[data-testid='confirm-interview']")).not.toBeNull();
    expect(interviewChatProps[0]?.forceRestart).toBe(true);
    // Nothing persisted, nothing stamped, merely by launching.
    expect(mockPersist).not.toHaveBeenCalled();
    expect(mockMarkOnboardingComplete).not.toHaveBeenCalled();
  });

  it("completing the re-interview persists ONCE with replace semantics and fires NO onboarding side effects", async () => {
    mockPersist.mockResolvedValue({ minted_interest_count: 1, rejected_interests: [] });

    await openRebuildFromSettings();
    await clickText("confirm");

    expect(mockPersist).toHaveBeenCalledTimes(1);
    expect(mockPersist).toHaveBeenCalledWith("user-1", CONFIRM_PAYLOAD, { replace_existing: true });
    // Success shows the done state and clears the stale transcript.
    expect(container.textContent).toContain("Profile rebuilt");
    expect(mockClearSession).toHaveBeenCalled();
    // First-run onboarding side effects must NOT re-fire (user_onboarded_at stays set,
    // the source step is untouched).
    expect(mockMarkOnboardingComplete).not.toHaveBeenCalled();
    expect(mockMarkSourceOnboardingComplete).not.toHaveBeenCalled();

    // "Done" returns to Settings (the flow overlay unmounts).
    await clickText("Done");
    expect(container.querySelector("[data-testid='rebuild-flow']")).toBeNull();
  });

  it("abandoning mid-interview closes the flow with the old profile untouched (no persist call)", async () => {
    await openRebuildFromSettings();
    await clickText("Cancel");

    expect(container.querySelector("[data-testid='rebuild-flow']")).toBeNull();
    expect(mockPersist).not.toHaveBeenCalled();
    // Back on Settings, the row is still there for a later re-run.
    expect(container.textContent).toContain("Rebuild my feed");
  });

  it("a persist failure surfaces a retry that re-persists the SAME payload; bailing out keeps the old profile", async () => {
    mockPersist.mockRejectedValueOnce(new Error("mint RPC down")).mockResolvedValueOnce({
      minted_interest_count: 1,
      rejected_interests: [],
    });

    await openRebuildFromSettings();
    await clickText("confirm");

    // Failure state: the error surfaces with a retry; the old profile is still live
    // (nothing was cleared, the flow did not pretend success).
    expect(container.textContent).toContain("Retry");
    expect(mockClearSession).not.toHaveBeenCalled();

    await clickText("Retry");
    expect(mockPersist).toHaveBeenCalledTimes(2);
    expect(mockPersist).toHaveBeenLastCalledWith("user-1", CONFIRM_PAYLOAD, { replace_existing: true });
    expect(container.textContent).toContain("Profile rebuilt");
  });

  it("bailing out of a failed persist closes the flow without another write (old feed keeps working)", async () => {
    mockPersist.mockRejectedValue(new Error("mint RPC down"));

    await openRebuildFromSettings();
    await clickText("confirm");
    await clickText("Keep my current feed");

    expect(container.querySelector("[data-testid='rebuild-flow']")).toBeNull();
    expect(mockPersist).toHaveBeenCalledTimes(1);
  });

  it("confirming an EMPTY list keeps the old profile — nothing persisted, honest 'no changes' state", async () => {
    // WHY (review-panel HIGH): a skip-through rebuild must never wipe a working profile.
    // An empty confirm is treated as keep-old: no persist call, and the done screen says
    // the feed stays as is (not "Profile rebuilt").
    await openRebuildFromSettings();
    await clickText("confirm-empty");

    expect(mockPersist).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Your feed stays as is");
    expect(container.textContent).not.toContain("Profile rebuilt");

    await clickText("Done");
    expect(container.querySelector("[data-testid='rebuild-flow']")).toBeNull();
  });

  it("a PARTIAL failure (new rows landed, cleanup pending) tells the truth instead of claiming 'untouched'", async () => {
    // WHY (review-panel HIGH): after the new rows are upserted, a stale-delete/traits
    // failure leaves an old∪new superset — the copy must say "retry to finish the
    // cleanup", never "your current feed is untouched" (which would sanction a blend).
    const partialError = new Error("delete step failed");
    partialError.name = "ReplacePartialError";
    mockPersist.mockRejectedValue(partialError);

    await openRebuildFromSettings();
    await clickText("confirm");

    expect(container.textContent).toContain("retry to finish the cleanup");
    expect(container.textContent).not.toContain("Your current feed is untouched");
    expect(container.textContent).not.toContain("Keep my current feed");
  });

  it("a rejected-picks result is surfaced on the done screen, not just logged (Rule 12)", async () => {
    // WHY (review-panel MED): backstop-rejected picks silently shrink the replaced
    // profile; the done screen must say so (mirrors OnboardingFlow's unpersisted note).
    mockPersist.mockResolvedValue({
      minted_interest_count: 1,
      rejected_interests: [{ canonical_slug: "sport.bad", reason: "under_two_anchor_terms" }],
    });

    await openRebuildFromSettings();
    await clickText("confirm");

    expect(container.textContent).toContain("Profile rebuilt");
    expect(container.textContent).toContain("We couldn't keep 1 of your picks");
  });
});
