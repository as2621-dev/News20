-- Migration 0006 — Story URL aliases (cross-day produce-once identity)
--
-- Source of truth: the reconciliation plan D2 (fix cross-day double-dipping).
-- PROBLEM: `canonical_story_id` is derived in agents/ingestion/dedup.py from the
-- normalized URL of the EARLIEST-published cluster member. That id is NOT stable
-- across days — when a multi-day story re-clusters tomorrow with a different
-- earliest member, it gets a NEW id, slips past the produce-once gate
-- (digests.digest_is_current keyed on story_id), and is re-produced (re-TTS /
-- re-poster / re-enrich) AND re-allocated — so the user sees the same event again
-- (the §3.8 don't-repeat keys on feed_story_id, which now differs).
--
-- FIX: persist writes one alias row per covering-outlet URL of every produced
-- story. A later batch normalizes its cluster's member URLs and, if ANY is already
-- aliased, REUSES that story_id — so the event keeps one id across days
-- (produce-once holds; no duplicate stories/digests; don't-repeat works).
--
-- ADDITIVE / forward-only. Applies cleanly ON TOP OF 0001 (stories). NO DROPs, no
-- destructive ALTERs. Reversible by drop. Column naming mirrors the 0003/0005
-- verbose, entity-prefixed convention.
-- alias_story_id FK type MUST match stories.story_id, which is `text`
-- (0001_content_schema.sql line 48) — NOT uuid.

-- ── story_url_aliases (every seen outlet URL → its canonical story id) ────────
create table story_url_aliases (
  alias_normalized_url  text primary key,
  alias_story_id        text not null references stories (story_id) on delete cascade,
  alias_first_seen_utc  timestamptz not null default now()
);
-- Access patterns: resolve-by-url (the ingest batch's single .in_() lookup, the
-- PK already serves this) and cascade-by-story on delete (the by-story index).
create index idx_story_url_aliases_story on story_url_aliases (alias_story_id);

-- ── RLS — service-role only (mirrors the content tables, 0002) ────────────────
-- Aliases are an internal ingestion-identity aid; clients never read or write
-- them. With RLS enabled and NO policy, the anon/auth roles get zero rows; the
-- service-role worker (which bypasses RLS) is the only reader/writer.
alter table story_url_aliases enable row level security;
