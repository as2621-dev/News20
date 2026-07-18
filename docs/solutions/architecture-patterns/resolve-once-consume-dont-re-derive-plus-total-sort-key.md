---
title: A value resolved once and carried on a result must be consumed, not re-derived
tags: [resolve-once, single-source-of-truth, segment-resolution, determinism, sort-key, persist]
problem_type: pattern
symptoms: duplicate structured logs (2x per item) inflating a rate metric; work redone at a downstream seam; a late failure after expensive spend that a cheaper earlier gate should have caught
root_cause: a downstream stage re-derives a value from the same raw inputs instead of consuming the value an upstream stage already resolved and carried on its result object
date: 2026-07-18
---

## What happened (slice #61, commit 60906b4)

`write_phase` resolved the story segment once and stored it on
`WritePhaseResult.segment_slug`, but `render_phase` handed `interest_segment_lookup`
back to `persist_digest`, which **re-resolved from the raw tags**. The two derivations
used the same inputs so the outputs agreed — which is exactly why it looked harmless.
It was not: `segment_resolution_conflict` logged **twice per story** (any conflict-rate
read off that log, e.g. the #47 audit, was doubled), the resolve work ran twice, and a
caller that passed the lookup to write but not render would burn TTS + poster +
enrichment spend before a persist-time re-resolution rejected it.

## Rules

1. **Consume, don't re-derive.** When an upstream stage resolves a value and carries it
   on its result object (`WritePhaseResult.segment_slug`), every downstream consumer must
   read that field. Re-deriving from the same raw inputs is a bug even when the outputs
   agree — it double-counts side effects (logs, metrics), redoes work, and reopens a
   reject point after money is spent.
2. **Enforce the invariant structurally, not by comment.** A docstring saying "resolve
   ONCE" is not enforcement. Remove the re-derivation *input* from the downstream seam:
   `render_phase` no longer takes `interest_segment_lookup`, so it *cannot* re-resolve —
   illegal state made unrepresentable. Keep a resolve-from-inputs fallback only for the
   genuinely separate direct-caller class (e2e fixtures, scripts) that never went through
   the upstream stage; branch on `if value is None:`.
3. **A partial sort key that leans on stable-sort input order is non-deterministic.**
   `resolve_segment_from_tags` sorted by `match_depth` only, so equal-depth tags under
   different roots were decided by incoming list order — the "deterministic winner" the
   conflict log named was a lie. Make the key **total** by appending an intrinsic field:
   `(depth, -relevance-or-inf, root)`. The last component (the resolved root itself)
   guarantees order-independence even when the middle signal is absent; a real signal
   (relevance) refines it when present.

Related: [[no-signal-returns-none-not-default-plus-twin-drift-test]] (same resolver's
reject-don't-default contract), [[read-side-gate-loses-the-write-side-discriminator]]
(the sibling re-computation trap — re-applying a predicate whose input drifted).
