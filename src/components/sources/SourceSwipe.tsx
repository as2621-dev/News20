"use client";

/**
 * SourceSwipe — the Tinder-style source-onboarding deck (Phase 5c SP-UI).
 *
 * Ported from the Claude Design "blip" handoff — Source Swipe (`blip-sources.js`).
 * After the topic picker, the user swipes through curated source recommendations
 * across 4 platforms (YouTube → Podcasts → X → People): swipe-right / Follow
 * persists the source, swipe-left / Skip discards it. A "building your profile"
 * curtain opens the flow; finishing each set auto-advances (a ~1.7s handoff); the
 * FINAL set shows "You're all set." and calls {@link SourceSwipeProps.onDone}.
 *
 * Wiring to the shipped phase-5c logic:
 *   - {@link loadSourceSwipeDeck} loads the per-platform card view-models (archetype
 *     match → balanced, popularity-ranked, follow-annotated catalog reads).
 *   - {@link followSource} / {@link unfollowSource} persist a swipe-right / undo,
 *     OPTIMISTICALLY: the card animates away immediately, the write runs async; a
 *     failure is logged with a `fix_suggestion` and surfaced non-blockingly (a
 *     small inline notice) rather than swallowed (Rule 12).
 *
 * Client-only (pointer gestures + animation + client Supabase). The prototype's
 * reviewer-only logo-upload affordance is omitted (see {@link SourceSwipeCard}).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ProfileCurtain } from "@/components/sources/ProfileCurtain";
import { SignalOrb } from "@/components/sources/SignalOrb";
import { SourceSwipeCard } from "@/components/sources/SourceSwipeCard";
import { SourceSwipeGlyphs } from "@/components/sources/sourceSwipeGlyphs";
import { logger } from "@/lib/logger";
import {
  loadSourceSwipeDeck,
  SOURCE_SWIPE_PLATFORMS,
  type SourceSwipeCardModel,
  type SourceSwipeDeck,
  type SourceSwipePlatformKey,
} from "@/lib/sourceSwipeData";
import { followSource, unfollowSource } from "@/lib/sources";

export interface SourceSwipeProps {
  /** Called once, with the total followed across all 4 sets, when the final set completes. */
  onDone: (total: number) => void;
}

/** Drag distance (px) past which a release commits the swipe (design threshold). */
const COMMIT_THRESHOLD_PX = 92;

/** The fly-off animation duration (ms) before the next card renders. */
const COMMIT_ANIM_MS = 340;

/** The per-set handoff dwell (ms) before auto-advancing to the next platform. */
const HANDOFF_MS = 1700;

/** The swipe-hint coachmark auto-dismiss delay (ms). */
const HINT_MS = 3800;

/** One entry in the undo history — enough to revert local state + unfollow if it persisted. */
interface SwipeHistoryEntry {
  platformIndex: number;
  cardIndex: number;
  didFollow: boolean;
  /** The followed source_id (only set when didFollow), so undo can unfollow it. */
  sourceId: string | null;
}

/** Loading / error / ready phases of the deck data fetch. */
type LoadPhase = "loading" | "error" | "ready";

/**
 * Render the source-swipe onboarding deck.
 *
 * @param props - {@link SourceSwipeProps}.
 */
