-- 0016_content_sources_user_added_write.sql
--
-- Fix: the "add a source" feature (Sources · "What you follow" → AddSourceSearch)
-- was silently broken. `content_sources` (migration 0009) enables RLS with ONLY a
-- public-read policy and NO write policy, on the assumption that the curated
-- catalog is seeded exclusively by the service-role key. But the user-add flow
-- (`upsertUserAddedSource` in src/lib/sources.ts) runs in the BROWSER under the
-- authenticated anon-key JWT and must anchor a non-curated `content_sources` row
-- (`is_curated = false`) before it can follow it via `user_content_sources`.
-- With no INSERT/UPDATE policy, that upsert was rejected by RLS, so NOTHING the
-- user typed (YouTube / podcast / X / person) ever appeared — while interests,
-- which write to the owner-all `user_interest_profile`, worked fine.
--
-- This migration grants authenticated users INSERT/UPDATE on content_sources, but
-- ONLY for user-added rows (`is_curated = false`). The curated catalog stays
-- service-role-only: a user can neither create a curated row nor flip an existing
-- row's curation (both predicates pin `is_curated = false`). Public read is
-- unchanged. Mirrors the WITH CHECK pinning style of the 0009 owner-all policies.

-- INSERT: an authenticated user may add a NON-curated source row (the anchor for
-- their follow). They cannot insert a curated row (is_curated must be false).
create policy content_sources_user_added_insert on content_sources
  for insert to authenticated
  with check (is_curated = false);

-- UPDATE: an authenticated user may update a NON-curated row (the upsert's
-- ON CONFLICT path, e.g. refreshing a pending source's name) but can neither
-- target a curated row (USING) nor promote/demote curation (WITH CHECK). A typed
-- add whose external_id collides with a CURATED row is therefore rejected by RLS
-- rather than silently demoting the catalog entry — the honest, safe failure.
create policy content_sources_user_added_update on content_sources
  for update to authenticated
  using (is_curated = false)
  with check (is_curated = false);
