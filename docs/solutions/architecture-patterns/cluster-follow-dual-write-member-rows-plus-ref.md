---
title: A cluster FOLLOW is a dual write — member rows for content + a per-user cluster REF for shared scheduling
tags: [source-clusters, user-content-sources, onboarding, x-sweep, follow-fanout, terminal-persist]
problem_type: pattern
symptoms: following an X "cluster" in onboarding wrote the members into user_content_sources
  but the shared once-daily sweep never picked the cluster up; or theme reels had no cluster to
  attribute back to; or a cluster follow looked identical to N unrelated member follows
root_cause: a cluster follow has TWO consumers with different granularity — per-member content
  ingestion vs per-cluster shared scheduling — and only the member half was being persisted
date: 2026-07-05
---

Built the in-chat X CLUSTERS picker (issue #20, `src/components/onboarding/XClusterPicker.tsx`
+ `src/lib/onboardingTerminal.ts::persistSourceFollows`). Following a cluster is NOT one write —
it is TWO, because a cluster follow is read by two subsystems at different granularity:

**1. Member expansion → `user_content_sources` / `user_personalities`.** The cluster's members
(individual X accounts / personalities) become ordinary per-source follow rows via
`commitClusterFollowSet` (→ `followSource` / `followPersonality`). This is what per-source
ingestion reads. Deduped across clusters (a member in two selected clusters = one row).

**2. Cluster REF → `user_source_clusters` (migration 0033).** A per-user link to the cluster
ITSELF via `commitUserClusterFollows`. This is what the SHARED once-daily X sweep
(`x_cluster_sweeps`, issue #23) schedules on — "which clusters have ≥1 follower" — and what
theme-of-the-day reels attribute back to. Without it, a cluster follow is invisible to the
sweeper: the members are followed but the cluster is never swept, so no themes are produced.

**If you catch yourself writing only the member rows for a cluster follow, you've half-shipped it.**
The member rows give the user content per-source; the ref gives the shared pipeline something to
schedule. They serve different readers, so both must land.

**Order + failure:** write member rows BEFORE the ref, both as idempotent upserts, inside the one
terminal-persist call (which rejects — no onboarding-gate stamp — on any failure, so a retry
converges). If only one half could land, member follows are the safer half: the user still gets
content; only cluster-level scheduling is missing until the retry. See the sibling pattern
`postgrest-clean-replace-destructive-delete-last.md` for the destructive-last ordering this slots into.

**Carrying the id without a re-read:** the picker resolves members + `cluster_id` when it loads the
catalog, then the SELECTION travels on the terminal payload (`InterviewClusterPick`) — the persist
never re-reads the catalog. `ResolvedCluster.cluster_id` was added as OPTIONAL only so existing
resolver fixtures compile; the resolver always sets it, and `resolveClusterPicks` skips any cluster
missing it rather than writing a ref-less follow.
