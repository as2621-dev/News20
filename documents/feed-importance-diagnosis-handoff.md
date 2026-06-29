# Handoff — Feed surfaces minor/random stories instead of the day's big news

**Date:** 2026-06-29
**Status:** Diagnosis complete, fix not started. Paste this into a fresh claude.ai/code session to continue.
**User profile under test:** ash@gmail.com (`b316800d-d67c-4e38-898b-ba67ca3a171d`) — the REAL profile, NOT ashesh.srivastava1234@gmail.com.

## The complaint
Feed reels are "small and random" — not the main story of the day. Originally suspected geopolitics; on inspection **geopolitics is fine and tech/markets is the disaster zone.**

## What we measured
Pulled ash's most recent feed (**feed_date 2026-06-22**, 30 reels) and graded each story by `story_outlet_count` (distinct covering outlets). Importance = `min(1.0, story_outlet_count/12)` per `agents/pipeline/produce_gate.py:75-100`.

| Pos | Category | Outlets | Importance | Headline |
|----|----|----|----|----|
| 1 | tech | 2 | 0.17 | OpenAI's Massive Spending Benefits Chipmaker Nvidia |
| 2 | tech | 2 | 0.17 | Cala Homes Teaches St Edburg's Students Construction Site Safety |
| 3 | markets | 2 | 0.17 | Cornell Study Advocates Risk-Based Food Safety |
| 4 | tech | 1 | 0.08 | Poland Launches Funding Program for Local Community Resilience |
| 5 | tech | 1 | 0.08 | Worcestershire on Demand Service Completes 80,000 Journeys |
| 6 | tech | 1 | 0.08 | Power Mech Projects Secures Rs 1,009 Crore JSW Order |
| 7 | tech | 1 | 0.08 | SpaceX Faces Five Major Rivals Challenging Its 2026 Dominance |
| 8 | tech | 1 | 0.08 | Relativity Space to Send NASA's Aeolus Orbiter to Mars in 2028 |
| 9 | tech | 1 | 0.08 | NASA Retired Space Shuttle Due to Cost, Safety, Slow Turnaround |
| 10 | tech | 1 | 0.08 | Markets Close Higher, Chip Stocks Lead, SpaceX Holds IPO Gains |
| 11 | markets | 12 | **1.00** | AI Spending Boom Expected to Keep US Inflation, Rates High |
| 12 | markets | 2 | 0.17 | SpaceX Now Worth $2.4T... But Here's Why I'm Still Buying Bitcoin |
| 13 | markets | 16 | **1.00** | Australian Inflation Expected to Edge Up in May |
| 14 | markets | 6 | 0.50 | DEA Allowed Fentanyl Pills to Reach New Mexico Streets |
| 15 | markets | 3 | 0.25 | Indian Equity Markets Open Higher Amid Lower Oil Prices |
| 16 | markets | 3 | 0.25 | Cherwell Council Approves £250,000 for District Economic Plans |
| 17 | geopolitics | 2 | 0.17 | Nvidia CEO Predicts Marvell Will Reach Trillion-Dollar Valuation |
| 18 | geopolitics | 9 | 0.75 | Accent Group Rejects Frasers Group's $390M Takeover Offer |
| 19 | geopolitics | 3 | 0.25 | MediSun Energy and Yanrun Form JV for Water Treatment |
| 20 | geopolitics | 2 | 0.17 | Qatar Gas Plant Explosion Injures Several People |
| 21 | sport | 7 | 0.58 | USTR Jamieson Greer Visits India to Advance Trade Deal |
| 22 | sport | 4 | 0.33 | US Trade Representative Greer Visits India for Trade Talks |
| 23 | sport | 4 | 0.33 | Army, ITBP Personnel Attend Yoga Camp at Shipki La Border |
| 24 | sport | 3 | 0.25 | Belgium Draws Iran 0-0 After Red Card in World Cup |
| 25 | sport | 3 | 0.25 | Allianz Care Unveils Health Cover for Australian Intl Workers |
| 26 | wildcard | 15 | **1.00** | "Toy Story 5" Achieves Year's Biggest Debut, Earning $160M |
| 27 | wildcard | 1 | 0.08 | New Discovery Could Reshape How We Understand Stonehenge |
| 28 | markets | 1 | 0.08 | Utah Regulators Impose Strict Conditions on Provo Canyon School |
| 29 | markets | 1 | 0.08 | China Approves New Five-Year Plan Prioritizing Quality Employment |
| 30 | wildcard (source) | 1 | 0.08 | Trump Crackdown on Anthropic Prompts Benefit Inquiry |