export function SourceSwipe({ onDone }: SourceSwipeProps) {
  const [loadPhase, setLoadPhase] = useState<LoadPhase>("loading");
  const [deck, setDeck] = useState<SourceSwipeDeck | null>(null);
  const [showCurtain, setShowCurtain] = useState(true);

  // Deck position: which platform set, and how far into it.
  const [platformIndex, setPlatformIndex] = useState(0);
  const [cardIndex, setCardIndex] = useState(0);
  // followedByPlatform[i] = set of source_ids followed in platform i (for counts + undo).
  const followedByPlatformRef = useRef<Set<string>[]>([new Set(), new Set(), new Set(), new Set()]);
  const historyRef = useRef<SwipeHistoryEntry[]>([]);
  // The followed sets live in a ref (so undo can mutate them without stale closures);
  // this bumps a counter purely to re-render the live count/total off the ref.
  const [, bumpFollowedTick] = useState(0);
  const reRenderFollowedCount = useCallback(() => bumpFollowedTick((tick) => tick + 1), []);

  // Per-handoff/per-commit timers (cleared on unmount + on undo).
  const handoffTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const commitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [showHint, setShowHint] = useState(false);
  const hintShownForPlatformRef = useRef(false);
  const [persistNotice, setPersistNotice] = useState<string | null>(null);
  // True while a commit fly-off is animating, so the done screen / handoff is shown after it.
  const [isHandingOff, setIsHandingOff] = useState(false);

  // ── Load the deck once on mount ─────────────────────────────────────────────
  useEffect(() => {
    let isMounted = true;
    void loadSourceSwipeDeck()
      .then((loaded) => {
        if (!isMounted) {
          return;
        }
        setDeck(loaded);
        setLoadPhase("ready");
      })
      .catch((error: unknown) => {
        if (!isMounted) {
          return;
        }
        logger.error("source_swipe_load_failed", {
          error_message: error instanceof Error ? error.message : "unknown",
          fix_suggestion: "Confirm migrations 0003/0007/0009 applied and the catalog/profile reads are permitted.",
        });
        setLoadPhase("error");
      });
    return () => {
      isMounted = false;
    };
  }, []);

  // Clear all pending timers on unmount.
  useEffect(() => {
    return () => {
      [handoffTimerRef, commitTimerRef, hintTimerRef].forEach((ref) => {
        if (ref.current) {
          clearTimeout(ref.current);
        }
      });
    };
  }, []);

  const platform = SOURCE_SWIPE_PLATFORMS[platformIndex];
  const isLastPlatform = platformIndex >= SOURCE_SWIPE_PLATFORMS.length - 1;
  const cards: SourceSwipeCardModel[] = deck?.cards_by_platform[platform.key] ?? [];
  const isSetComplete = cardIndex >= cards.length;

  /** The 3-card slice (lead + up to 2 behind) currently in the deck. */
  const slice = cards.slice(cardIndex, cardIndex + 3);

  /** Show the swipe hint once per platform set, auto-dismissing after HINT_MS. */
  const maybeShowHint = useCallback(() => {
    if (hintShownForPlatformRef.current) {
      return;
    }
    hintShownForPlatformRef.current = true;
    setShowHint(true);
    if (hintTimerRef.current) {
      clearTimeout(hintTimerRef.current);
    }
    hintTimerRef.current = setTimeout(() => setShowHint(false), HINT_MS);
  }, []);

  const dismissHint = useCallback(() => {
    setShowHint(false);
    if (hintTimerRef.current) {
      clearTimeout(hintTimerRef.current);
    }
  }, []);

  // Show the hint whenever a fresh, non-empty set becomes interactive (deck visible).
  useEffect(() => {
    if (!showCurtain && loadPhase === "ready" && !isSetComplete && slice.length > 0) {
      maybeShowHint();
    }
  }, [showCurtain, loadPhase, isSetComplete, slice.length, maybeShowHint]);

  /** Advance to the next platform set (resets card index + hint state). */
  const advancePlatform = useCallback(() => {
    setIsHandingOff(false);
    setPlatformIndex((prev) => Math.min(prev + 1, SOURCE_SWIPE_PLATFORMS.length - 1));
    setCardIndex(0);
    hintShownForPlatformRef.current = false;
  }, []);

  /**
   * Persist a follow optimistically. The card has already animated away; the write
   * runs async. A failure is logged + surfaced non-blockingly (never swallowed),
   * and the source is removed from the local followed set so undo/counts stay honest.
   */
  const persistFollow = useCallback(
    (card: SourceSwipeCardModel, platformIdx: number) => {
      void followSource(card.source_id).catch((error: unknown) => {
        followedByPlatformRef.current[platformIdx].delete(card.source_id);
        reRenderFollowedCount();
        logger.error("source_swipe_follow_failed", {
          source_id: card.source_id,
          error_message: error instanceof Error ? error.message : "unknown",
          fix_suggestion: "Confirm the user is authed and migration 0009 RLS permits the owner follow write.",
        });
        setPersistNotice(`Couldn't save ${card.source_name} — sign-in may have expired.`);
      });
    },
    [reRenderFollowedCount],
  );

  /** Commit the lead card: follow (right) or skip (left). Optimistic + advances. */
  const commit = useCallback(
    (follow: boolean) => {
      const leadCard = slice[0];
      if (!leadCard || isHandingOff) {
        return;
      }
      dismissHint();

      const followed = followedByPlatformRef.current[platformIndex];
      if (follow) {
        followed.add(leadCard.source_id);
        reRenderFollowedCount();
        persistFollow(leadCard, platformIndex);
      }
      historyRef.current.push({
        platformIndex,
        cardIndex,
        didFollow: follow,
        sourceId: follow ? leadCard.source_id : null,
      });

      // Animate the fly-off via a transient class on the lead DOM node, then advance.
      const leadEl = document.querySelector<HTMLElement>('[data-source-swipe-card="lead"]');
      if (leadEl) {
        leadEl.style.transition = "transform .42s cubic-bezier(.4,0,.2,1), opacity .42s ease";
        leadEl.style.transform = `translateX(${follow ? 120 : -120}%) rotate(${follow ? 14 : -14}deg)`;
        leadEl.style.opacity = "0";
        const stamp = leadEl.querySelector<HTMLElement>(follow ? ".stamp.follow" : ".stamp.skip");
        if (stamp) {
          stamp.style.opacity = "1";
        }
      }
      if (commitTimerRef.current) {
        clearTimeout(commitTimerRef.current);
      }
      commitTimerRef.current = setTimeout(() => setCardIndex((prev) => prev + 1), COMMIT_ANIM_MS);
    },
    [slice, isHandingOff, dismissHint, platformIndex, cardIndex, persistFollow, reRenderFollowedCount],
  );

  /** Skip the rest of this set (jump card index to the end → triggers done). */
  const skipSet = useCallback(() => {
    dismissHint();
    setCardIndex(cards.length);
  }, [cards.length, dismissHint]);

  /** Undo the last swipe: revert local state, and unfollow if it had persisted. */
  const undo = useCallback(() => {
    const entry = historyRef.current.pop();
    if (!entry) {
      return;
    }
    // Returning to a prior platform cancels any in-flight handoff timer.
    if (handoffTimerRef.current) {
      clearTimeout(handoffTimerRef.current);
    }
    setIsHandingOff(false);
    setPlatformIndex(entry.platformIndex);
    setCardIndex(entry.cardIndex);
    if (entry.didFollow && entry.sourceId) {
      const undoneSourceId = entry.sourceId;
      followedByPlatformRef.current[entry.platformIndex].delete(undoneSourceId);
      reRenderFollowedCount();
      void unfollowSource(undoneSourceId).catch((error: unknown) => {
        logger.error("source_swipe_undo_unfollow_failed", {
          source_id: undoneSourceId,
          error_message: error instanceof Error ? error.message : "unknown",
          fix_suggestion: "Confirm the user is authed and migration 0009 RLS permits the owner unfollow.",
        });
        setPersistNotice("Couldn't fully undo that follow — we'll reconcile it next sync.");
      });
    }
  }, [reRenderFollowedCount]);

  // ── Per-set completion: handoff + auto-advance, or final done ───────────────
  // Reason: platformIndex is an intentional re-key so a SECOND consecutive empty set
  // (isSetComplete stays true across platforms) still schedules its own auto-advance.
  // biome-ignore lint/correctness/useExhaustiveDependencies: platformIndex re-key is intentional (see above).
  useEffect(() => {
    if (showCurtain || loadPhase !== "ready" || !isSetComplete) {
      return;
    }
    if (isLastPlatform) {
      return; // final done renders its own CTA; no auto-advance
    }
    setIsHandingOff(true);
    if (handoffTimerRef.current) {
      clearTimeout(handoffTimerRef.current);
    }
    handoffTimerRef.current = setTimeout(advancePlatform, HANDOFF_MS);
    return () => {
      if (handoffTimerRef.current) {
        clearTimeout(handoffTimerRef.current);
      }
    };
  }, [showCurtain, loadPhase, isSetComplete, isLastPlatform, platformIndex, advancePlatform]);

  const totalFollowed = followedByPlatformRef.current.reduce((sum, set) => sum + set.size, 0);
  const setCount = followedByPlatformRef.current[platformIndex].size;

  // Bind pointer gestures to the lead card.
  const handlePointerDown = useDragGesture(commit, dismissHint, isHandingOff);

  const countsByPlatform = deck
    ? (Object.fromEntries(SOURCE_SWIPE_PLATFORMS.map((p) => [p.key, deck.cards_by_platform[p.key].length])) as Record<
        SourceSwipePlatformKey,
        number
      >)
    : ({ yt: 0, pod: 0, x: 0, people: 0 } as Record<SourceSwipePlatformKey, number>);

  return (
    <div className="sw-screen">
      <SourceSwipeGlyphs />
      <div className="sw-root">
        {loadPhase === "error" ? (
          <div className="sw-done" style={{ display: "flex" }}>
            <h2>Couldn&apos;t load recommendations.</h2>
            <p>We&apos;ll start you with the day&apos;s biggest stories.</p>
            <button type="button" className="sw-next" style={{ display: "inline-block" }} onClick={() => onDone(0)}>
              See my briefing →
            </button>
          </div>
        ) : null}

        {showCurtain && loadPhase !== "error" ? (
          <ProfileCurtain
            picks={deck?.curtain_picks ?? []}
            countsByPlatform={countsByPlatform}
            onReveal={() => setShowCurtain(false)}
          />
        ) : null}

        {loadPhase === "ready" && !showCurtain ? (
          <>
            <div className="sw-top">
              <div className="pass-pips">
                {SOURCE_SWIPE_PLATFORMS.map((p, i) => (
                  <span key={p.key} className="pip-wrap">
                    <span className={`pip${i < platformIndex ? " done" : i === platformIndex ? " cur" : ""}`}>
                      <svg className="g" aria-hidden="true">
                        <use href={`#${p.glyph}`} />
                      </svg>
                      {p.label}
                    </span>
                    {i < SOURCE_SWIPE_PLATFORMS.length - 1 ? <span className="sep">›</span> : null}
                  </span>
                ))}
              </div>
              <div className="pass-head">
                <h1>{platform.title}</h1>
                <span className="nofm">
                  <b>{Math.min(cardIndex + 1, cards.length || 1)}</b> / {cards.length}
                </span>
              </div>
              <div className="seg-progress2">
                {cards.map((card, i) => (
                  <i key={card.source_id} className={i < cardIndex ? "done" : i === cardIndex ? "cur" : ""} />
                ))}
              </div>
            </div>

            {!isSetComplete ? (
              <div className="deck" onPointerDown={handlePointerDown}>
                {slice
                  .map((card, i) => ({ card, layer: i as 0 | 1 | 2 }))
                  .reverse()
                  .map(({ card, layer }) => (
                    <SourceSwipeCard key={card.source_id} card={card} platform={platform} layer={layer} />
                  ))}
                <div className={`swhint${showHint ? " show" : ""}`}>
                  <div className="side l">
                    <div className="arrow">
                      <svg style={{ transform: "scaleX(-1)" }} aria-hidden="true">
                        <use href="#i-arrow" />
                      </svg>
                    </div>
                    <div className="t">Swipe left · skip</div>
                  </div>
                  <div className="side r">
                    <div className="arrow">
                      <svg aria-hidden="true">
                        <use href="#i-arrow" />
                      </svg>
                    </div>
                    <div className="t">Swipe right · follow</div>
                  </div>
                </div>
              </div>
            ) : (
              <DoneScreen
                platformLabel={platform.label}
                setCount={setCount}
                isLastPlatform={isLastPlatform}
                nextLabel={isLastPlatform ? null : SOURCE_SWIPE_PLATFORMS[platformIndex + 1].label}
                totalFollowed={totalFollowed}
                onDone={() => onDone(totalFollowed)}
              />
            )}

            {!isSetComplete ? (
              <>
                <div className="actions">
                  <div className="acol">
                    <button type="button" className="act2 skip" data-action="skip" onClick={() => commit(false)}>
                      <svg aria-hidden="true">
                        <use href="#i-x" />
                      </svg>
                    </button>
                    <span className="al">Skip</span>
                  </div>
                  <div className="acol">
                    <button type="button" className="act2 undo" data-action="undo" onClick={undo}>
                      <svg aria-hidden="true">
                        <use href="#i-undo" />
                      </svg>
                    </button>
                    <span className="al">Undo</span>
                  </div>
                  <div className="acol">
                    <button type="button" className="act2 follow" data-action="follow" onClick={() => commit(true)}>
                      <svg aria-hidden="true">
                        <use href="#i-plus" />
                      </svg>
                    </button>
                    <span className="al">Follow</span>
                  </div>
                </div>

                <div className="sw-foot">
                  <span className="sw-count">
                    <b>{setCount}</b> FOLLOWED THIS SET
                  </span>
                  <button type="button" className="sw-skipall" onClick={skipSet}>
                    Skip set
                  </button>
                </div>
              </>
            ) : null}

            {persistNotice ? (
              <p role="alert" className="sw-persist-notice">
                {persistNotice}
              </p>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The per-set "done"/handoff screen (a brief auto-advancing handoff for sets 1–3,
 * a final "You're all set." CTA for the last set).
 */
function DoneScreen({
  platformLabel,
  setCount,
  isLastPlatform,
  nextLabel,
  totalFollowed,
  onDone,
}: {
  platformLabel: string;
  setCount: number;
  isLastPlatform: boolean;
  nextLabel: string | null;
  totalFollowed: number;
  onDone: () => void;
}) {
  return (
    <div className="sw-done" style={{ display: "flex" }}>
      <SignalOrb size={84} />
      {isLastPlatform ? (
        <>
          <div className="done-ey ey">All sets complete</div>
          <h2>You&apos;re all set.</h2>
          <p>{totalFollowed} sources followed across YouTube, Podcasts, X &amp; People.</p>
          <button
            type="button"
            className="sw-next"
            data-action="see-briefing"
            style={{ display: "inline-block" }}
            onClick={onDone}
          >
            See my briefing →
          </button>
        </>
      ) : (
        <>
          <div className="done-ey ey">{platformLabel} · set complete</div>
          <h2>{setCount ? `Nice — ${setCount} followed.` : `${platformLabel} done.`}</h2>
          <div className="hop">
            <span className="hp from">
              {platformLabel} <span className="ck">✓</span>
            </span>
            <span className="arr">→</span>
            <span className="hp to">{nextLabel}</span>
          </div>
          <div className="hopbar">
            <i style={{ width: "100%", transition: `width ${HANDOFF_MS - 200}ms linear` }} />
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Pointer-drag gesture handler for the lead card: tracks horizontal drag, rotates
 * the card + fades the FOLLOW/SKIP stamp by drag distance, and commits past
 * {@link COMMIT_THRESHOLD_PX} on release (right = follow, left = skip), else snaps
 * back. Returns the `onPointerDown` handler bound to the deck.
 */
function useDragGesture(
  commit: (follow: boolean) => void,
  dismissHint: () => void,
  isHandingOff: boolean,
): (event: React.PointerEvent<HTMLDivElement>) => void {
  return useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (isHandingOff) {
        return;
      }
      const lead = (event.target as HTMLElement).closest<HTMLElement>('[data-source-swipe-card="lead"]');
      if (!lead) {
        return;
      }
      dismissHint();
      const followStamp = lead.querySelector<HTMLElement>(".stamp.follow");
      const skipStamp = lead.querySelector<HTMLElement>(".stamp.skip");
      const startX = event.clientX;
      let deltaX = 0;
      lead.style.transition = "none";
      lead.setPointerCapture?.(event.pointerId);

      const onMove = (moveEvent: PointerEvent) => {
        deltaX = moveEvent.clientX - startX;
        lead.style.transform = `translate(${deltaX}px, ${deltaX * 0.04}px) rotate(${deltaX * 0.06}deg)`;
        const intensity = Math.min(Math.abs(deltaX) / COMMIT_THRESHOLD_PX, 1);
        if (deltaX > 0) {
          if (followStamp) followStamp.style.opacity = String(intensity);
          if (skipStamp) skipStamp.style.opacity = "0";
        } else {
          if (skipStamp) skipStamp.style.opacity = String(intensity);
          if (followStamp) followStamp.style.opacity = "0";
        }
      };

      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        if (Math.abs(deltaX) > COMMIT_THRESHOLD_PX) {
          commit(deltaX > 0);
        } else {
          lead.style.transition = "transform .3s cubic-bezier(.4,0,.2,1)";
          lead.style.transform = "";
          if (followStamp) followStamp.style.opacity = "0";
          if (skipStamp) skipStamp.style.opacity = "0";
        }
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    },
    [commit, dismissHint, isHandingOff],
  );
}
