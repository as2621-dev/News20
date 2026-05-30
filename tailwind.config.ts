import type { Config } from "tailwindcss";

/**
 * Design tokens for blip — mirrors the prototype `index.html` inline
 * `tailwind.config` and `reference/design-language.md` VERBATIM so the
 * prototype's class names port unchanged (`bg-background`,
 * `text-caption-highlight`, `font-serif`, `rounded-card`, `seg-geopolitics`,
 * `bias-right`, `pt-safe-t`, …).
 *
 * Exported standalone so the Vitest token test can assert key presence/values.
 */
export const tokens = {
  colors: {
    primary: "#3B82F6", // actions, active states, "follow"
    secondary: "#D1D4BD", // muted sage surface
    accent: "#E8B7BC", // soft blush highlight
    background: "#020617", // near-black canvas — app base
    surface: "#D1D4BD", // light detail-view cards (sparing)
    "text-primary": "#FFFFFF",
    "text-secondary": "#A1A1AA",
    border: "#D1D4BD",
    "caption-highlight": "#FACC15", // the ONE yellow keyword/sentence
    "bias-left": "#3B82F6",
    "bias-center": "#A1A1AA",
    "bias-right": "#E8B7BC",
    "seg-geopolitics": "#EF4444",
    "seg-markets": "#22C55E",
    "seg-tech": "#22D3EE",
    "seg-sport": "#F59E0B",
    "seg-wildcard": "#E8B7BC",
  },
  fontFamily: {
    sans: ["Inter", "system-ui", "sans-serif"],
    serif: ['"Playfair Display"', "Georgia", "serif"],
    mono: ['"JetBrains Mono"', "monospace"],
  },
  borderRadius: { card: "1px", control: "16px", pill: "9999px" },
  // Reason: prototype hard-codes Dynamic Island (59px) + home indicator (34px)
  // insets. The real shell prefers env(safe-area-inset-*) with these as the
  // fallback (port-map §6); these tokens stay as the named spacing scale.
  spacing: { "safe-t": "59px", "safe-b": "34px" },
} as const;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: tokens.colors,
      fontFamily: tokens.fontFamily,
      borderRadius: tokens.borderRadius,
      spacing: tokens.spacing,
    },
  },
};

export default config;
