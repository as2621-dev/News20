-- Migration 0028 — cluster sub-niche + description (root catalog v2)
--
-- Source of truth: plans/prd.md (2026-07-04, chat onboarding + source reels) +
-- the v2 seed catalog artifact (bd94cf30 — scripts/seed_catalog/data/root_catalog_v2.json).
-- The v2 catalog maps every X cluster 1:1 to a sub-niche within its root, and
-- carries a one-line editorial description shown in the onboarding cluster
-- checklist. 0022 shipped neither column; this adds both.
--
-- ⚠ ADDITIVE / forward-only — two nullable text columns, no defaults rewritten,
-- no rows touched. Reversible only by manual column drops.
--
-- cluster_subniche: the sub-niche display label in the root's vocabulary (e.g.
-- "Frontier labs & LLMs" under ai). Text, not an interests FK: the sub-niche
-- interest node is minted by the same seed pass, but clusters must not
-- cascade-break if taxonomy nodes are re-organised — the label is the stable
-- editorial key, resolved to a node at read time when needed.
alter table source_clusters add column if not exists cluster_subniche   text;
alter table source_clusters add column if not exists cluster_description text;
