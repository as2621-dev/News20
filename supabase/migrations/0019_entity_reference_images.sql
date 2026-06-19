-- Migration 0019 — Canonical entity reference-image store (Phase 0c SP1)
--
-- Source of truth: plans/phase-0c-poster-identity-grounding.md (Sub-phase 1) +
-- reference/poster-pipeline.md (the appended "Canonical reference-image store"
-- section).
--
-- Context: the poster pipeline (agents/m0/) sources WHO a person is from the image
-- model's training prior, so a role story ("Fed chair", "G7 leader") renders the
-- former-but-famous incumbent instead of the person the story actually names. This
-- store holds VERIFIED, CURRENT canonical reference photos keyed by the resolved
-- person — the trusted face the image model is conditioned on (SP3 populates it,
-- SP4 conditions generation on it). The image model is never the source of truth
-- for identity; the story text supplies the name, this store supplies the face.
--
-- This migration is ADDITIVE and ISOLATED:
--   * It creates ONE new table (`entity_reference_images`) and ONE new public
--     Storage bucket (`entity-reference-images`). It DROPs / ALTERs nothing.
--   * It does NOT touch, reference, or FK to `entities` (migration 0007). The link
--     is by `entity_key` (a normalized resolved name) computed in code, NOT a DB FK
--     — resolution is demand-driven from story text, not the static registry.
--
-- ⚠ irreversible (live DB schema + new bucket). Authorized by the locked phase plan.
-- Additive only (no drops/destructive alters), so rollback is a manual drop of the
-- new table + bucket. Bucket creation is idempotent (on conflict); the table is a
-- first-apply create.

-- ── entity_reference_images — one row per resolved person (verified photo) ─────
-- entity_key is the NORMALIZED resolved person name (lowercased + trimmed, e.g.
-- 'donald trump') — the demand-driven lookup key SP3 upserts and SP4 reads. It is
-- UNIQUE: one canonical reference photo per resolved entity (a re-fetch upserts the
-- same row). reference_storage_path / reference_public_url point at the uploaded
-- object in the `entity-reference-images` bucket. verification_confidence (0–1) is
-- the Flash identity-verification score; valid_as_of is the story date the photo
-- was accepted as a current likeness for (staleness / refresh use SP3).
create table entity_reference_images (
  reference_id             uuid primary key default gen_random_uuid(),
  entity_key               text not null unique,
  entity_kind              text not null default 'person',
  reference_storage_path   text not null,
  reference_public_url     text not null,
  source_page_url          text,
  verified_at              timestamptz not null default now(),
  valid_as_of              date,
  verification_confidence  real,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);
-- The UNIQUE on entity_key already creates a btree index that serves the only access
-- pattern (point lookup by normalized name) — no extra index is added (matches the
-- "don't over-index" convention in 0007/0018, which only add indexes for access
-- patterns the unique/PK doesn't already cover).

-- ── RLS — public read, service-role-only write (mirrors `entities`/`story-posters`) ──
-- PUBLIC-READ reference data: an anon SELECT policy via `using (true)`, and NO
-- INSERT/UPDATE/DELETE policy — so only the service-role key (which bypasses RLS)
-- can write. This mirrors the `entities` registry (0007) and the content tables
-- (0002) exactly. The reference photos are non-sensitive shared catalog data.
alter table entity_reference_images enable row level security;
create policy entity_reference_images_public_read on entity_reference_images
  for select using (true);

-- ── Public Storage bucket (entity-reference-images) ───────────────────────────
-- public = true serves objects over the public CDN path without a signed URL
-- (same pattern SP4 / the uploader needs). Idempotent via on conflict, mirroring
-- the digest-audio / story-posters buckets in 0002.
insert into storage.buckets (id, name, public)
values ('entity-reference-images', 'entity-reference-images', true)
on conflict (id) do update set public = excluded.public;

-- Public-read object policy for the bucket (mirrors the story-posters policy in 0002).
create policy "public read entity-reference-images"
  on storage.objects for select using (bucket_id = 'entity-reference-images');

-- ── Apply + verify (IPv4 session pooler, port :6543) ──────────────────────────
-- Apply via the IPv4 session pooler (the transaction pooler on :5432 times out):
--   supabase db push --db-url "<session-pooler-url-on-:6543>"
-- Smoke queries after a fresh apply (expected: 0 rows, bucket listable):
--   select count(*) from entity_reference_images;        -- expected: 0
--   select id from storage.buckets where id = 'entity-reference-images';
