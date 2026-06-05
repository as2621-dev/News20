"use client";

/**
 * SelectionTray — the persistent bottom tray of the recursive interest picker
 * (Phase 5 SP4). A fixed, dark (`--ink`) bar (spec §8) that reads the SHARED
 * selection store via `useSyncExternalStore` and surfaces, per spec §8/§10:
 *   - the **total follow count** (Fraunces display per §8 — system-font fallback,
 *     NOT a globals.css/tailwind edit; SP3 Concern #3),
 *   - a live **preview** of the most-recent picks,
 *   - **per-category counts** (group `store.all()` by `selection.path[0]`),
 *   - a **Review panel** grouped by category (toggle open/closed),
 *   - **Copy/Export** of the §7-shaped payload (`store.all()` IS the §7 shape).
 *
 * Styling is self-contained via inline spec §8 hex tokens (no globals.css/tailwind
 * edits — out of SP4 scope, SP3 Concern #3). `data-*` hooks back the SP4 tests.
 */

import { type CSSProperties, useState, useSyncExternalStore } from "react";
import type { FollowSelection, SelectionStore } from "@/types/picker";

/** Spec §8 design tokens (warm editorial palette), inlined to avoid global edits. */
const TOKENS = {
  bg: "#f4f1ea",
  ink: "#1b1a17",
  muted: "#6f6a5e",
  line: "#dcd6c8",
  card: "#fffdf7",
  sel: "#3a5a40",
  rust: "#9a4a1f",
} as const;
/** Display stack (Fraunces in the prototype) + system serif fallback (Concern #3). */
const DISPLAY_FONT_STACK = '"Fraunces", Georgia, "Times New Roman", serif';
/** Body stack (Spline Sans in the prototype) + system fallback. */
const BODY_FONT_STACK = '"Spline Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
/** Mono stack (Spline Sans Mono in the prototype) for eyebrow caps / counts + fallback. */
const MONO_FONT_STACK = '"Spline Sans Mono", ui-monospace, SFMono-Regular, Menlo, monospace';
/** How many recent picks the inline preview shows before "+N more". */
const PREVIEW_LIMIT = 4;

export interface SelectionTrayProps {
  /** The shared selection store (owned by {@link OnboardingPicker}). */
  store: SelectionStore;
}

/** Subscribe a component to the store snapshot (re-renders on any selection change). */
function useStoreSnapshot(store: SelectionStore): readonly FollowSelection[] {
  return useSyncExternalStore(
    (listener) => store.subscribe(listener),
    () => store.getSnapshot(),
    () => store.getSnapshot(),
  );
}

/**
 * Group selections by their category (always `path[0]` — SP3 guarantees the category
 * label is the first path segment). Returns insertion-ordered `[category, count]`.
 */
function countsByCategory(selections: readonly FollowSelection[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const selection of selections) {
    const category = selection.path[0] ?? "Other";
    counts.set(category, (counts.get(category) ?? 0) + 1);
  }
  return [...counts.entries()];
}

/** Group selections into `{ category → selections }` for the Review panel. */
function selectionsByCategory(selections: readonly FollowSelection[]): Array<[string, FollowSelection[]]> {
  const grouped = new Map<string, FollowSelection[]>();
  for (const selection of selections) {
    const category = selection.path[0] ?? "Other";
    const bucket = grouped.get(category) ?? [];
    bucket.push(selection);
    grouped.set(category, bucket);
  }
  return [...grouped.entries()];
}

/**
 * Render the fixed bottom selection tray.
 *
 * @param props - {@link SelectionTrayProps}.
 */
