# Phase 1e — Sub-phase 2: Email magic-link sign-in — Execution Report

**Status:** SUCCESS
**Date:** 2026-05-30

## Implemented
Email magic-link (passwordless OTP) sign-in for the static-export SPA:
- Flipped the browser Supabase client auth config to carry an authed session.
- A typed `sendMagicLink` wrapper that validates the email locally (Zod) BEFORE any API call (Rule 12), then calls `signInWithOtp` with a `/callback` redirect.
- A 5-state (`empty | invalid | sending | sent | error`) `EmailSignIn` client component.
- A client-side `(auth)/callback` page that establishes the session via `detectSessionInUrl` (no server runtime).
- A boundary-mocked test that asserts the valid/invalid contract.

## Files created / modified
- **Modified** `src/lib/supabase/client.ts` — auth config only: `persistSession:false→true`, `autoRefreshToken:false→true`, added `detectSessionInUrl:true`; JSDoc updated (now also carries the authed session). Signature of `getSupabaseBrowserClient` unchanged.
- **Created** `src/lib/supabase/auth.ts` — `sendMagicLink(email, client?)` (Zod-validated, `{ok:true}|{ok:false,error_message}`), `getCurrentSession(client?)` (used by the callback page), private `resolveEmailRedirectTo()` (`window.location.origin` with `NEXT_PUBLIC_APP_URL` SSR fallback).
- **Created** `src/components/onboarding/EmailSignIn.tsx` — `"use client"`, explicit `SignInState` machine, optional `onSent?(email)` prop for SP4, reel visual register (BlipLogo, pill/control tokens). No flow wiring.
- **Created** `src/app/(auth)/callback/page.tsx` — `"use client"`, `window`-guarded; on mount checks `getSession()` and subscribes to `onAuthStateChange`, renders "signing you in…" → "you're signed in". No `useSearchParams`, no hard redirect into SP4-owned routes.
- **Created** `tests/lib/supabase/auth.test.ts` — boundary-mock of `client.auth.signInWithOtp` (matches `supabaseFeed.test.ts`).

## Divergences (+ why)
- **Quote style:** used double quotes, not single. The codebase's Biome config (`biome.json`) enforces `quoteStyle: "double"` and every existing file uses double quotes. Rule 11 (match the codebase) + Rule 7 (the more-tested local convention wins over the global CLAUDE.md note).
- **Injectable `client` param** on `sendMagicLink`/`getCurrentSession` (defaults to the shared browser client). Lets the test mock at the boundary without `vi.mock` of the singleton — same spirit as `getFeed(client)` in the feed module. Not dead code: it is the seam the test uses.
- `getCurrentSession` is retained (not dead, Rule 2): the callback page documents it as the confirmation helper; the page itself calls `supabase.auth.getSession()` inline for tighter `isMounted` control, and `getCurrentSession` remains the typed public helper SP4 can reuse. (Minor: see concern below.)

## Review findings + fixes (Step B/C)
- **[Medium] Redundant ternary** in `EmailSignIn` onChange (`... ? "empty" : "empty"`) — both branches identical. **Fixed** to a plain `setSignInState("empty")` with a clarifying comment.
- **[Low] Biome formatting** (two wrap/line-length nits in `auth.ts` and `EmailSignIn.tsx`). **Fixed** via `biome check --write`.
- **No secret/token logging** — verified: logs carry event names, `email_redirect_to`, a coarse `has_at_symbol` flag, and `error.message`; never the email body, OTP, or session tokens.
- **Static-export safety** — verified: both client files are `"use client"`; the callback guards `typeof window === "undefined"`; no `useSearchParams`/Suspense; redirect resolution has an SSR-safe `NEXT_PUBLIC_APP_URL` fallback.
- **No `any`** introduced; the test's `as never` boundary cast carries a `// Reason:` comment (matches `supabaseFeed.test.ts`).

## Validation results (Step D)
- `npm run lint` (Biome) → **PASS** — `Checked 39 files. No fixes applied.` 0 errors.
- `npx tsc --noEmit` → **PASS** — 0 errors (no output).
- `npx vitest run` → **PASS** — `Test Files 9 passed (9) | Tests 85 passed (85)`. New file: 3 passed.
- `npm run build` → **PASS** — static export compiled; `/callback` generated: `○ /callback 62 kB` under `(Static) prerendered as static content`.
- **Rule 9 mutation check** — temporarily removed the `sendMagicLink` validation early-return → the invalid-email test FAILED (`signInWithOtp` got called, `result.ok` was true); restored → 3 passed. Confirms the test encodes the guarantee, not just behavior.

## Definition of done — PASS
- Valid email calls `signInWithOtp` exactly once (asserted vs mocked client) and the component renders the "sent" state. ✔
- Invalid email renders an inline error (`invalid` state, `role="alert"`) and NEVER calls the API (asserted; mutation-verified). ✔
- Callback page establishes the session client-side via `detectSessionInUrl` (getSession + onAuthStateChange). ✔
- No secrets logged. ✔
- State machine has all 5 states (`empty | invalid | sending | sent | error`). ✔

## Concerns for the orchestrator
1. **Deploy-config, not code: Supabase redirect allowlist.** The dashboard must allowlist the callback URL (Authentication → URL Configuration → Redirect URLs): add the prod app origin + `/callback`, plus `http://localhost:3000/callback` for dev, and the Capacitor scheme origin once known. Without this, `signInWithOtp({ emailRedirectTo })` is rejected by Supabase. Also set the Site URL.
2. **Email provider must be enabled** in Supabase Auth for magic links to actually send (default project SMTP is rate-limited; a real SMTP/provider is needed for volume).
3. **`getCurrentSession` is currently only consumed by SP4-facing intent**, not by the callback page's own code path (the page inlines `getSession()` for `isMounted` control). If the orchestrator's slop scan flags it as unused-for-now, it is intentionally the typed public seam for SP4 — keep or let SP4 adopt it. Flagging per Rule 12 rather than hiding it.
4. **SP4 dependency:** the callback page intentionally does NOT route into onboarding (route owned by SP4, doesn't exist yet). SP4 must decide the post-auth redirect (e.g. from `(auth)/callback` → `(onboarding)`), and wire `EmailSignIn`'s `onSent`.
5. Did NOT commit (per instructions — orchestrator commits at phase end).
