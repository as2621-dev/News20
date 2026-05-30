# Progress: phase-1b-supabase-backend-seed

**Phase file:** plans/phase-1b-supabase-backend-seed.md
**Started:** 2026-05-30
**Resumed:** 2026-05-30 (credentials added → unblocked)
**Mode:** Sequential (SP1/SP2/SP3 are ⚠ irreversible — no worktree parallelism, per run-phase Step 2).

## ✅ UNBLOCKED — credentials added, hosted DB reachable

The earlier blocker (empty Supabase keys) is **resolved**. `.env` now has real values for
`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_DB_PASSWORD`,
and the `NEXT_PUBLIC_SUPABASE_*` pair. REST probe confirms reachability (`GET stories` →
404 = tables not migrated yet, the expected pre-apply state).

### DDL connection recipe (no secrets stored here)

Direct host `db.<ref>.supabase.co` is **IPv6-only** and this machine has **no IPv6 egress**,
so DDL goes through the **IPv4 session pooler**. `psql` is not installed; the Supabase CLI's
built-in pg driver is used instead. Discovered working endpoint:

- **Host:** `aws-1-us-east-1.pooler.supabase.com` · **Port:** `5432` (session mode)
- **User:** `postgres.<ref>` (ref = the project ref in `SUPABASE_URL`)
- **Password:** from `$SUPABASE_DB_PASSWORD` in `.env` (URL-encode it)
- **Apply command:**
  `supabase db push --db-url "postgresql://postgres.<ref>:<urlenc-pw>@aws-1-us-east-1.pooler.supabase.com:5432/postgres"`
  (one push applies both `0001` + `0002` in order; verify SP1 + SP2 DoDs separately afterward)
- **Seed (SP3)** needs no DB password — `supabase-js` + `SUPABASE_SERVICE_ROLE_KEY` over HTTPS.

`db push --dry-run` against this endpoint returned exit 0 and listed both migrations as
pending — connection + migration recognition confirmed.

## Sub-phase progress (RESUMED — DB reachable)
- [x] APPLY: `supabase db push --yes` applied `0001` + `0002` cleanly (exit 0). Orchestrator-verified: 13 content tables, 3 enums (anchor_speaker/bias_lean/segment_slug), 2 public buckets (digest-audio, story-posters).
- [x] 1: Content-schema migration — APPLIED + VERIFIED. 13 tables, 3 enums (exact labels), 13 FKs valid, no drift vs reference. lint+tsc PASS. DoD PASS.
- [x] 2: Storage buckets + RLS — APPLIED + VERIFIED. 2 public buckets; RLS on 13/13; 13 read policies, 0 write policies; anon SELECT 200 / anon INSERT 401(42501 RLS-deny); public object URL 200. DoD PASS. Note: anon write-denial rests on RLS (default GRANTs remain) — fine for M1.
- [x] 3: Seed 5 M0 digests — SEEDED + VERIFIED. 5 stories; 1 current digest each; captions 11/10/10/11/10 (all ≥6); 52/52 sentences have word_tokens(start/end_ms) + exactly 1 highlight; detail_chunks/trust/suggested/qa all present; audio+poster URLs HTTP 200. New tests/seed/seedMapping.test.ts (16 tests) asserts verbatim word reconstruction. Suite 81/81. DoD PASS. NOTE: fixed a real seed bug (partial-unique-index onConflict → idempotent select/update/insert); also extended vitest.config.ts include to tests/seed/** (out-of-scope but required for the mandated test to run).
- [x] 4: Typed feed data-access layer — LIVE-VERIFIED. getFeed() returns 5 stories (s1..s5), all Zod-valid vs src/types/feed.ts; word_tokens carry start/end_ms; 10/10 audio+poster URLs HTTP 200; anchors/segment/durations populated. Offline suite 81/81. DoD PASS. NOTE: fixed a real bug — PostgREST returns the many-to-one `segments` embed as a single object, code read `segments[0]` (offline mock wrongly used an array, masking it); fixed with Array.isArray normalization. Hardening the offline contract test to cover the single-object embed shape (regression guard) before commit.

## Offline validation (what DID pass)
- `npm run lint` (Biome, 35 files) → 0 errors.
- `npx tsc --noEmit` → 0 errors (the seed script IS in the program, confirmed via `--listFiles`).
- `npx vitest run` → 65 passed / 65 (7 files; the 4 new supabaseFeed tests + existing 61).
- `npm run build` → compiled successfully, static pages generated.
- Slop scan: clean (the one `console.log` is the seed's structured `log()` helper, correct
  for a CLI; the test's `as never` is the standard boundary-mock cast).
- CSO: clean — no hardcoded secrets, all creds via `process.env`, `.env` gitignored + not
  staged, seed logs no secrets, deps current (supabase-js 2.106 / zod 4.4 / tsx 4.22).

## What the user must provide to unblock
1. Put real values in `.env` for `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
   `SUPABASE_SERVICE_ROLE_KEY`, plus `NEXT_PUBLIC_SUPABASE_URL` +
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` (the browser client reads the NEXT_PUBLIC_ pair).
2. Add `SUPABASE_DB_PASSWORD` to `.env` (Dashboard → Project Settings → Database →
   Connection) so DDL applies non-interactively, OR run
   `supabase link --project-ref <ref> && supabase db push` yourself.
3. After schema + buckets exist: `npm run seed`, then confirm `getFeed()` returns 5
   stories with HTTP-200 audio/poster URLs (the only DoD parts I could not verify).
