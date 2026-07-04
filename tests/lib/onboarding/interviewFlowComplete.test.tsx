import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Flow-wiring tests for the interview → persist → sources handoff in
 * {@link OnboardingFlow} (FSR slice #4), using the same createRoot + act harness as
 * `onboardingFlowSessionSkip.test.tsx`. The chat stage is mocked to a single
 * "confirm" button that invokes its `onComplete` prop with a fixed terminal payload,
 * so the suite drives the state machine WITHOUT the real interview UI.
 *
 * Rule 9 — WHY (each fails on a real regression):
 *   - Persistence must fire EXACTLY once and ONLY on terminal confirm (no half-profiles).
 *   - Onboarding-gate invariant (2026-06-30): `user_onboarded_at` (markOnboardingComplete)
 *     must NEVER be stamped by the interview stage — only at the true flow end.
 *   - A persist FAILURE must return to the interview (retryable) and NOT clear the
 *     resumable transcript, NOT stamp the gate.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { OnboardingFlow } from "@/components/onboarding/OnboardingFlow";
import { clearInterviewSession } from "@/lib/interview/session";
import { persistInterviewInterests } from "@/lib/interviewProfile";
import { markOnboardingComplete } from "@/lib/onboardingProfile";
import { getCurrentSession } from "@/lib/supabase/auth";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/supabase/auth", () => ({
  getCurrentSession: vi.fn(),
  TEST_AUTH_MODE: false,
  TEST_AUTH_CODE: "123456",
  TEST_AUTH_CODE_LENGTH: 6,
  OTP_CODE_LENGTH: 8,
  signInWithTestPassword: vi.fn(),
  verifyEmailOtp: vi.fn(),
}));

vi.mock("@/lib/supabase/client", () => ({ getSupabaseBrowserClient: vi.fn() }));
vi.mock("@/lib/auth/routeGuard", () => ({ resolveRootGate: vi.fn(async () => "onboarding") }));
vi.mock("@/lib/sources", () => ({ getFollowedSources: vi.fn(async () => []) }));

vi.mock("@/lib/onboardingProfile", () => ({
  isSourceOnboardingComplete: vi.fn(() => false),
  markOnboardingComplete: vi.fn(),
  markSourceOnboardingComplete: vi.fn(),
}));

vi.mock("@/lib/interviewProfile", () => ({
  persistInterviewInterests: vi.fn(),
  persistMuteTerms: vi.fn().mockResolvedValue({ persisted_mute_count: 0 }),
}));

vi.mock("@/lib/interview/session", () => ({
  clearInterviewSession: vi.fn(),
}));

// The interview stage: a single button that confirms a fixed terminal payload.
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
vi.mock("@/components/onboarding/InterviewChat", () => ({
  InterviewChat: ({ onComplete }: { onComplete: (payload: unknown) => void }) => (
    <button type="button" data-testid="confirm-interview" onClick={() => onComplete(CONFIRM_PAYLOAD)}>
      confirm
    </button>
  ),
}));
vi.mock("@/components/onboarding/EmailSignIn", () => ({ EmailSignIn: () => <div data-testid="email-signin" /> }));
vi.mock("@/components/sources/SourceClusterScreen", () => ({
  SourceClusterScreen: () => <div data-testid="source-cluster" />,
}));
vi.mock("@/components/onboarding/BuildYour30", () => ({ BuildYour30: () => <div data-testid="build-your-30" /> }));

const mockGetCurrentSession = vi.mocked(getCurrentSession);
const mockPersist = vi.mocked(persistInterviewInterests);
const mockMarkOnboardingComplete = vi.mocked(markOnboardingComplete);
const mockClearSession = vi.mocked(clearInterviewSession);

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  mockGetCurrentSession.mockResolvedValue({
    user: { id: "user-1" },
  } as Awaited<ReturnType<typeof getCurrentSession>>);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** Advance a signed-in user from splash to the interview stage. */
async function reachInterview(): Promise<void> {
  act(() => root.render(<OnboardingFlow />));
  const getStarted = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((button) =>
    button.textContent?.includes("Get started"),
  );
  if (!getStarted) {
    throw new Error("Get started button not found");
  }
  await act(async () => {
    getStarted.click();
  });
}

async function clickConfirm(): Promise<void> {
  const confirm = container.querySelector<HTMLButtonElement>("[data-testid='confirm-interview']");
  if (!confirm) {
    throw new Error("interview stage did not render");
  }
  await act(async () => {
    confirm.click();
  });
}

describe("OnboardingFlow — interview persist handoff (Rule 9)", () => {
  it("persists once on confirm, advances to sources, and NEVER stamps the onboarding gate", async () => {
    mockPersist.mockResolvedValue({ minted_interest_count: 1, rejected_interests: [] });

    await reachInterview();
    // Persistence must NOT have fired merely by reaching the interview.
    expect(mockPersist).not.toHaveBeenCalled();

    await clickConfirm();

    expect(mockPersist).toHaveBeenCalledTimes(1);
    expect(mockPersist).toHaveBeenCalledWith("user-1", CONFIRM_PAYLOAD);
    expect(container.querySelector("[data-testid='source-cluster']")).not.toBeNull();
    // Onboarding-gate invariant: the interview stage must never stamp user_onboarded_at.
    expect(mockMarkOnboardingComplete).not.toHaveBeenCalled();
    // Success clears the resumable transcript.
    expect(mockClearSession).toHaveBeenCalledTimes(1);
  });

  it("on a persist failure, returns to the interview without stamping or clearing the transcript", async () => {
    mockPersist.mockRejectedValue(new Error("mint RPC down"));

    await reachInterview();
    await clickConfirm();

    // Back on the interview stage (retryable), gate not stamped, transcript kept.
    expect(container.querySelector("[data-testid='confirm-interview']")).not.toBeNull();
    expect(container.querySelector("[data-testid='source-cluster']")).toBeNull();
    expect(mockMarkOnboardingComplete).not.toHaveBeenCalled();
    expect(mockClearSession).not.toHaveBeenCalled();
  });
});
