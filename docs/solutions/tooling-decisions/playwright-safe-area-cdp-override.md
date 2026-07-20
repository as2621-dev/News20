---
title: Simulating iOS safe-area insets in headless E2E (CDP setSafeAreaInsetsOverride) + stateful gate stubs
tags: [playwright, safe-area, env, cdp, e2e, onboarding, supabase, gate]
problem_type: tooling
symptoms: a UI slice must be verified at notch/Dynamic-Island insets but headless Chromium resolves
  env(safe-area-inset-*) to 0, so inset-dependent layout (doubled-padding bugs, clipped chrome)
  is untestable in the browser lane; separately, a walkthrough that stamps a gate flag then
  routes home flakes because the stubbed gate read still says "not onboarded"
root_cause: env() only resolves on real devices with viewport-fit=cover — unless overridden via
  the Chrome DevTools Protocol; and a stateless PostgREST stub makes any post-write read-back
  race the app's own redirect logic
date: 2026-07-07
---

Established in slice #38 (Playwright onboarding walkthrough). Reuse for ANY browser test that
must prove layout under real insets, and any walkthrough that crosses a persisted gate.

**Safe-area simulation (works in playwright-core 1.60 / chromium-1223 and Chrome 149):**

```js
const cdp = await context.newCDPSession(page);
await cdp.send("Emulation.setSafeAreaInsetsOverride", { insets: { top: 59, bottom: 34 } });
```

After this, `env(safe-area-inset-top)` computes to 59px in CSS — measurable via
`getComputedStyle(el).paddingTop`. Use 59/34 for Dynamic-Island class, 20/0 for SE class.
This turns "doubled safe-area padding" bugs into a hard numeric assertion: measure the
padded element's box and assert `y ≈ inset + designGap`, with the doubled value
(`2*inset + gap`) as the named failure point. (#38 verdict: onboarding applies env() ONCE
in OnboardingFlow `<main>`; progress bar at 83px = 59 + 24 `pt-6` — the reported gap was
the stale Capacitor bundle, not code.)

**Playwright vs the repo's puppeteer e2es:** playwright-core is already a devDependency and
launches the ms-playwright cached chromium with zero downloads — same standalone
`tests/e2e/*.e2e.mjs` convention (NOT wired into vitest). CORS rule from the puppeteer
learnings carries over verbatim: every cross-origin `route.fulfill` (including OPTIONS 204)
needs `access-control-allow-*` headers or Chromium discards it.

**Stateful gate stub (the flake that looks random):** if the flow PATCHes
`users.user_onboarded_at` and then routes to `/`, the root gate re-READS users. A stub that
always returns `user_onboarded_at: null` makes the app (correctly) bounce back to
/onboarding, so `waitForFunction(pathname === "/")` races the redirect — intermittent
timeouts. Record the PATCH and let subsequent GETs return the stamped value. General rule:
any stubbed read that the app consults AFTER a write it just made must reflect that write.

**Two smaller traps from the same slice:**
- Phases rendered mutually exclusively (`{phase === "youtube" ? … : null}`) mean layout
  probes must run WHILE the phase is mounted — a later "summary-phase" probe measures an
  empty DOM and can never fail.
- An inner `overflow-y-auto` scroller forces computed `overflow-x: auto`, so contained
  child overflow never inflates the ROOT scrollWidth — assert the scroller's own
  `scrollWidth <= clientWidth`, not the document's.
- `next build` clobbers a running `next dev`'s `.next` — restart dev after building, or the
  dev-target e2e fails on a broken bundle.
