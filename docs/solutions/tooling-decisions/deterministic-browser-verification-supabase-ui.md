---
title: Deterministic browser verification of a Supabase-backed UI (puppeteer interception + browser-use CDP attach)
tags: [puppeteer, browser-use, e2e, supabase, postgrest, cors, cdp, ui-slice]
problem_type: tooling
symptoms: A UI slice needs browser verification over a scripted data state (specific daily_feeds rows), but the app reads live Supabase — no fixture path covers the new metadata, browser-use's install script 404s, and intercepted PostgREST stubs are silently discarded by the browser
root_cause: (1) cross-origin request interception must answer the CORS preflight — puppeteer req.respond() without Access-Control-Allow-* headers makes the browser drop the stubbed response and the app spins on LOADING; (2) the documented browser-use install (curl browser-use.com/cli/install.sh) is dead, and the current CLI attaches to an existing CDP browser instead of launching one
date: 2026-07-03
---

Established verifying slice #8 (reel section rendering). Reuse for any UI slice that must
be browser-verified against a scripted Supabase data state.

**Puppeteer regression (committed, e.g. `tests/e2e/reelSections.e2e.mjs`).**
- Seed a non-expired session into localStorage via `page.evaluateOnNewDocument` under the
  key `sb-<project-ref>-auth-token` (pattern from `tests/e2e/interviewChat.e2e.mjs`).
- `page.setRequestInterception(true)` and stub `*.supabase.co` routes. **Every stubbed
  response — including a 204 for `OPTIONS` preflights — must carry
  `access-control-allow-origin/headers/methods` or Chrome discards it** (the interview
  e2e never hit this because its stub was same-origin `/api/...`).
- `.maybeSingle()` reads expect a single JSON **object** body, not a one-row array.
- Keep puppeteer OUT of package.json (repo convention): `npm i --no-save puppeteer`.

**browser-use walkthrough (exploratory, B8.5).**
- The playbook §3 installer URL 404s. Working path: `uvx browser-use` (Python CLI,
  heredoc of Python with pre-imported helpers: `goto_url`, `js(...)`,
  `capture_screenshot(path)`).
- It attaches to a *running* browser via CDP. Launch puppeteer's own Chrome-for-Testing
  headless with `--remote-debugging-port=9222 --window-size=390,900` (portrait, or the
  phone-shell chrome is cropped out of screenshots), read `webSocketDebuggerUrl` from
  `http://127.0.0.1:9222/json/version`, export it as `BU_CDP_WS`.
- browser-use cannot intercept requests — instead run a ~60-line node PostgREST stub
  (CORS + `/rest/v1/users` + the rows under test) and start the dev server with
  `NEXT_PUBLIC_SUPABASE_URL=http://localhost:<stub>` `NEXT_PUBLIC_SUPABASE_ANON_KEY=stub`.
  The localStorage key becomes `sb-localhost-auth-token`.
- `js(...)` evaluations share one page context: a top-level `const c = …` collides on the
  next call — wrap statements in an IIFE.

**PostgREST embed gotcha (bonus, from the same slice).** When a table has TWO FKs into the
same target (`daily_feeds` → `interests` twice), disambiguate the embed with the **column
hint** (`interests!feed_matched_interest_id(interest_label)`), not the auto-generated
constraint name — the name exists only by Postgres convention and a rename breaks the
select silently at runtime.
