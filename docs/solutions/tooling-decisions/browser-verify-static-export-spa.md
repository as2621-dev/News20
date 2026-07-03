---
title: Browser-verifying a UI slice in a static-export SPA (no API routes, JWT-gated client, maybe-dead worker)
tags: [puppeteer, browser-verification, static-export, supabase-session, onboarding, ui-slice]
problem_type: tooling
symptoms: UI slice needs a real-browser walkthrough but the app is `output: "export"` (no API route handlers to stub), the client requires a Supabase session, and the worker endpoint is unreachable locally
root_cause: n/a (technique)
date: 2026-07-03
---

Established browser-verifying the interview chat stage (slice #4). Reuse for any UI slice
whose client calls the worker and/or requires auth.

**Constraint:** `next.config` is `output: "export"`, so you CANNOT add an `app/api/**/route.ts`
to mock an endpoint (it errors at `next build`). And the client short-circuits on
`getCurrentSession()` before it ever fetches, so mocking the HTTP response alone isn't enough.

**Recipe (deterministic, offline, no repo package.json changes):**

1. **puppeteer-core + system Chrome** — no download. Chrome is at
   `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, and `~/.cache/puppeteer`
   already exists. Install `puppeteer-core` in the SCRATCHPAD (not the repo — shared tree),
   launch with `executablePath` = that Chrome.
2. **Seed a Supabase session in localStorage** via `page.evaluateOnNewDocument(...)` BEFORE
   navigation: key `sb-<project-ref>-auth-token` (ref from `NEXT_PUBLIC_SUPABASE_URL`, e.g.
   `cerfennlcgureyifraqy`), value a JSON session with `expires_at` far in the future.
   `supabase.auth.getSession()` reads localStorage and returns a non-expired token WITHOUT a
   network call, so `getCurrentSession()` resolves and the real client path runs offline.
3. **Request-intercept the worker endpoint** host-agnostically: `page.setRequestInterception(true)`
   then match on the URL PATH substring (e.g. `/api/interview/turn`) — this catches it whether
   the base URL resolves to same-origin or the real Railway worker — and `req.respond(...)` scripted
   JSON keyed on the request body (e.g. the conversation length) for a deterministic multi-turn flow.
4. **Sidestep the persist network** with `NEXT_PUBLIC_ONBOARDING_SKIP_AUTH=true` when starting
   `next dev`: the onboarding flow opens directly on the stage under test AND its terminal/confirm
   handoff takes the no-persist branch (no live Supabase write), so the walkthrough reaches the
   NEXT stage without a working DB. (Seed the session anyway — the turn client still needs it.)

**Commit the puppeteer script** as the deterministic regression (`tests/e2e/*.e2e.mjs`), but do
NOT wire it into `npm test` (vitest include is `tests/lib/**` + `tests/seed/**`) — a browser dep
would make unit CI require Chromium. Document `npm i -D puppeteer` in the file header; puppeteer is
intentionally kept out of `package.json` to avoid clobbering the shared lockfile mid-flight.

**jsdom gotcha (unit side):** the repo's jsdom build lacks a usable `localStorage.clear()`. Any
vitest touching localStorage must `Object.defineProperty(globalThis, "localStorage", {…})` an
in-memory stub in `beforeEach` — see `tests/lib/onboardingProfile.test.ts` / `signals.test.ts`.
