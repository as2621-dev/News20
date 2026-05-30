import { describe, expect, it } from "vitest";
import config, { tokens } from "../../tailwind.config";

/**
 * Token-config contract test (phase-1 SP1 DoD).
 *
 * Per Rule 9 these assert the *values*, not merely that the keys compile — if a
 * token is renamed, dropped, or its hex/value drifts from the design-language
 * source of truth, the matching assertion FAILS. The whole reel UI ports the
 * prototype's class names (`bg-background`, `text-caption-highlight`,
 * `font-serif`, `rounded-card`, `seg-geopolitics`, `bias-right`), so a silent
 * token rename would break the visual contract; this test is the tripwire.
 */
describe("tailwind token config", () => {
  it("exposes the segment colour-coding tokens with exact hex values", () => {
    expect(tokens.colors["seg-geopolitics"]).toBe("#EF4444");
    expect(tokens.colors["seg-markets"]).toBe("#22C55E");
    expect(tokens.colors["seg-tech"]).toBe("#22D3EE");
    expect(tokens.colors["seg-sport"]).toBe("#F59E0B");
    expect(tokens.colors["seg-wildcard"]).toBe("#E8B7BC");
  });

  it("exposes the bias coverage tokens with exact hex values", () => {
    expect(tokens.colors["bias-left"]).toBe("#3B82F6");
    expect(tokens.colors["bias-center"]).toBe("#A1A1AA");
    expect(tokens.colors["bias-right"]).toBe("#E8B7BC");
  });

  it("reserves caption-highlight as the one yellow keyword colour (#FACC15)", () => {
    expect(tokens.colors["caption-highlight"]).toBe("#FACC15");
  });

  it("exposes the core surface + text tokens", () => {
    expect(tokens.colors.primary).toBe("#3B82F6");
    expect(tokens.colors.secondary).toBe("#D1D4BD");
    expect(tokens.colors.accent).toBe("#E8B7BC");
    expect(tokens.colors.background).toBe("#020617");
    expect(tokens.colors.surface).toBe("#D1D4BD");
    expect(tokens.colors["text-primary"]).toBe("#FFFFFF");
    expect(tokens.colors["text-secondary"]).toBe("#A1A1AA");
    expect(tokens.colors.border).toBe("#D1D4BD");
  });

  it("resolves font-serif to Playfair Display (the reel hero + Detail body face)", () => {
    expect(tokens.fontFamily.serif).toContain('"Playfair Display"');
    expect(tokens.fontFamily.sans).toContain("Inter");
    expect(tokens.fontFamily.mono).toContain('"JetBrains Mono"');
  });

  it("keeps the editorial radius scale (card 1px / control 16px / pill)", () => {
    expect(tokens.borderRadius.card).toBe("1px");
    expect(tokens.borderRadius.control).toBe("16px");
    expect(tokens.borderRadius.pill).toBe("9999px");
  });

  it("exposes the safe-area spacing tokens (Dynamic Island 59px / home 34px)", () => {
    expect(tokens.spacing["safe-t"]).toBe("59px");
    expect(tokens.spacing["safe-b"]).toBe("34px");
  });

  it("wires the tokens into the exported Tailwind theme.extend so utilities resolve", () => {
    const extend = config.theme?.extend;
    expect(extend?.colors).toBe(tokens.colors);
    expect(extend?.fontFamily).toBe(tokens.fontFamily);
    expect(extend?.borderRadius).toBe(tokens.borderRadius);
    expect(extend?.spacing).toBe(tokens.spacing);
  });
});
