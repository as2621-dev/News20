---
title: A "dumb renderer" chat maps bubble_kind=skip to an EMPTY answer — so a label-matched accept must be an option, not a skip
tags: [interview, chat-ui, stateless-turn, bubble-kind, skip, fast-forward, ui-engine-seam, contract]
problem_type: gotcha
symptoms: a skip-fast-forward / accept-offer turn is unreachable through the chat UI even though the engine's replay test passes; tapping "accept" re-asks or never terminates
root_cause: bubble_kind="skip" is overloaded — the engine emits an accept-that-must-be-label-matched as skip-kind, but the client renders every skip-kind bubble as its "skip → empty answer" affordance, stripping the label the engine matches by
date: 2026-07-05
---

Surfaced building the one-scrollback interview chat (slice #18) against the stateless turn engine
(#14/#17). Reusable for any client that renders a server-driven turn protocol where bubbles carry a
`kind` and the server replays the echoed conversation state.

**The trap.** The interview engine emits uniform `question` turns (text + bubbles, each
`option | skip | type_your_own`) with NO turn-kind field. Two things are BOTH tagged `skip`-kind
but need OPPOSITE client behavior:
- A **true skip** ("Not really / skip", the WHO-drill "Nothing specific →") — the engine's
  `_engaged`/`real_taps` want an EMPTY answer (meta labels are filtered out), so the client must send
  `bubbles_tapped: []`.
- An **accept** on a fast-forward offer ("Skip this topic for now", "Just build my feed") — the engine
  registers it ONLY when its LABEL rides in `bubbles_tapped` (`_tapped_label` does a literal label
  match, no filtering). The client sending `[]` (or a label that `real_taps` filters) makes the accept
  **unreachable** — the offer silently takes its decline branch forever.

A uniform client rule can't satisfy both: true skip needs `[]`, label-matched accept needs `[label]`.
Whatever the client picks, one path breaks. (The *old* single-select UI sent `[label]` for every
skip-kind tap, so its offers worked but its WHO-drill skip mis-recorded; the new empty-answer skip
fixed WHO but broke the offers.)

**The fix is server-side, and it's a two-char change: encode a label-matched accept as `option`, not
`skip`.** `bubble_kind="skip"` must mean exactly one thing to the client — "skip → empty answer". Any
bubble whose SELECTION carries meaning (an accept the engine matches by label) is an `option`; the
client renders it as a tappable chip and its label lands in `bubbles_tapped` naturally. Then the
client's `handleSkip → []` is correct for every genuine skip. (`agents/interview/responses.py`:
`category_skip_offer_response` / `build_feed_offer_response` accept bubbles `skip`→`option`.)

**Why the engine's own tests didn't catch it:** the replay test fabricated the exchange directly as
`tapped=[ACCEPT_LABEL]`, proving the engine's *intended* contract — but nothing drove the offer turn
*through the renderer*, where a skip-kind bubble can never deposit its label into `bubbles_tapped`.
Lesson: a turn-protocol contract needs a test at the RENDERER seam (tap the rendered affordance →
assert the echoed `bubbles_tapped`), not only an engine-replay test with a hand-built exchange.

**General rule for stateless-turn chat renderers:** the client is a dumb renderer that maps each
`bubble_kind` to exactly one affordance+echo. If the server needs a control choice matched by label,
it must be an `option` (echoed verbatim). Reserve `skip`-kind for "no answer". Don't overload one kind
across "empty answer" and "answer that must be matched".
