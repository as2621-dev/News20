import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptLine } from "@/components/voice/TranscriptLine";
import { orbAnimationClass, VoiceOrb } from "@/components/voice/VoiceOrb";
import { WAVEFORM_BAR_COUNT, Waveform, waveformBarHeights } from "@/components/voice/Waveform";

/**
 * Component + unit tests for the Phase 3 SP4 shared voice UI (VoiceOrb + Waveform
 * + TranscriptLine).
 *
 * Rule 9 — these encode WHY the behaviour matters, not just WHAT renders:
 *   - The orb's state→class map IS the contract phase-3b styles against
 *     (`globals.css` `.orb.listening` / `.orb.responding`). A render of each state
 *     must emit exactly the prototype contract classes; a wrong/renamed class would
 *     silently un-style the orb in 3b, so the tests pin the class strings.
 *   - The mic is folded INTO the orb (no separate mic button): tapping the orb is
 *     the ONLY pause/resume control, so a test asserts the tap fires the callback —
 *     if the tap stopped firing, the user could never pause a live conversation.
 *   - `prefers-reduced-motion` is an accessibility guarantee: the orb must emit ZERO
 *     animation classes under it (the prototype kills the keyframes via media
 *     query; here it must be assertable in JS). A test asserts the animating
 *     `listening` / `responding` classes are ABSENT under reduced motion, so a
 *     regression that animated anyway FAILS.
 *   - The waveform must REACT to amplitude (its whole purpose): louder signal →
 *     taller bars; silence/inactive/reduced-motion → flat resting bars. Tests assert
 *     the height math + that inactive/reduced renders flat.
 *
 * Rendering uses React 19's `react-dom/client` + `react`'s `act` directly (no
 * @testing-library — not a project dependency; matches `tests/lib/detail/*`). No
 * Gemini / WebSocket / audio is touched — every component is pure-prop.
 */

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

/** Render any element into the test container and flush effects. */
function render(node: React.ReactElement): void {
  act(() => {
    root.render(node);
  });
}

/** Read the orb button's class list (the prototype contract under test). */
function readOrbClassName(): string {
  const orb = container.querySelector<HTMLButtonElement>("[data-voice-orb]");
  if (orb === null) {
    throw new Error("orb button not rendered");
  }
  return orb.className;
}

describe("orbAnimationClass — the state→animation map (Rule 9)", () => {
  it("maps listening/responding to their animation class only when motion is allowed", () => {
    // WHY: this map is the prototype contract phase-3b styles against.
    expect(orbAnimationClass("listening", false)).toBe("listening");
    expect(orbAnimationClass("responding", false)).toBe("responding");
  });

  it("returns no animation class for the still states (idle / paused)", () => {
    // WHY: animating = live, still = paused. idle/paused must NEVER animate.
    expect(orbAnimationClass("idle", false)).toBe("");
    expect(orbAnimationClass("paused", false)).toBe("");
  });

  it("suppresses ALL animation classes under reduced motion (accessibility, Rule 9)", () => {
    // WHY: reduced motion must emit zero animation classes — even for the
    // otherwise-animating states.
    expect(orbAnimationClass("listening", true)).toBe("");
    expect(orbAnimationClass("responding", true)).toBe("");
  });
});

