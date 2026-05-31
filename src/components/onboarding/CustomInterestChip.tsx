"use client";

/**
 * CustomInterestChip — the free-text "custom interest" chip (Phase 1e SP3).
 *
 * A small inline input that lets the user name an interest the taxonomy doesn't
 * cover ("just give me Formula 1 stuff"). On submit it does NOT write to the DB —
 * SP3 is selection-only. It calls {@link CustomInterestChipProps.onAddCustom}
 * with the trimmed label so `InterestChips` can hold it as a **pending custom
 * selection** (`interest_kind: "custom"`), which SP4 persists / canonicalizes
 * (phase Open Q2: flat custom node for v1).
 *
 * Visual register matches the reel/onboarding surface (`EmailSignIn`,
 * `TapToStart`): near-black canvas, soft pill controls, Inter chrome.
 */

import { type FormEvent, useState } from "react";

export interface CustomInterestChipProps {
  /**
   * Fired on submit with the trimmed, non-empty label. The parent turns this
   * into a pending custom selection — no DB write happens in SP3.
   */
  onAddCustom: (label: string) => void;
}

/**
 * Render the free-text custom-interest input chip.
 *
 * @param props - {@link CustomInterestChipProps}.
 */
export function CustomInterestChip({ onAddCustom }: CustomInterestChipProps) {
  const [labelInput, setLabelInput] = useState("");

  const trimmedLabel = labelInput.trim();
  const canSubmit = trimmedLabel !== "";

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Reason: never emit a blank/whitespace custom interest — a dangling empty
    // selection would orphan a row in SP4 (Rule 12, phase DoD "never orphaned").
    if (!canSubmit) {
      return;
    }
    onAddCustom(trimmedLabel);
    setLabelInput("");
  }

  return (
    <form onSubmit={handleSubmit} className="flex w-full items-center gap-2">
      <input
        type="text"
        name="custom-interest"
        autoComplete="off"
        placeholder="Add your own…"
        aria-label="Add a custom interest"
        value={labelInput}
        onChange={(event) => setLabelInput(event.target.value)}
        className="min-w-0 flex-1 rounded-control border border-white/15 bg-white/5 px-4 py-2.5 font-sans text-[14px] text-text-primary placeholder:text-white/35 focus:border-white/40 focus:outline-none"
      />
      <button
        type="submit"
        disabled={!canSubmit}
        className="shrink-0 rounded-pill bg-white px-4 py-2.5 font-sans text-[13px] font-semibold text-background transition-opacity disabled:opacity-40"
      >
        Add
      </button>
    </form>
  );
}
