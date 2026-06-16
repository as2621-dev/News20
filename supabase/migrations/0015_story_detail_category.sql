-- Migration 0015 — stories.story_detail_category + story_is_breaking
--
-- Source of truth: plans/write-a-detailed-plan-squishy-bubble.md (Phase 1).
--
-- ADDITIVE / forward-only. Separate file from 0014 because those `alter type …
-- add value` statements cannot share a transaction with table DDL.
--
-- story_detail_category: the 9-bucket key the Detail page reads to pick its panel
-- TEMPLATE (breaking | world | markets | tech | sport | culture | youtube |
-- podcasts | x). Resolved deterministically at enrichment time from the story's
-- feed_category (+ is_breaking). Plain `text` (not a new enum) keeps this additive
-- and reversible; the value set is validated Python-side (detail_templates.py) and
-- TS-side (detailTemplates.ts). Nullable: pre-migration stories carry NULL and the
-- UI null-guards to a single slot-0 panel until the next daily re-enrichment.
--
-- story_is_breaking: there is no breaking flag on a story today (breaking is only a
-- feed-placement tier). The Detail page needs a per-story signal to select the
-- Breaking template, derived at persist from the GDELT coverage census
-- (coverage_momentum == 'breaking'). DEFAULT false so existing rows are unaffected.

alter table stories add column story_detail_category text;
alter table stories add column story_is_breaking boolean not null default false;
