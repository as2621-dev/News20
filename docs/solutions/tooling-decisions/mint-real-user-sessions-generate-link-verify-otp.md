---
title: Mint real Supabase user sessions for E2E (generate_link → verify_otp) — fresh client per user
tags: [supabase, auth, e2e, jwt, personas, browser-verification, worker]
problem_type: tooling
symptoms: an E2E run needs a REAL user JWT (worker routes verify via supabase.auth.get_user;
  the SPA needs a live session in localStorage) for test users whose emails can't receive
  OTP mail (e.g. persona.*@news20.seed); and when minting sessions for several users in one
  script, the SECOND admin call fails 403 "User not allowed"
root_cause: admin.generate_link(magiclink) + auth.verify_otp(token_hash) yields a genuine
  session with no mailbox — but verify_otp REPLACES the supabase-py client's own auth
  session with the minted user's, so subsequent admin.* calls on that client send the
  user's JWT instead of the service key
date: 2026-07-03
---

Established in slice #10 (M4 persona validation) minting JWTs for the 3 seeded personas
to (a) call the JWT-gated worker (`/api/interview/turn`) and (b) browser-verify the SPA
as each persona.

**Recipe (per user, service-role client):**

```python
sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)   # FRESH client per user
link = sb.auth.admin.generate_link({"type": "magiclink", "email": email})
session = sb.auth.verify_otp(
    {"token_hash": link.properties.hashed_token, "type": "magiclink"}
).session
# session.access_token → Authorization: Bearer for worker routes
# full session dict → localStorage["sb-<project-ref>-auth-token"] for the SPA
```

- Works for unreachable/test mailboxes — no email is sent; the hashed token is consumed
  directly.
- **The fresh-client-per-user part is load-bearing**: `verify_otp` sets the client's
  session to the minted user. On a shared client the next `auth.admin.*` call runs as
  that USER and 403s ("User not allowed"). Symptom order is deceptive — user #1 works,
  user #2 fails.
- For the SPA, seed the session via `page.evaluateOnNewDocument` BEFORE navigation
  (see browser-verify-static-export-spa.md); `getSession()` then resolves offline.
- Worker-side verification (`verify_supabase_user`) accepts these sessions exactly like
  phone logins — no test-only auth seam needed.
