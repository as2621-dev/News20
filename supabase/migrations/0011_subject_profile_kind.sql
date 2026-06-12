-- 0011_subject_profile_kind.sql
--
-- Adds the 'subject_profile' value to the analytic_kind enum (0004): the new
-- PROFILE second-analytic tab — a short background profile of the story's
-- central person/organization. Product decision 2026-06-12: MARKET IMPACT now
-- exists only for markets + tech stories; geopolitics/sport/wildcard get
-- PROFILE instead (mapping lives in agents/pipeline/stages/detail_enrichment.py
-- _SEGMENT_TO_ANALYTIC_KIND — deterministic in code, never the LLM).
--
-- ALTER TYPE ... ADD VALUE is non-transactional in Postgres but idempotent via
-- IF NOT EXISTS; existing story_analytics rows keep their old kinds until the
-- next enrichment run rewrites them.

alter type analytic_kind add value if not exists 'subject_profile';
