# Phase 6: Community-signal research agent

**Milestone:** M6 — Discovery agent & learned ordering
**Status:** Not started
**Estimated effort:** L

## Goal
A scheduled agent keeps per-archetype source lists **fresh** by mining community signals — crawling Reddit threads, X conversations, podcast directories, and forums to find who each niche actually recommends — then resolving and upserting newly-rising sources into the catalog **idempotently** without clobbering curated entries, feeding the existing 5c matcher.

## Why this phase exists
Master plan Decision #12 names this an explicit **build-fresh** gap: TL;DW's recommendations come from a hand-curated catalog + LLM enumeration, **not** community crawling. Spec §3.2 wants the catalog to catch rising voices without manual curation. This agent writes straight into the Phase 5b `content_sources`/`personalities` schema and reuses the 5c matcher — so no UI changes are needed, only fresher data.

## Context the sub-agents need
- **Writes into Phase 5b schema:** `content_sources` (`personas`, `topic_tags`, `popularity_score`, unique `(content_source_type, external_id)`) and `personalities`. Reuses 5b resolvers (`scripts/seed_catalog/youtube_resolve.py`, `itunes_resolve.py`) + 5c's X resolver.
- **Donor:** only the **cadence pattern** — `trigger/refresh-recommendation-seeds.ts` (quarterly LLM refresh). The crawl/extract logic is new (`reference/sources-reuse-map.md` §6 build-fresh checklist).
- **Stack:** Python agent under `agents/discovery/` (new module), following the project's agent structure (`tools`/`prompts`/`models`/`dependencies` discipline, CLAUDE.md §3). HTTP via `httpx`; LLM via the existing `agents/pipeline/llm_clients.py`. Server-side only.
- **Rule 5:** use the LLM only for extraction/judgment (who's being recommended, why); use **code** for dedup, frequency counting, and ranking.
- **Archetypes:** the 12 (or `/cmo`-locked) archetypes from `reference/archetypes.md` define the niches to mine.

## Sub-phases

### Sub-phase 1: Community-signal collectors
- **Files touched:** `agents/discovery/__init__.py`, `agents/discovery/collectors.py`, `agents/discovery/models.py`, `agents/discovery/dependencies.py`.
- **What ships:** per-source collectors (Reddit threads, X conversations, podcast directories, forums) that, given a niche/query, return raw `CandidateMention` Pydantic records (raw text, platform, url, observed_at) — each collector a pure async function over `httpx`, with typed inputs/outputs and structured logging.
- **Definition of done:** each collector returns typed `CandidateMention`s for a niche query (HTTP **mocked**); a failing upstream logs a structured error with `fix_suggestion` and returns `[]` (no crash); no secrets logged. Pytest with mocked `httpx` (happy + failure + empty per CLAUDE.md minimum coverage).
- **Dependencies:** none (new module).

### Sub-phase 2: LLM extraction + code-side ranking
- **Files touched:** `agents/discovery/extract.py`, `agents/discovery/prompts.py`, `agents/discovery/models.py` (extend).
- **What ships:** an extraction stage turning raw mentions → structured `DiscoveredSource` records (name, handle/url, `content_source_type`, archetype, `topic_tags`, rationale) via one LLM call (prompt in `prompts.py`), then **deterministic code** dedups by normalized name/handle and ranks by mention frequency + recency (Rule 5 — LLM extracts, code ranks).
- **Definition of done:** given fixture mentions, returns ranked `DiscoveredSource`s tagged to the correct archetype (LLM **mocked**); the dedup + frequency ranking is pure code and deterministic for a fixed input (asserted); a low-signal niche yields fewer, not fabricated, results. Pytest, LLM mocked.
- **Dependencies:** Sub-phase 1.

### Sub-phase 3: Resolve + idempotent upsert into catalog
- **Files touched:** `agents/discovery/resolve_upsert.py`.
- **What ships:** resolve each `DiscoveredSource` to a real entity (YouTube via 5b `youtube_resolve`, podcasts via `itunes_resolve`, X via 5c resolver, people via Wikipedia) and **upsert** into `content_sources`/`personalities` with merged `personas` + `topic_tags` + updated `popularity_score`, on the `(content_source_type, external_id)` key — **never overwriting** a curated row's hand-set fields (merge, don't clobber); unresolved candidates are dropped with a logged reason.
- **Definition of done:** discovered candidates upsert as new/refreshed `content_sources` tagged to the right archetype (resolvers + DB **mocked**/test DB); re-running is **idempotent** (no dupes, `is_curated` rows keep their curated fields); an unresolvable candidate is dropped, logged, never written half-formed. Pytest.
- **Dependencies:** Sub-phase 2; Phase 5b SP1 (schema) + SP3 (resolvers).

