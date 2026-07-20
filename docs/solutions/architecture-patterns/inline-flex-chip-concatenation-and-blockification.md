---
title: An `inline-flex` chip concatenates with the next inline sibling — and `display: flex` is a safe fix even for a shared style
tags: [css, layout, flexbox, reel-header, shared-styles, blockification]
problem_type: architecture
symptoms: A category chip and the headline below it render as one run-on line ("Wildcard Language Magazine"); the markup looks correctly stacked, and a jsdom unit test asserting the class names passes
root_cause: `.seg-chip` was `display: inline-flex` (an inline-level box) and the headline was wrapped in a `<button>` (UA default `inline-block`), so both sat on one shared line box, baseline-aligned
date: 2026-07-18
---

Established fixing slice #46 (PRD RC6). Two durable facts.

**1. `inline-flex` is an inline-level box.** It flows on a line with any inline-level
sibling that follows it. `inline-flex` vs `flex` controls the box's OUTER display type,
not just how its children lay out — an easy thing to misread, because the interesting part
of the name is the "flex". If an element is meant to own its own line in a block container,
it needs `display: flex`, not `inline-flex`.

**2. Changing a shared style from `inline-flex` to `flex` is safe wherever the element is
a flex item.** This is the part that makes the fix a one-liner instead of a scoped
override. `.seg-chip` renders in two places:
- `ReelSectionHeader.tsx` inside `.head` — a block container. Here the change is the fix.
- `ArticleLayer.tsx` inside `.art-top` — which is `display: flex`. Here the change is a
  **no-op**, because per CSS Display 3 §2.7 a flex item's `display` is *blockified*, and
  `inline-flex` blockifies to `flex`. The computed value there was already `flex`.

So before scoping a shared-style fix to one call site (`.head .seg-chip { … }`), check
whether the other call sites are flex/grid containers. If they are, the unscoped one-liner
is correct and you avoid a specificity band-aid. **Verify it, don't just reason it** — the
parity check here was driving the article layer in puppeteer and asserting the chip stayed
to the right of the back button on the same row.

**Corollary — a full-width block chip is usually harmless.** Going block-level makes the
box stretch to the container width. That only matters if the element has a background,
border, or `justify-content`. `.seg-chip` has none, its label carries its own
`max-width` + ellipsis, and `.head` already sat above the tap layer — so nothing changed
visually or in hit-testing. Check those four things before assuming full-width is fine.

**Testing note.** jsdom has no layout engine, so no unit test can prove this. The honest
lock is a browser test asserting **geometry** (`headline.top >= chip.bottom`), never a
class name — a class name survives the exact bug. See
`tests/e2e/reelHeaderLayout.e2e.mjs`.

**Mutation-test each fix in isolation when you wrote two.** The first pass here changed
both the chip's CSS *and* the headline button to `display: block`. Reverting each
separately showed **either alone** made the test pass — so one was dead weight and was
dropped (Rule 2). Two plausible fixes applied together hide which one is load-bearing.
