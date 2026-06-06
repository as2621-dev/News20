"use client";

/**
 * ProfileCurtain — the "Building your profile" interstitial that opens the source
 * swipe (Phase 5c SP-UI).
 *
 * Ported from the Claude Design "blip" handoff — Source Swipe (`blip-sources.js`
 * curtain / `blip-flow.css` `.pf-curtain`). It runs a ~6.4s scripted reveal: the
 * orb pulses, the user's interest picks fade in as pills, the rolling head copy
 * advances ("Matching… → Curating… → Your profile's ready"), and four platform
 * rows (YouTube / Podcasts / X / People) tick a per-platform card count up with a
 * green check. A progress bar fills; a "Skip →" jumps straight to the deck. When
 * the script finishes (or the user skips), {@link ProfileCurtainProps.onReveal}
 * fires once and the parent dissolves the curtain into the deck.
 *
 * The counts shown per row are the REAL recommended-card counts for that platform
 * (so the curtain doesn't promise sources the deck can't deliver) — derived from
 * the loaded deck, not hardcoded as in the prototype.
 */

import { useEffect, useRef, useState } from "react";
import { SignalOrb } from "@/components/sources/SignalOrb";
import type { SourceSwipePlatformKey } from "@/lib/sourceSwipeData";

export interface ProfileCurtainProps {
  /** The user's interest picks (top categories) shown as pills. Empty → no pills. */
  picks: string[];
  /** The real recommended-card count per platform, shown ticking up in each row. */
  countsByPlatform: Record<SourceSwipePlatformKey, number>;
  /** Fired exactly once when the scripted reveal finishes OR the user taps Skip. */
  onReveal: () => void;
}

/** The four rows of the curtain, in platform order (label is curtain-specific copy). */
const CURTAIN_ROWS: ReadonlyArray<{ key: SourceSwipePlatformKey; glyph: string; label: string }> = [
  { key: "yt", glyph: "g-yt", label: "YouTube channels" },
  { key: "pod", glyph: "g-pod", label: "Podcasts" },
  { key: "x", glyph: "g-x", label: "X accounts" },
  { key: "people", glyph: "g-people", label: "People to follow" },
];

/** Scripted reveal timeline (ms) — ported from the prototype's `runCurtain` `T(...)` calls. */
const SCRIPT = {
  pill0: 350,
  pill1: 820,
  head1: 1500,
  rowYt: 2050,
  rowPod: 2850,
  head2: 3050,
  rowX: 3650,
  rowPeople: 4450,
  headDone: 5550,
  reveal: 6450,
  /** Per-row count-up animation duration. */
  countDuration: 650,
  /** Total progress-bar fill duration. */
  barDuration: 6400,
} as const;

/** The rolling head copy, advanced on a schedule. */
const HEAD_COPY = {
  initial: "Reading your picks…",
  matching: "Matching 12,000 sources…",
  curating: "Curating your sources…",
  ready: "Your profile's ready",
} as const;

/** Animate a count from 0 → `to` over `durationMs`, calling `onTick` with each integer value. */
function animateCount(to: number, durationMs: number, onTick: (value: number) => void): () => void {
  const start = performance.now();
  let frame = 0;
  const tick = (now: number) => {
    const progress = Math.min((now - start) / durationMs, 1);
    onTick(Math.round(progress * to));
    if (progress < 1) {
      frame = requestAnimationFrame(tick);
    }
  };
  frame = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(frame);
}

/**
 * Render the building-your-profile curtain.
 *
 * @param props - {@link ProfileCurtainProps}.
 */
