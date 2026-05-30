-- Migration 0001 — Content schema (Phase 1b SP1)
--
-- Source of truth: reference/supabase-schema.md §1–2 (content half only).
-- Builds the 3 content enums + 13 content tables the audio-first reel reads:
--   segments, outlets, anchors, stories, digests, caption_sentences,
--   detail_chunks, story_trust, story_timeline, story_sources,
--   suggested_questions, story_qa, story_topics.
-- User/auth tables (users, follows, saves, …) are DEFERRED to M3 — not here.
--
-- ⚠ Forward-only. Column names transcribed VERBATIM from supabase-schema.md.

-- ── Enums ──────────────────────────────────────────────────────────────────
create type bias_lean as enum ('left', 'center', 'right');
create type segment_slug as enum ('geopolitics', 'markets', 'tech', 'sport', 'wildcard');
create type anchor_speaker as enum ('ALEX', 'JORDAN');

-- ── segments ───────────────────────────────────────────────────────────────
create table segments (
  segment_slug        segment_slug primary key,
  segment_label       text     not null,
  segment_accent_hex  text     not null,
  segment_sort_order  smallint not null default 0
);

-- ── outlets ────────────────────────────────────────────────────────────────
create table outlets (
  outlet_id            uuid primary key default gen_random_uuid(),
  outlet_name          text      not null unique,
  outlet_bias_lean     bias_lean not null,
  outlet_bias_score    numeric,
  outlet_reliability   numeric,
  outlet_homepage_url  text,
  outlet_created_at    timestamptz not null default now()
);
create index idx_outlets_bias_lean on outlets (outlet_bias_lean);

-- ── anchors ────────────────────────────────────────────────────────────────
create table anchors (
  anchor_id            anchor_speaker primary key,
  anchor_display_name  text     not null,
  gemini_voice_id      text     not null,
  identity_color_hex   text     not null,
  anchor_sort_order    smallint not null default 0
);

-- ── stories ────────────────────────────────────────────────────────────────
create table stories (
  story_id                   text primary key,
  story_segment_slug         segment_slug not null references segments (segment_slug),
  story_headline             text not null,
  story_dek                  text not null,
  story_primary_outlet_id    uuid references outlets (outlet_id),
  story_primary_outlet_name  text,
  story_ambient_poster_url   text,
  story_first_reported_utc   timestamptz not null default now(),
  story_last_updated_utc     timestamptz not null default now(),
  story_published_label      text,
  story_key_figure_value     text,
  story_key_figure_label     text,
  story_outlet_count         integer not null default 0,
  story_blindspot_lean       bias_lean,
  story_blindspot_flag       boolean generated always as (story_blindspot_lean is not null) stored,
  story_created_at           timestamptz not null default now()
);
create index idx_stories_segment      on stories (story_segment_slug);
create index idx_stories_last_updated on stories (story_last_updated_utc desc);

-- ── digests (audio-first: audio + caption track, NOT an MP4) ────────────────
create table digests (
  digest_id                  uuid primary key default gen_random_uuid(),
  digest_story_id            text not null references stories (story_id) on delete cascade,
  digest_audio_url           text not null,
  digest_duration_ms         integer not null,
  digest_ambient_poster_url  text,
  digest_caption_track_url   text,
  digest_legacy_mp4_url      text,
  digest_is_current          boolean not null default true,
  digest_generated_utc       timestamptz not null default now()
);
create unique index uq_digests_current_per_story
  on digests (digest_story_id) where digest_is_current;

-- ── caption_sentences (the karaoke hero table) ──────────────────────────────
create table caption_sentences (
  caption_sentence_id  uuid primary key default gen_random_uuid(),
  caption_digest_id    uuid not null references digests (digest_id) on delete cascade,
  caption_story_id     text not null references stories (story_id) on delete cascade,
  sentence_index       smallint not null,
  anchor_speaker       anchor_speaker not null,
  sentence_text        text not null,
  highlight_keyword    text not null,
  sentence_start_ms    integer not null,
  sentence_end_ms      integer not null,
  word_tokens          jsonb not null,
  constraint uq_caption_sentence_order unique (caption_digest_id, sentence_index)
);
create index idx_caption_sentences_digest on caption_sentences (caption_digest_id, sentence_index);

