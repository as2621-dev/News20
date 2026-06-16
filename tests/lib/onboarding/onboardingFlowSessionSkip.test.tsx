import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Component tests for the OnboardingFlow splash session-skip (go-live pre-phase).
 *
 * Rendering uses React 19's `react-dom/client` `createRoot` + `react`'s `act`
 * directly (no @testing-library — not a project dependency), mirroring
 * `tests/lib/onboarding/onboardingPicker.test.tsx`. The Supabase auth boundary and
 * the heavy child steps (TopicTree, EmailSignIn, SourceSwipe, BuildYour30) are
 * MOCKED (CLAUDE.md mocking rule) — this suite exercises ONLY the splash branch.
 *
 * Rule 9 — WHY this behavior matters, each test failing on a real regression:
 *   - A signed-in user (restored session, re-onboarding, or an injected test
 *     session) must NEVER be asked to sign in again: "Get started" must route
 *     straight to the picker. Regressing to the email step FAILS this — and would
 *     also force magic-link emails (rate-limited) for already-authed users.
 *   - A signed-out user must still land on the email step — skipping auth for an
 *     anonymous user would let un-scoped follows reach the persist path (Rule 12).
 */

// Tell React this is an act() environment so state updates flush synchronously.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { OnboardingFlow } from "@/components/onboarding/OnboardingFlow";
import { getCurrentSession } from "@/lib/supabase/auth";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/supabase/auth", () => ({
  getCurrentSession: vi.fn(),
  // Reason: OtpCodeEntry (rendered inside OnboardingFlow) reads these at module
  // load — the mock must export them or import fails. Test mode stays OFF here.
  TEST_AUTH_MODE: false,
  TEST_AUTH_CODE: "123456",
  TEST_AUTH_CODE_LENGTH: 6,
  OTP_CODE_LENGTH: 8,
  signInWithTestPassword: vi.fn(),
  verifyEmailOtp: vi.fn(),
}));

vi.mock("@/lib/supabase/client", () => ({
  getSupabaseBrowserClient: vi.fn(),
}));

vi.mock("@/lib/onboardingProfile", () => ({
  isSourceOnboardingComplete: vi.fn(() => false),
  markSourceOnboardingComplete: vi.fn(),
  persistPickerFollows: vi.fn(),
}));

// Stub the step components: this suite tests the state machine's splash branch,
// not the steps themselves (each has its own suite).
vi.mock("@/components/onboarding/TopicTree", () => ({
  TopicTree: () => <div data-testid="topic-tree" />,
}));
vi.mock("@/components/onboarding/EmailSignIn", () => ({
  EmailSignIn: () => <div data-testid="email-signin" />,
}));
vi.mock("@/components/sources/SourceSwipe", () => ({
  SourceSwipe: () => <div data-testid="source-swipe" />,
}));
vi.mock("@/components/onboarding/BuildYour30", () => ({
  BuildYour30: () => <div data-testid="build-your-30" />,
}));

const mockGetCurrentSession = vi.mocked(getCurrentSession);

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  mockGetCurrentSession.mockReset();
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

/** Render the flow and flush effects. */
function renderFlow(): void {
  act(() => {
    root.render(<OnboardingFlow />);
  });
}

/** Click the splash "Get started" CTA and flush the async session check. */
async function clickGetStarted(): Promise<void> {
  const buttons = Array.from(container.querySelectorAll<HTMLButtonElement>("button"));
  const getStartedButton = buttons.find((button) => button.textContent?.includes("Get started"));
  if (!getStartedButton) {
    throw new Error("Get started button not found on splash");
  }
  await act(async () => {
    getStartedButton.click();
  });
}

describe("OnboardingFlow — splash session-skip (Rule 9)", () => {
  it("routes a signed-in user straight to the picker, never the email step", async () => {
    // WHY: a signed-in user must never re-auth; regressing to the email step would
    // demand a rate-limited magic-link email from an already-authed user.
    mockGetCurrentSession.mockResolvedValue({
      user: { id: "user-already-signed-in" },
    } as Awaited<ReturnType<typeof getCurrentSession>>);

    renderFlow();
    await clickGetStarted();

    expect(container.querySelector("[data-testid='topic-tree']")).not.toBeNull();
    expect(container.querySelector("[data-testid='email-signin']")).toBeNull();
  });

  it("routes a signed-out user to the email step (auth is not skippable)", async () => {
    // WHY: skipping auth for an anonymous user would let un-scoped follows reach
    // the persist path (Rule 12) — the email step is the only entry to a session.
    mockGetCurrentSession.mockResolvedValue(null);

    renderFlow();
    await clickGetStarted();

    expect(container.querySelector("[data-testid='email-signin']")).not.toBeNull();
    expect(container.querySelector("[data-testid='topic-tree']")).toBeNull();
  });
});