### Per-category summary
| Category | Stories | Avg outlets | ≤2 outlets |
|----|----|----|----|
| tech | 9 | 1.2 | 9 of 9 |
| markets | 9 | 5.1 | 4 of 9 |
| geopolitics | 4 | 4.0 | 2 of 4 |
| sport | 5 | 4.2 | 0 of 5 |
| wildcard | 3 | 5.7 | 2 of 3 |

**17 of 30 stories (57%) covered by ≤2 outlets. Only 3 stories all day were genuinely big (pos 11, 13, 26) — two got buried beneath ten minor tech stories.**

## Three root causes (in priority order)

1. **Ranking ignores importance.** Feed order is driven by interest/keyword match, not story magnitude. `feed_score` does not track importance — pos 4–10 carry feed_score 0.72–1.02 at importance 0.08, while the 12- and 16-outlet stories sit at pos 11/13. Formula: `Score = (Affinity×DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2` (`agents/pipeline/stages/ranking.py:9-11`). Importance's 0.3 weight is too weak to surface big stories.

2. **Category mis-assignment (clustering/segmentation bug).** "geopolitics" slots held Nvidia + a corporate takeover + a water-treatment JV; "sport" slots held a US–India trade deal (twice), a yoga camp, and health insurance — only 1 of 5 sport was real sport. Look at `agents/pipeline/feed_assembly.py` + the M3b assign-or-spawn clustering engine for where `story_segment_slug` is assigned.

3. **SUSPECTED measurement bug — verify FIRST (potential one-line, high-leverage fix).** Importance reads `stories.story_outlet_count` (range 1–16, usually 1–2). But `story_trust.coverage_outlet_count` for the SAME stories runs **78–214**. If importance should key off the coverage census instead of the small column, every story currently looks minor to the system — which would explain the whole problem. Confirm which column is the intended magnitude signal before doing anything else.

## On the user's original idea
User proposed adding major-outlet NAMES (Reuters/BBC/CNN) as tags in the GDELT search query. Finding: ingestion matches topic anchor-terms against GDELT title+entity fields, NOT against publisher — so outlet names in the query wouldn't filter by source. The system ALREADY counts outlet coverage; the fix is to USE that count for ranking (and read the right column), not to change the query. User's instinct ("trust widely-covered stories") is correct; the lever is different and hits all categories at once.

## Next steps
1. Verify root cause #3 (which outlet-count column importance should read). Check `agents/pipeline/produce_gate.py:75-100` vs `story_trust.coverage_outlet_count`.
2. If confirmed, fix the column → re-score → re-assemble ash's feed and re-pull this table to confirm big stories rise.
3. Then address #1 (raise importance weight / add an importance floor for category leads) and #2 (category mis-assignment in clustering).
4. Suggested entry: `/rca` on "low-importance off-category stories dominate the feed."

## How to re-run the diagnostic
Re-run script: `scratchpad/feed_report.py` (session pooler `aws-1-us-east-1.pooler.supabase.com:6543`, `SUPABASE_DB_URL` from `.env`, asyncpg, SELECT-only). NOTE: the web sandbox won't have `.env` secrets — add `SUPABASE_DB_URL` there to re-run live DB queries, or skip and reason from this table.

Key files: `agents/ingestion/adapters/gdelt_bigquery.py`, `agents/ingestion/interest_keyed_pipeline.py`, `agents/ingestion/dedup.py`, `agents/pipeline/produce_gate.py`, `agents/pipeline/stages/ranking.py`, `agents/pipeline/stages/coverage_gdelt.py`, `agents/pipeline/feed_assembly.py`.