-- ── detail_chunks ───────────────────────────────────────────────────────────
create table detail_chunks (
  detail_chunk_id  uuid primary key default gen_random_uuid(),
  detail_story_id  text not null references stories (story_id) on delete cascade,
  chunk_index      smallint not null,
  chunk_text       text not null,
  constraint uq_detail_chunk_order unique (detail_story_id, chunk_index)
);
create index idx_detail_chunks_story on detail_chunks (detail_story_id, chunk_index);

-- ── story_trust ─────────────────────────────────────────────────────────────
create table story_trust (
  story_trust_id         uuid primary key default gen_random_uuid(),
  trust_story_id         text not null unique references stories (story_id) on delete cascade,
  coverage_left_count    integer not null default 0,
  coverage_center_count  integer not null default 0,
  coverage_right_count   integer not null default 0,
  coverage_outlet_count  integer not null default 0,
  blindspot_lean         bias_lean,
  opposing_view_text     text
);

-- ── story_timeline ──────────────────────────────────────────────────────────
create table story_timeline (
  story_timeline_id     uuid primary key default gen_random_uuid(),
  timeline_story_id     text not null references stories (story_id) on delete cascade,
  timeline_event_index  smallint not null,
  timeline_when_label   text not null,
  timeline_event_at     timestamptz not null default now(),
  timeline_what_text    text not null,
  constraint uq_story_timeline_order unique (timeline_story_id, timeline_event_index)
);
create index idx_story_timeline_story_at on story_timeline (timeline_story_id, timeline_event_at desc);

-- ── story_sources ───────────────────────────────────────────────────────────
create table story_sources (
  story_source_id       uuid primary key default gen_random_uuid(),
  source_story_id       text not null references stories (story_id) on delete cascade,
  source_outlet_id      uuid references outlets (outlet_id),
  source_outlet_name    text not null,
  source_bias_lean      bias_lean,
  source_article_url    text,
  source_published_utc  timestamptz,
  source_is_citation    boolean not null default true,
  constraint uq_story_source unique (source_story_id, source_outlet_name)
);
create index idx_story_sources_story on story_sources (source_story_id);

-- ── suggested_questions ─────────────────────────────────────────────────────
create table suggested_questions (
  suggested_question_id  uuid primary key default gen_random_uuid(),
  question_story_id      text not null references stories (story_id) on delete cascade,
  question_index         smallint not null,
  question_text          text not null,
  constraint uq_suggested_question_order unique (question_story_id, question_index)
);

-- ── story_qa ────────────────────────────────────────────────────────────────
create table story_qa (
  story_qa_id               uuid primary key default gen_random_uuid(),
  qa_story_id               text not null references stories (story_id) on delete cascade,
  qa_question_text          text not null,
  qa_answer_text            text not null,
  qa_is_grounded            boolean not null default true,
  qa_source_kind            text not null default 'canned',
  qa_citation_outlet_names  text[] not null default '{}',
  qa_created_at             timestamptz not null default now(),
  constraint uq_story_qa unique (qa_story_id, qa_question_text)
);
create index idx_story_qa_story on story_qa (qa_story_id);

-- ── story_topics ────────────────────────────────────────────────────────────
create table story_topics (
  story_topic_id  uuid primary key default gen_random_uuid(),
  topic_story_id  text not null references stories (story_id) on delete cascade,
  topic_keyword   text not null,
  constraint uq_story_topic unique (topic_story_id, topic_keyword)
);
create index idx_story_topics_story on story_topics (topic_story_id);