export function ProfileCurtain({ picks, countsByPlatform, onReveal }: ProfileCurtainProps) {
  const [head, setHead] = useState<string>(HEAD_COPY.initial);
  const [shownPills, setShownPills] = useState(0);
  const [activeRows, setActiveRows] = useState<Set<SourceSwipePlatformKey>>(new Set());
  const [okRows, setOkRows] = useState<Set<SourceSwipePlatformKey>>(new Set());
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [responding, setResponding] = useState(true);
  const [barFilled, setBarFilled] = useState(false);
  const hasRevealedRef = useRef(false);

  // Reveal exactly once (script-complete OR skip), guarded so a late timer can't double-fire.
  const reveal = (): void => {
    if (hasRevealedRef.current) {
      return;
    }
    hasRevealedRef.current = true;
    onReveal();
  };

  // Reason: the scripted reveal timeline runs ONCE on mount — countsByPlatform is
  // captured at mount (the deck is loaded before the curtain renders) and `reveal` is
  // ref-guarded against a double-fire, so re-running on either dep would restart it.
  // biome-ignore lint/correctness/useExhaustiveDependencies: run-once-on-mount timeline (see above).
  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    const cancellers: Array<() => void> = [];
    const at = (ms: number, fn: () => void) => timers.push(setTimeout(fn, ms));

    // Start the progress bar on the next frame (so the CSS transition animates).
    const barFrame = requestAnimationFrame(() => setBarFilled(true));

    const tickRow = (key: SourceSwipePlatformKey) => {
      setActiveRows((prev) => new Set(prev).add(key));
      cancellers.push(
        animateCount(countsByPlatform[key] ?? 0, SCRIPT.countDuration, (value) =>
          setCounts((prev) => ({ ...prev, [key]: value })),
        ),
      );
      at(SCRIPT.countDuration + 140, () => setOkRows((prev) => new Set(prev).add(key)));
    };

    at(SCRIPT.pill0, () => setShownPills((n) => Math.max(n, 1)));
    at(SCRIPT.pill1, () => setShownPills((n) => Math.max(n, 2)));
    at(SCRIPT.head1, () => setHead(HEAD_COPY.matching));
    at(SCRIPT.rowYt, () => tickRow("yt"));
    at(SCRIPT.rowPod, () => tickRow("pod"));
    at(SCRIPT.head2, () => setHead(HEAD_COPY.curating));
    at(SCRIPT.rowX, () => tickRow("x"));
    at(SCRIPT.rowPeople, () => tickRow("people"));
    at(SCRIPT.headDone, () => {
      setHead(HEAD_COPY.ready);
      setResponding(false);
    });
    at(SCRIPT.reveal, reveal);

    return () => {
      timers.forEach((timer) => {
        clearTimeout(timer);
      });
      cancellers.forEach((cancel) => {
        cancel();
      });
      cancelAnimationFrame(barFrame);
    };
  }, []);

  return (
    <div className="pf-curtain" data-source-curtain="">
      <div className="pf-inner">
        <div className="pf-kicker">Building your profile</div>
        <SignalOrb size={100} responding={responding} />
        <h1 className="pf-head">{head}</h1>
        {picks.length > 0 ? (
          <div className="pf-picks">
            {picks.map((pick, index) => (
              <div key={pick} className={`pf-pill${index < shownPills ? " pf-show" : ""}`}>
                <span className="pf-leaf">{pick}</span>
              </div>
            ))}
          </div>
        ) : null}
        <div className="pf-rows">
          {CURTAIN_ROWS.map((row) => (
            <div
              key={row.key}
              className={`pf-row${activeRows.has(row.key) ? " pf-on" : ""}${okRows.has(row.key) ? " pf-ok" : ""}`}
            >
              <svg className="pf-ico" width={18} height={18} aria-hidden="true">
                <use href={`#${row.glyph}`} />
              </svg>
              <span className="pf-label">{row.label}</span>
              <span className="pf-count">
                <span className="pf-num">{counts[row.key] ?? 0}</span>
              </span>
              <svg className="pf-check" width={17} height={17} aria-hidden="true">
                <use href="#i-check" />
              </svg>
            </div>
          ))}
        </div>
      </div>
      <div className="pf-bar">
        <i
          style={{
            width: barFilled ? "100%" : "0%",
            transition: `width ${SCRIPT.barDuration}ms cubic-bezier(.45,0,.25,1)`,
          }}
        />
      </div>
      <button type="button" className="pf-skip" data-curtain-skip="" onClick={reveal}>
        Skip →
      </button>
    </div>
  );
}
