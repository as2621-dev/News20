# Phase 1b — SP2: Storage buckets + public-read RLS

**STATUS: APPLIED + VERIFIED against hosted DB. DoD: PASS.**

Migration `0002_storage_and_rls.sql` was applied by the orchestrator. This report
records the independent verification (no re-apply, no commit). Checks ran via the IPv4
session pooler (psql, owner role — structural only) and the anon/service-role keys via
PostgREST + Storage REST (the real client path, RLS enforced).

## Implemented
- `supabase/migrations/0002_storage_and_rls.sql`:
  - `enable row level security` on all 13 content tables.
  - An anon `for select using (true)` (public-read) policy on each. No write policy →
    only the service-role key (bypasses RLS) can write.
  - Two public Storage buckets — `digest-audio`, `story-posters` — created idempotently.
  - Public-read object policies on `storage.objects` for both `bucket_id`s.

## Verification results

### 1. Buckets — PASS
Both exist and are public:
- `digest-audio | public=true`
- `story-posters | public=true`

### 2. RLS enabled — PASS (13/13)
`pg_class.relrowsecurity = true` on all 13 content tables: segments, outlets, anchors,
stories, digests, caption_sentences, detail_chunks, story_trust, story_timeline,
story_sources, suggested_questions, story_qa, story_topics.

### 3. Policy audit — PASS
- **Read policy present:** 13/13 tables have a `SELECT` policy (`<table>_public_read`).
- **No write policy:** 0 INSERT/UPDATE/DELETE/ALL policies on any content table.
- **Storage:** `storage.objects` has 2 SELECT policies (`public read digest-audio`,
  `public read story-posters`); no write policy.

### 4. Functional anon allow/deny (the key assertion) — PASS
- **anon SELECT** on `stories` → **HTTP 200**, body `[]` (table empty pre-seed; "returns
  rows" is satisfied post-seed — SP3 seeds 5 rows). Read allowed = PASS.
- **anon INSERT** on `stories` (valid required columns: `story_id`, `story_segment_slug`,
  `story_headline`, `story_dek`) → **HTTP 401**, Postgres `42501` "new row violates
  row-level security policy for table \"stories\"". Confirmed nothing was written
  (follow-up anon SELECT for the probe id returned `[]`). **Write denied by RLS = PASS.**
  - Note: the `anon` role still holds Supabase's default table-level INSERT/UPDATE/DELETE
    GRANTs, so the denial rests entirely on RLS (no INSERT policy). The 401/42501 above
    confirms RLS is the active gate — this is the security-critical result.

### 5. Public object readability — PASS (definitive, with cleanup)
Uploaded a throwaway text object to `story-posters` via the service-role Storage API
(HTTP 200), read it via the public URL `/storage/v1/object/public/story-posters/<obj>`
with **no auth header** → **HTTP 200** with correct body, then deleted it (HTTP 200) and
confirmed it 404/400s afterward. Public CDN path serves bucket objects without a signed URL.

## Definition of done — PASS
- [x] Both buckets exist and are public.
- [x] anon-key SELECT on `stories` succeeds (200; `[]` pre-seed — rows confirmed in SP3).
- [x] anon INSERT on `stories` rejected by RLS (401 / 42501). **Asserted.**
- [x] A stored object is publicly readable via its URL (200, verified with throwaway object).

## Concerns for orchestrator
- No anon WRITE path found: anon INSERT is RLS-denied (401/42501) despite default table
  GRANTs. UPDATE/DELETE are likewise policy-less and would be denied the same way.
- "returns rows" / seeded-object public-URL 200s are fully exercised post-seed in SP3/SP4;
  pre-seed the table is empty by design. Not a SP2 failure.
