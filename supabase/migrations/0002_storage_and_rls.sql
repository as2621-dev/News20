-- Migration 0002 — Storage buckets + public-read RLS (Phase 1b SP2)
--
-- Source of truth: reference/supabase-schema.md §6 + phase file SP2.
-- Content is public-read in M1 (anonymous passive reel; no auth until M3):
--   1. Enable RLS on all 13 content tables.
--   2. Add an anon SELECT (public-read) policy on each. No INSERT/UPDATE/DELETE
--      policy → only the service-role key (which bypasses RLS) can write.
--   3. Create the two public Storage buckets: digest-audio, story-posters.
--   4. Add public-read object policies on both buckets.
--
-- ⚠ Forward-only. Depends on 0001. Bucket creation is idempotent (on conflict);
-- RLS/policies are first-apply.

-- ── 1 + 2. RLS + anon SELECT on every content table ─────────────────────────
alter table segments            enable row level security;
alter table outlets             enable row level security;
alter table anchors             enable row level security;
alter table stories             enable row level security;
alter table digests             enable row level security;
alter table caption_sentences   enable row level security;
alter table detail_chunks       enable row level security;
alter table story_trust         enable row level security;
alter table story_timeline      enable row level security;
alter table story_sources       enable row level security;
alter table suggested_questions enable row level security;
alter table story_qa            enable row level security;
alter table story_topics        enable row level security;

create policy segments_public_read            on segments            for select using (true);
create policy outlets_public_read             on outlets             for select using (true);
create policy anchors_public_read             on anchors             for select using (true);
create policy stories_public_read             on stories             for select using (true);
create policy digests_public_read             on digests             for select using (true);
create policy caption_sentences_public_read   on caption_sentences   for select using (true);
create policy detail_chunks_public_read       on detail_chunks       for select using (true);
create policy story_trust_public_read         on story_trust         for select using (true);
create policy story_timeline_public_read      on story_timeline      for select using (true);
create policy story_sources_public_read       on story_sources       for select using (true);
create policy suggested_questions_public_read on suggested_questions for select using (true);
create policy story_qa_public_read            on story_qa            for select using (true);
create policy story_topics_public_read        on story_topics        for select using (true);

-- ── 3. Public Storage buckets (audio + posters) ─────────────────────────────
-- public = true serves objects over the public CDN path without a signed URL.
insert into storage.buckets (id, name, public)
values ('digest-audio', 'digest-audio', true)
on conflict (id) do update set public = excluded.public;

insert into storage.buckets (id, name, public)
values ('story-posters', 'story-posters', true)
on conflict (id) do update set public = excluded.public;

-- ── 4. Public-read object policies for both buckets ─────────────────────────
create policy "public read digest-audio"
  on storage.objects for select using (bucket_id = 'digest-audio');
create policy "public read story-posters"
  on storage.objects for select using (bucket_id = 'story-posters');