describe("VoiceOrb — each state matches the prototype class contract (DoD)", () => {
  it("renders the bare `orb` class when idle (no animation)", () => {
    render(<VoiceOrb orb_state="idle" />);
    const className = readOrbClassName();
    expect(className).toContain("orb");
    expect(className).not.toContain("listening");
    expect(className).not.toContain("responding");
  });

  it("renders `orb listening` when listening (pulse-ring contract)", () => {
    render(<VoiceOrb orb_state="listening" />);
    const className = readOrbClassName();
    expect(className).toContain("orb");
    expect(className).toContain("listening");
    expect(className).not.toContain("responding");
    // The pulse-ring element (prototype `.orb-ring`) must be present.
    expect(container.querySelector("[data-orb-ring]")?.className).toContain("orb-ring");
  });

  it("renders `orb responding` when responding (throb contract)", () => {
    render(<VoiceOrb orb_state="responding" />);
    const className = readOrbClassName();
    expect(className).toContain("orb");
    expect(className).toContain("responding");
    expect(className).not.toContain("listening");
  });

  it("renders the bare `orb` class when paused (still — no animation)", () => {
    render(<VoiceOrb orb_state="paused" />);
    const className = readOrbClassName();
    expect(className).toContain("orb");
    expect(className).not.toContain("listening");
    expect(className).not.toContain("responding");
  });

  it("adds the `orb-brand` class for the brand (white) variant", () => {
    render(<VoiceOrb orb_state="listening" orb_variant="brand" />);
    expect(readOrbClassName()).toContain("orb-brand");
  });
});

describe("VoiceOrb — mic-folded-into-orb tap toggles pause/resume (DoD)", () => {
  it("fires onPauseToggle when the orb is tapped (the only pause control)", () => {
    // WHY: there is NO separate mic button — tapping the orb IS pause/resume.
    // If this stopped firing, a live conversation could never be paused.
    const onPauseToggle = vi.fn();
    render(<VoiceOrb orb_state="listening" onPauseToggle={onPauseToggle} />);
    const orb = container.querySelector<HTMLButtonElement>("[data-voice-orb]");
    act(() => {
      orb?.click();
    });
    expect(onPauseToggle).toHaveBeenCalledTimes(1);
  });

  it("exposes the orb as an accessible button with a pause/resume label", () => {
    // WHY: the orb is the tap target — it must be a real button (keyboard + SR
    // reachable), not a bare div, and announce its action.
    render(<VoiceOrb orb_state="listening" />);
    const orb = container.querySelector<HTMLButtonElement>("[data-voice-orb]");
    expect(orb?.tagName).toBe("BUTTON");
    expect(orb?.getAttribute("aria-label")).toContain("Pause");
  });

  it("announces resume when paused", () => {
    render(<VoiceOrb orb_state="paused" />);
    const orb = container.querySelector<HTMLButtonElement>("[data-voice-orb]");
    expect(orb?.getAttribute("aria-label")).toContain("Resume");
  });
});

describe("VoiceOrb — reduced motion emits NO animation classes (DoD)", () => {
  it("does not emit `listening` while listening under reduced motion", () => {
    render(<VoiceOrb orb_state="listening" prefers_reduced_motion />);
    const className = readOrbClassName();
    // WHY: the accessibility guarantee — a reduced-motion render must not pulse.
    expect(className).not.toContain("listening");
    expect(className).not.toContain("responding");
    // The static base class is still present (the orb still renders).
    expect(className).toContain("orb");
  });

  it("does not emit `responding` while responding under reduced motion", () => {
    render(<VoiceOrb orb_state="responding" prefers_reduced_motion />);
    const className = readOrbClassName();
    expect(className).not.toContain("responding");
    expect(className).not.toContain("listening");
  });
});

describe("waveformBarHeights — bars react to amplitude (Rule 9)", () => {
  it("renders a flat resting row at amplitude 0 (silence)", () => {
    // WHY: silence must read as a flat low row, not a fanned signal.
    const heights = waveformBarHeights(0);
    expect(heights).toHaveLength(WAVEFORM_BAR_COUNT);
    expect(new Set(heights).size).toBe(1); // all equal (flat)
  });

  it("fans the bars up with the centre tallest at full amplitude (happy path)", () => {
    // WHY: a loud signal must visibly react, symmetric with the centre tallest.
    const heights = waveformBarHeights(1);
    const centre = heights[2];
    expect(centre).toBeGreaterThan(heights[0]);
    expect(centre).toBeGreaterThan(heights[4]);
    expect(heights[0]).toBeCloseTo(heights[4], 6); // symmetric edges
    // Louder than silence everywhere.
    expect(Math.min(...heights)).toBeGreaterThan(Math.min(...waveformBarHeights(0)));
  });

  it("clamps out-of-range amplitude so heights never run away or invert (edge/failure)", () => {
    // WHY: a noisy mic feed can spike >1 or report a tiny negative; clamping keeps
    // heights bounded — an unclamped impl would produce a taller-than-max or
    // negative bar here.
    const tooLoud = waveformBarHeights(5);
    const negative = waveformBarHeights(-3);
    expect(tooLoud).toEqual(waveformBarHeights(1));
    expect(negative).toEqual(waveformBarHeights(0));
    expect(Math.min(...negative)).toBeGreaterThan(0);
  });
});

