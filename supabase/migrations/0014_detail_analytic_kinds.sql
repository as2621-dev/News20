-- Migration 0014 — new analytic_kind + coverage_mode values (category-specific Detail)
--
-- Source of truth: plans/write-a-detailed-plan-squishy-bubble.md (Phase 1).
--
-- ADD-VALUE ONLY (no other DDL). `alter type … add value` cannot run inside a
-- transaction block alongside statements that USE the new value, so this file is
-- kept enum-only — mirrors 0011_subject_profile_kind.sql's precedent. The
-- stories-column ALTERs live in 0015.
--
-- WHY these kinds: the Detail page now renders a per-CATEGORY ordered triple of
-- panels instead of one segment-skinned analytic. Each new analytic_kind is one
-- panel a category needs:
--   what_we_know    — Breaking slot 2 (confirmed vs unconfirmed)
--   by_the_numbers  — Markets slot 3 (key figures as rows)
--   the_concept     — Tech slot 3 (explain the underlying principle)
--   stat_line       — Sport slot 2 (the event's box-score numbers)
--   recent_form     — Sport slot 3 (team/player recent record; background allowed)
--   source_context  — Sources slot 1 (the video/episode/gist) [used phase-5d+]
--   key_points      — Sources slot 2 [used phase-5d+]
--   implications    — Sources slot 3 [used phase-5d+]
-- And coverage_mode gains `reach_lite` — Breaking's Coverage variant: outlet
-- count + notable names only, NO momentum / who-broke-it.
--
-- `if not exists` keeps the file idempotent/re-runnable.

alter type analytic_kind add value if not exists 'what_we_know';
alter type analytic_kind add value if not exists 'by_the_numbers';
alter type analytic_kind add value if not exists 'the_concept';
alter type analytic_kind add value if not exists 'stat_line';
alter type analytic_kind add value if not exists 'recent_form';
alter type analytic_kind add value if not exists 'source_context';
alter type analytic_kind add value if not exists 'key_points';
alter type analytic_kind add value if not exists 'implications';

alter type coverage_mode add value if not exists 'reach_lite';