### Sub-phase 4: Scheduled refresh task
- **Files touched:** `trigger/refreshSourceCatalog.ts`.
- **What ships:** a Trigger.dev **v4 `schedules.task`** (cadence tuned from the donor quarterly default) that, per archetype, runs collect → extract → resolve → upsert, with a **no-silent-cap** summary log of sources added / refreshed / dropped per archetype.
- **Definition of done:** valid v4 `schedules.task` (never `client.defineJob`); a dev run discovers → resolves → upserts for ≥1 archetype (mocked) and logs the add/drop counts; the cron expression validates. ⚠ live runs make outward API + LLM calls — keep gated to dev until M6 deploy.
- **Dependencies:** Sub-phase 3.

## Phase-level definition of done
A scheduled agent mines community signals per archetype, extracts and code-ranks candidate sources, and resolves + idempotently upserts newly-rising sources into `content_sources`/`personalities` without clobbering curated rows — so the existing 5c matcher serves fresher recommendations with no UI change. **Validated by:** the collector tests (happy/failure/empty, mocked HTTP); the extract+rank determinism test (mocked LLM); the idempotent-upsert + curated-preservation test; the v4 cron validity + per-archetype summary-log test.

## Out of scope
- **UI** — recommendations are served by the existing 5c screens; this only refreshes their data.
- **Learned feed ordering** (Phase 6b).
- A quality/abuse review layer beyond a basic resolution + frequency gate (note as open Q).
- Real-time discovery — this is periodic, not streaming.

## Open questions
1. **Community APIs + keys** — Reddit API, X API, Podcast Index (donor uses it behind a key) — which, cost, rate limits, ToS for crawling.
2. **Crawl cadence** — donor refreshes quarterly; rising-voices may want weekly. Pick per archetype.
3. **Quality gate** — how to avoid junk/spam sources (min mention frequency? cross-source corroboration? a manual review queue for net-new high-rank entries?).
4. **`/cmo` archetype lock** — the niche list this agent mines.

## Self-critique
**Product lens:** PASS — delivers spec §3.2's "research agent that catches newly rising voices without manual curation," the explicit M6 value. It's additive (data only), so it can't regress the shipped 5c flow.
**Engineering lens:** PASS — follows the project's Python-agent module discipline (CLAUDE.md §3) and Rule 5 (LLM extracts, code ranks/dedups). Reuses 5b/5c resolvers and the 5b schema rather than a parallel catalog. The v4 `schedules.task` constraint is honored. DoDs are mock-verifiable. SP4 (cron) lands last and is gated.
**Risk lens:** PASS with flags. The **biggest risk is writing junk sources into the catalog** — mitigated by the idempotent, curated-preserving, resolve-or-drop upsert (SP3 DoD) and surfaced as the quality-gate open question (not hidden). ⚠ SP4 makes outward API+LLM calls when live — gated to dev. No schema drops. Within-phase files are distinct per sub-phase. Collectors test the failure/empty paths (Rule 9). ToS/legal risk of crawling flagged as an open question for the owner.
**Irreversible sub-phases:** none (idempotent upserts; the live cron is reversible — disable the schedule). Curated-row preservation is an explicit guard against an effectively-irreversible data clobber.