describe("Waveform — renders five reactive bars, flat when inactive/reduced (Rule 9)", () => {
  /** Read the five `data-wave-bar` heights (inline style) in render order. */
  function readWaveBarHeights(): string[] {
    return Array.from(container.querySelectorAll<HTMLElement>("[data-wave-bar]")).map((bar) => bar.style.height);
  }

  it("renders exactly five bars (prototype contract)", () => {
    render(<Waveform amplitude_level={0.8} is_active />);
    expect(container.querySelectorAll("[data-wave-bar]")).toHaveLength(WAVEFORM_BAR_COUNT);
  });

  it("reacts to amplitude when actively listening", () => {
    render(<Waveform amplitude_level={1} is_active />);
    const heights = readWaveBarHeights().map((h) => Number.parseFloat(h));
    // Centre bar taller than edges → the row reacted to the signal.
    expect(heights[2]).toBeGreaterThan(heights[0]);
  });

  it("renders flat resting bars when NOT active, ignoring amplitude", () => {
    // WHY: a paused/responding orb must not show a live (fanned) waveform.
    render(<Waveform amplitude_level={1} is_active={false} />);
    const heights = readWaveBarHeights();
    expect(new Set(heights).size).toBe(1); // all equal → flat
  });

  it("renders flat resting bars under reduced motion even while active", () => {
    render(<Waveform amplitude_level={1} is_active prefers_reduced_motion />);
    const heights = readWaveBarHeights();
    expect(new Set(heights).size).toBe(1); // flat
  });
});

describe("TranscriptLine — input/output role contract (Rule 9)", () => {
  it("renders a full-white line for input (user speech)", () => {
    render(<TranscriptLine transcript_role="input" transcript_text="What led to this?" />);
    const line = container.querySelector<HTMLElement>('[data-transcript-line="input"]');
    expect(line?.textContent).toBe("What led to this?");
    // WHY: input (the user's question) is full-white in the prototype contract.
    expect(line?.className).toContain("text-white");
    expect(line?.className).not.toContain("text-white/85");
  });

  it("renders a dimmer (white/85) line for output (the grounded answer)", () => {
    render(<TranscriptLine transcript_role="output" transcript_text="Three outlets report it began Tuesday." />);
    const line = container.querySelector<HTMLElement>('[data-transcript-line="output"]');
    expect(line?.textContent).toContain("Three outlets report");
    expect(line?.className).toContain("text-white/85");
  });

  it("renders nothing for an empty transcript (no empty shell)", () => {
    // WHY: a not-yet-spoken turn must render blank, not a stray empty paragraph.
    render(<TranscriptLine transcript_role="output" transcript_text="" />);
    expect(container.querySelector("[data-transcript-line]")).toBeNull();
    expect(container.querySelector("p")).toBeNull();
  });

  it("marks a streaming line aria-busy for assistive tech", () => {
    render(<TranscriptLine transcript_role="output" transcript_text="Partial…" is_streaming />);
    const line = container.querySelector<HTMLElement>("[data-transcript-line]");
    expect(line?.getAttribute("aria-busy")).toBe("true");
  });
});
