# Phase 1d: Interest-keyed personalized daily pipeline → per-user feed

**Milestone:** M1 — Personalized audio-first karaoke reel MVP
**Status:** Not started
**Estimated effort:** L

## Goal
A **Trigger.dev v4 daily pipeline** that (1) nudges interest weights from yesterday's engagement signals, (2) ingests real news **per active interest** (the distinct union of all users' followed interest nodes) into a **deduped story pool** tagged to interest nodes **and their ancestors**, produces an audio digest **once** per qualifying story, then (3) **scores stories per user** and (4) assembles a ~30-slot **per-user `daily_feeds`** — so each user opens a feed that feels built for them, while every reel is still produced at most once and shared across everyone whose interest it serves.

## Re-scope context (what changed and why)
The previous plan ingested broadly into the **5 fixed segments**, ranked one **global** ~20–30-story feed, and deferred all personalization to M3. That cannot serve niche followers ("Venezuelan oil prices", "Meta stock", "Arsenal" sit 2–3 levels below a segment). The owner re-scoped M1 to personalize end-to-end. The unit of ingestion is now the **interest** (granular node with a search query), not the segment; the unit of distribution is **interest → all subscribed users** via a deduped pool. The full algorithm (scoring, fallback tree, allocation, profile-update) lives in **`reference/ranking-spec.md`** — this phase implements it. Requires Phase 1e (interests + profiles + migration 0003) to exist first.

