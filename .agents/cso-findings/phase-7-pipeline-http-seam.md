# CSO findings — phase-7-pipeline-http-seam

**Date:** 2026-06-16
**Phase diff verdict:** PASS — the seam itself is sound (fail-closed router-wide bearer guard, `hmac.compare_digest` timing-safe compare, empty-secret → 500 not open, Pydantic-validated bodies, parameterized Supabase queries, no hardcoded secrets, no secret/PII logged, no new deps).

## Carry-forward concern for Phase 7b (HIGH — design, not a defect in this diff)

`POST /feed/assemble-for-user` and `POST /pipeline/daily` are guarded by the **shared** `PIPELINE_TRIGGER_SECRET` and run on the **service-role** Supabase client (RLS bypassed). They take `user_id` / run params from the request body.

Phase 7b's plan as written has the **onboarding client** (`BuildYour30.tsx`) call `/feed/assemble-for-user`. If the client holds `PIPELINE_TRIGGER_SECRET`, that secret ships in the app/browser bundle and an attacker could:
- assemble/overwrite the feed for **any** `user_id`, and
- trigger the paid `/pipeline/daily` run at will.

**This shared-secret seam must stay server-to-server only.** For Phase 7b, do ONE of:
1. **Server proxy (recommended):** a Next.js route handler / server action holds the secret server-side; the client calls the proxy with its Supabase session; the proxy derives `user_id` from the **verified** session (never the body) and calls the worker.
2. **JWT-scoped endpoint:** add a separate worker endpoint that authenticates the user's Supabase JWT and assembles only the token's own user (user_id from the verified token, not the body) — no shared secret on the client.

Do NOT embed `PIPELINE_TRIGGER_SECRET` in the SPA/Capacitor client. Reflected in `phase-7b-onboarding-first-run-feed.md` Sub-phase 1 — orchestrator to flag before 7b runs.

## Medium / low (no action required this phase)
- `_load_interest_nodes` duplicates the interest-node construction inlined in `_run_daily` (lazy-import boundary makes sharing awkward). Low — acceptable; revisit if a third caller appears.
- Synthetic `canonical_url` (`https://news20.app/{story_id}`) is fabricated for `CanonicalStory` because the allocator does not read the URL. Low — documented in `_load_ready_story_pool`.