export function SelectionTray({ store }: SelectionTrayProps) {
  const selections = useStoreSnapshot(store);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const total = selections.length;
  const categoryCounts = countsByCategory(selections);
  // Most-recent picks first for the inline preview (the snapshot is insertion-ordered).
  const preview = [...selections].slice(-PREVIEW_LIMIT).reverse();
  const previewOverflow = total - preview.length;

  /** Copy the §7-shaped payload (the store IS the §7 shape) to the clipboard. */
  const handleCopy = async (): Promise<void> => {
    // Export the exact §7 payload fields (drop the picker-internal canonicalKey).
    const payload = selections.map((selection) => ({
      followId: selection.followId,
      label: selection.label,
      path: selection.path,
      type: selection.type,
      ...(selection.kind ? { kind: selection.kind } : {}),
      ...(selection.ticker ? { ticker: selection.ticker } : {}),
      source: selection.source,
    }));
    const json = JSON.stringify(payload, null, 2);
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(json);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    } catch {
      // Clipboard blocked (permissions/insecure context) — non-fatal; the export is
      // a convenience, not a gate. The payload still persists on Continue.
      setCopied(false);
    }
  };

  const barStyle: CSSProperties = {
    position: "fixed",
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 40,
    background: TOKENS.ink,
    color: TOKENS.bg,
    borderTop: `1px solid ${TOKENS.muted}`,
    padding: "10px 14px calc(10px + env(safe-area-inset-bottom))",
    fontFamily: BODY_FONT_STACK,
  };

  return (
    <div data-selection-tray="" style={barStyle}>
      {reviewOpen ? (
        <div
          data-tray-review=""
          style={{
            maxHeight: "40vh",
            overflowY: "auto",
            marginBottom: 10,
            paddingBottom: 6,
            borderBottom: `1px solid ${TOKENS.muted}`,
          }}
        >
          {total === 0 ? (
            <p style={{ fontFamily: MONO_FONT_STACK, fontSize: 11, color: TOKENS.line, margin: "4px 0" }}>
              NOTHING PICKED YET — TAP BUBBLES TO FOLLOW, OR SKIP.
            </p>
          ) : (
            selectionsByCategory(selections).map(([category, items]) => (
              <div key={category} data-review-category={category} style={{ margin: "6px 0" }}>
                <span
                  style={{
                    fontFamily: MONO_FONT_STACK,
                    fontSize: 10.5,
                    letterSpacing: ".06em",
                    textTransform: "uppercase",
                    color: TOKENS.line,
                  }}
                >
                  {category} · {items.length}
                </span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "4px 0" }}>
                  {items.map((selection) => (
                    <span
                      key={selection.followId}
                      style={{
                        fontFamily: BODY_FONT_STACK,
                        fontSize: 12.5,
                        padding: "3px 9px",
                        borderRadius: 999,
                        background: TOKENS.sel,
                        color: TOKENS.card,
                      }}
                    >
                      {selection.label}
                      {selection.ticker ? (
                        <span style={{ fontFamily: MONO_FONT_STACK, fontSize: 9.5, color: TOKENS.bg, marginLeft: 5 }}>
                          {selection.ticker}
                        </span>
                      ) : null}
                    </span>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      ) : null}

      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <span
          data-tray-count=""
          style={{ fontFamily: DISPLAY_FONT_STACK, fontWeight: 900, fontSize: 22, lineHeight: 1, color: TOKENS.bg }}
        >
          {total}
        </span>
        <span style={{ fontFamily: MONO_FONT_STACK, fontSize: 10, letterSpacing: ".06em", color: TOKENS.line }}>
          {total === 1 ? "FOLLOW" : "FOLLOWS"}
        </span>

        {/* Per-category counts (spec §8). */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, flex: 1, minWidth: 0 }}>
          {categoryCounts.map(([category, count]) => (
            <span
              key={category}
              data-tray-category={category}
              style={{
                fontFamily: MONO_FONT_STACK,
                fontSize: 10,
                letterSpacing: ".04em",
                textTransform: "uppercase",
                color: TOKENS.bg,
                border: `1px solid ${TOKENS.muted}`,
                borderRadius: 999,
                padding: "2px 7px",
              }}
            >
              {category} <span style={{ color: TOKENS.rust }}>{count}</span>
            </span>
          ))}
        </div>

        <button
          type="button"
          data-tray-review-toggle=""
          aria-expanded={reviewOpen}
          onClick={() => setReviewOpen((open) => !open)}
          style={miniButtonStyle}
        >
          {reviewOpen ? "Hide" : "Review"}
        </button>
        <button
          type="button"
          data-tray-export=""
          onClick={() => void handleCopy()}
          disabled={total === 0}
          style={{ ...miniButtonStyle, opacity: total === 0 ? 0.4 : 1 }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>

      {/* Live preview of recent picks (spec §8). */}
      {preview.length > 0 ? (
        <div data-tray-preview="" style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 7 }}>
          {preview.map((selection) => (
            <span
              key={selection.followId}
              style={{
                fontFamily: BODY_FONT_STACK,
                fontSize: 11.5,
                padding: "2px 8px",
                borderRadius: 999,
                background: TOKENS.muted,
                color: TOKENS.bg,
              }}
            >
              {selection.label}
            </span>
          ))}
          {previewOverflow > 0 ? (
            <span style={{ fontFamily: MONO_FONT_STACK, fontSize: 10, color: TOKENS.line, alignSelf: "center" }}>
              +{previewOverflow} MORE
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Shared mono mini-button style (Review / Copy) — ≥44px target (spec §8/§10). */
const miniButtonStyle: CSSProperties = {
  fontFamily: MONO_FONT_STACK,
  fontSize: 10.5,
  letterSpacing: ".04em",
  textTransform: "uppercase",
  minHeight: 44,
  background: "none",
  border: `1px solid ${TOKENS.muted}`,
  color: "#f4f1ea",
  borderRadius: 999,
  padding: "0 12px",
  cursor: "pointer",
};