## Context the sub-agents need
- **Heavy reuse** (`reference/reuse-map.md`): ingestion `adapters/base.py`, `feed_utils.py`, `dedup.py` = **PORT**; `ranking.py` = **ADAPT** (becomes the per-user heuristic scorer + fallback tree, *not* segment weighting); `scripting.py` = **ADAPT** (single-source two-host); `verification.py` = **PORT**; `agents/memory/{player_signals.py,session_processor.py}` = **ADAPT** (the profile-update loop); LLM client/prompts/models = **PORT**. Donor at `~/TLDW-Phase2/tldw/voice-agent-dashboard/`; read before porting (Rule 8); don't touch TLDW `_legacy/`.
- **Already built in M0 (reuse, don't rebuild):** `agents/voice/gemini_tts.py` (anchor-duo TTS), `agents/pipeline/stages/forced_alignment.py` (time-slice caption path), the poster generator under `agents/m0/` (Gemini `gemini-3-pro-image-preview`).
- **Persist target = `reference/supabase-schema.md`** + the Phase-1e migration 0003 tables (`story_interests`, `daily_feeds`, `interest_search_query`, `profile_is_strict`). Caption JSON → `caption_sentences.word_tokens`.
- **Ranking contract = `reference/ranking-spec.md`** (Score formula, threshold `T`, fallback tree, 30-slot allocation, profile-update bounds). Do not re-derive — implement it.
- **Locked constraints:** single-source scripts (Decision #4) + the `verification` hallucination guardrail (Decision #5); Gemini TTS pins in `reference/stack-notes.md`; Trigger.dev **v4 only** (`schedules.task`/`task`/`batchTrigger` — **never** `client.defineJob`).
- **Caption timing:** reuse M0's time-slice path for M1 (master-plan Open Q7); real forced alignment is a flagged future upgrade.
- **Precompute, not RPC:** ranking/allocation runs in this batch and writes `daily_feeds`; the client read is a trivial indexed select (`ranking-spec.md` §0/§5).

## Sub-phases

### Sub-phase 1: Interest-keyed ingestion + dedup pool + ancestor tagging
- **Files touched:** `agents/ingestion/adapters/{base.py,feed_utils.py}` (PORT), `agents/ingestion/dedup.py` (PORT), `agents/ingestion/adapters/newsapi.py` (NEW — ≥1 real adapter against `base.py`), `agents/ingestion/interest_keyed_pipeline.py` (NEW/ADAPT — build the active-interest set, fan out search per interest), `agents/ingestion/ancestor_tagging.py` (NEW), `tests/agents/ingestion/{test_dedup.py,test_newsapi_adapter.py,test_ancestor_tagging.py}` (mock HTTP).
- **What ships:** the **active-interest set** = distinct `user_interest_profile.profile_interest_id` across all users → each interest's `interest_search_query`; news search per interest; cross-source dedup into a single canonical story pool with outlet attribution (feeds the outlet-count/trust numbers); each story tagged to its matched interest node **and all ancestors** into `story_interests` with `story_interest_match_depth` (0 leaf / 1 parent / 2 grandparent).
- **Definition of done:** running ingestion against a **mocked** feed returns typed candidate stories; `dedup` merges near-duplicate items and counts covering outlets; a story matched at leaf `sport.soccer.arsenal` writes `story_interests` rows for **Arsenal (depth 0)**, **Soccer (depth 1)**, and **Sport (depth 2)** with correct `match_depth`; the active-interest set is empty-safe (fails loud with `fix_suggestion` if no profiles exist). Unit tests assert adapter parsing + dedup clustering + ancestor tagging (mocked HTTP — no live key in tests). ⚠ live external API when run for real.
- **Dependencies:** Phase 1e (interests seeded with search queries + ≥1 `user_interest_profile`).

### Sub-phase 2: Produce-once gate → single-source script + verification
- **Files touched:** `agents/pipeline/produce_gate.py` (NEW — produce only stories that serve ≥1 interest, clear the importance/freshness floor, and **lack a current digest**), `agents/pipeline/stages/scripting.py` (ADAPT — one source article → ~50–55 s two-host ALEX/JORDAN dialogue, single-source), `agents/pipeline/stages/verification.py` (PORT — guardrail), `agents/pipeline/{llm_clients,prompts,models,json_utils}.py` (PORT), `tests/agents/pipeline/{test_produce_gate.py,test_scripting.py,test_verification.py}`.
- **What ships:** a gate that keeps generation cost down (skip stories with an existing `digest_is_current` digest; skip stories serving zero active interests) + a scripting stage producing a speaker-tagged, single-source, length-bounded digest script that passes the verification guardrail.
- **Definition of done:** unit tests (mocked LLM) assert: the produce-gate **skips** a story that already has a current digest and **skips** a story with zero `story_interests`; scripting output is speaker-tagged (ALEX/JORDAN), within the ~140-word/55 s budget, and constrained to the single source; `verification` flags an injected out-of-source claim. Ruff passes; agent files < 500 LoC.
- **Dependencies:** Sub-phase 1.

### Sub-phase 3: Per-user scoring + fallback tree + orchestrator/persist
- **Files touched:** `agents/pipeline/stages/ranking.py` (ADAPT — the `Score = (Affinity×DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2` scorer + the leaf→parent→grandparent fallback with strict-ceiling/`Score≥T` stop), `agents/pipeline/orchestrator.py` (ADAPT — chain `script → TTS (reuse agents/voice/gemini_tts) → caption-timing (reuse forced_alignment) → poster (reuse agents/m0) → persist`), `agents/pipeline/persist.py` (NEW — `supabase-py` service-role writer: insert `story`/`digest`/`caption_sentences`/`detail_chunks`/`story_trust`/`story_sources`/`suggested_questions` + `story_interests` + upload audio/poster, mapping to `supabase-schema.md`), `tests/agents/pipeline/{test_ranking.py,test_fallback_tree.py,test_persist.py,test_orchestrator.py}`.
- **What ships:** the per-user heuristic scorer + fallback candidate generation (`ranking-spec.md` §1–2), and the per-story orchestrator that turns one gated story into a persisted, playable digest.
- **Definition of done:** unit tests assert affinity-dominant ordering (a small niche-followed story outscores a generic broad one on the Score math); the fallback climbs leaf→parent only when no leaf story clears `Score ≥ T` and **stops at a `strict` interest** (no upward broadening); the orchestrator on one fixture story produces a Supabase story with a digest (audio URL resolves HTTP 200), `caption_sentences` (`word_tokens` with timings, one highlight/sentence), `poster_url` resolves, and `story_interests` rows; a unit test asserts the caption-JSON→`caption_sentences` mapping is lossless. ⚠ writes data + uploads + calls paid TTS/image APIs when run for real.
- **Dependencies:** Sub-phase 2, **Phase 1b** (content schema/storage), **Phase 1e** (0003 schema).

### Sub-phase 4: Profile-update job + per-user allocation → `daily_feeds` + Trigger.dev fan-out
- **Files touched:** `agents/memory/session_processor.py` (ADAPT — aggregate `player_signals` → bounded, slow-decay nudges to `user_interest_profile.profile_weight`), `agents/memory/player_signals.py` (ADAPT), `agents/pipeline/feed_assembly.py` (NEW — the ~30-slot per-user allocator: breaking tier + weight-proportional buckets + floor-1 + ~40% cap + redistribute + ~10% exploration + strict-disables-fallback + don't-repeat-from-prior-`daily_feeds`, writing `daily_feeds` rows), `trigger.config.ts` (v4), `trigger/dailyPersonalizedFeed.ts` (`schedules.task` → update-weights → ingest+tag → produce → score → allocate, fanning per-story production via `batchTrigger`), `tests/agents/{memory/test_session_processor.py,pipeline/test_feed_assembly.py}`, `tests/trigger/*`.
- **What ships:** the profile-update loop (runs **first**, so today's feed reflects yesterday's behavior), the per-user allocator writing `daily_feeds`, and the scheduled daily orchestration end-to-end.
- **Definition of done:** the aggregation job over seeded signals **raises** an engaged interest's weight and **lowers** an ignored one **within bounds** (no over-narrowing past the floor/decay — asserts on resulting `user_interest_profile` rows, testing the prioritization logic not just the insert, Rule 9); the allocator fills ≤30 slots honoring breaking-preempt + ~40% cap + floor-1 + ~10% exploration + strict-disables-exploration (asserted on `daily_feeds` rows for **2 seeded users with different interests**); one manual batch run produces **≥2 distinct** per-user `daily_feeds` (floor ≥10 produced digests) ordered `01..N`. Uses `schedules.task`/`batchTrigger` only (**never** `client.defineJob`). ⚠ scheduled cron + paid API calls + data writes — run manually before enabling the schedule.
- **Dependencies:** Sub-phase 3.

## Phase-level definition of done
One manual batch run: nudges interest weights from seeded `player_signals` → ingests real news **per active interest** into a deduped, ancestor-tagged story pool → produces digests **once** for qualifying stories → scores stories per user → writes a ~30-slot **per-user `daily_feeds`** for **≥2 seeded users whose feeds are demonstrably different**, with a `strict` user showing no upward-fallback/exploration rows and an ancestor-tagged niche story reaching a broad follower. **Automated:** ingestion/dedup/ancestor-tagging, produce-gate, scorer + fallback stop-conditions, profile-update bounds/decay, allocator invariants, and persist-mapping unit tests (mocked external services) green; **one manual end-to-end batch run** produces the per-user feeds. **Floor:** ≥10 produced digests; ≥2 distinct per-user feeds. ⚠ contains a scheduled Trigger.dev task + paid TTS/image/news API calls + data writes.

## Out of scope
- Onboarding / auth / the interest profile itself (Phase 1e writes it; this phase reads it).
- The reel's per-user read of `daily_feeds` (Phase 1c SP4).
- Voice-sourced profile updates (M3 3c) and follow/what's-new (M3 3d).
- An always-on "world tier" — superseded by the breaking tier within followed interests (`ranking-spec.md` §6).
- A dedicated "seen" table — don't-repeat is derived from prior `daily_feeds` + `player_signals` (`ranking-spec.md` §3.8).
- A data-feed lane for price/score interests (news-search only for v1; flagged).
- Image sourcing beyond the reused M0 poster generator; YouTube/podcast ingestion (later phase). Remotion share-export (demoted, deferred).

## Open questions
1. **Caption timing source** (master-plan Open Q7): reuse M0's time-slice path or invest in real forced alignment? Recommend time-slice for M1, flag the upgrade.
2. **Paid-API budget + keys:** interest-keyed ingestion multiplies queries by the active-interest count. NewsAPI / Gemini TTS / Gemini image quotas — confirm budget before enabling the schedule. The owner has accepted ~$0.16/story unique-generation cost for the first 10–50 users; dedup + produce-once keeps real cost below the worst case.
3. **Execution host** (master-plan Open Q5): Trigger.dev task vs the FastAPI worker for the heavy Python TTS/poster steps — decide where they run.
4. **Allocation tuning:** the §3 constants (`N≈30`, ~4 breaking slots, ~40% cap, ~10% exploration, weights α/β/γ, threshold `T`) are first-draft — confirm via the 2-user manual run.

## Self-critique

**Product lens:** PASS. This is what makes the re-scoped M1 "true" — a per-user feed that feels built for them (the owner's hard requirement), not a global briefing. Single-source + verification keeps the zero-hallucination guardrail (Decisions #4/#5). Produce-once + dedup pool preserves the one-canonical-asset-per-story economics (Decision #3) even while feeds are personalized — reuse when interests overlap, unique only when they don't.

**Engineering lens:** PASS. Every stage maps to a `reuse-map.md` decision (ingestion PORT, ranking/scripting/signals ADAPT, TTS/align/poster reuse M0, Trigger.dev v4). The algorithm is externalized to `ranking-spec.md` and *implemented* here (one source of truth, not re-derived per sub-phase). DoDs assert on **outputs** (score ordering, allocator invariants, weight nudges), not inserts alone (Rule 9). Sub-phases are disjoint: ingest/tag ≠ produce-gate/script ≠ score/orchestrate/persist ≠ update/allocate/schedule. Precompute-to-`daily_feeds` (not a live RPC) is the right call for a static-export client.

**Risk lens:** PASS with flags. ⚠ side-effecting/irreversible: SP1 hits a live news API, SP3 writes data + paid TTS/image, SP4 registers a scheduled cron + writes `daily_feeds` — all flagged; tests mock externals and the real run is a deliberate manual one before enabling the schedule. **Over-narrowing risk** (the brief's caution) is mitigated in SP4 by bounded/decayed nudges + floor-1 + 40% cap + 10% exploration, and *tested* on the 2-user run. **Empty-feed risk** for sparse/strict profiles handled by breaking-tier fill + redistribution + recency fallback. Dependency ordering: SP1 needs Phase 1e's profiles/queries; flagged. Painting-into-a-corner: SP1→4 leaves per-user `daily_feeds` rows Phase 1c SP4 reads directly.

**Irreversible sub-phases:** SP3 (data writes + paid API calls), SP4 (scheduled cron + paid API calls + data writes); SP1 has live-API side-effects when run for real.
