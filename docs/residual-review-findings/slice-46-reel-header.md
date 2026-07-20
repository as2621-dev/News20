# Residual review findings — slice #46 (reel header chip/headline stacking)

Commit: `86278e2` (amended). Panel: correctness lens + simplicity/reuse lens.
Applied findings are not listed here — only what was deferred and why.

## DEFERRED — low · `tests/e2e/*.e2e.mjs` are not wired into any automated lane

**Finding (correctness lens).** The committed puppeteer regression
`tests/e2e/reelHeaderLayout.e2e.mjs` can never fire automatically. `puppeteer` is
deliberately absent from `package.json`, and `vitest.config.ts` scopes `npm test` to
`tests/lib/**` + `tests/seed/**`, so `npm test` never reaches `tests/e2e/`. The geometry
lock only holds if a human remembers to `npm i --no-save puppeteer`, boot a dev server on
the right port, and run the file by hand.

**Why deferred, not fixed.** This is a pre-existing, *deliberate* repo convention, not a
defect this slice introduced. All six sibling e2e files have the same property, and the
convention is documented in
`docs/solutions/tooling-decisions/browser-verify-static-export-spa.md`: puppeteer is kept
out of `package.json` on purpose so the unit lane does not require Chromium. Reversing
that is a human call about CI cost and lane structure, and it would touch every e2e file
— outside this slice's scope (Rule 3).

**Concrete fix if taken up.** Add a `test:e2e` npm script that boots the dev server and
runs every `tests/e2e/*.e2e.mjs`, add `puppeteer` as a devDependency (or install it in the
e2e lane only), and run that lane pre-merge. Worth its own slice.

## Noted, no action — `parseFloat(getComputedStyle(el).lineHeight)` is Blink-specific

`.headline` uses a unitless `line-height: 1.12`. Blink resolves `getComputedStyle` to
`"33.6px"`; Gecko would return `"1.12"`, collapsing the wrap threshold to `1.68` and making
the wrap assertion vacuous. Correct today because puppeteer is Chrome-only. An inline
comment now records this at the probe. Only matters if the suite is ever ported to
Playwright/Firefox.
