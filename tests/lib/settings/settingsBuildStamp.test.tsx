import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Render test for the Settings footer build stamp (issue #33).
 *
 * Rule 9 — WHY: the stamp is how the founder verifies ON DEVICE that the
 * installed bundle is current (2026-07-05 stale-bundle incident). If the
 * footer stops rendering the stamp next to the version, the build identity
 * is invisible again and the regression is silent.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { SettingsLayer } from "@/components/blip/reel/SettingsLayer";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/supabase/auth", () => ({
  getCurrentSession: vi.fn(async () => null),
  signOut: vi.fn(),
}));

vi.mock("@/lib/profile", () => ({
  getProfileDisplayName: vi.fn(async () => null),
  saveProfileDisplayName: vi.fn(),
  PROFILE_DISPLAY_NAME_MAX_LENGTH: 40,
}));

vi.mock("@/components/blip/library/RebuildFeedFlow", () => ({
  RebuildFeedFlow: () => null,
}));

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
});

describe("Settings footer build stamp", () => {
  it("renders the version footer with the build stamp ('dev' when no id is baked)", async () => {
    await act(async () => {
      root.render(<SettingsLayer />);
    });
    const footer = container.querySelector(".set-ver");
    expect(footer).not.toBeNull();
    // Test env has no NEXT_PUBLIC_BUILD_ID baked → the dev fallback must show.
    expect(footer?.textContent).toContain("dev");
    expect(footer?.textContent).toContain("blip 0.1.0");
  });
});
