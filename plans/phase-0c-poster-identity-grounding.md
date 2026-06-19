# Phase 0c: Poster Identity Grounding

**Milestone:** M0 — Poster quality (extends [phase-0b](phase-0b-serp-seeded-poster-pipeline.md))
**Status:** Not started
**Estimated effort:** L

## Goal
Generated posters depict the **correct current person** named in each story — never a stale-prior substitute (e.g. Biden for a Trump-led G7, or Jerome Powell for a new-Fed-chair story) — by resolving identity from the story text and conditioning the image model on a **verified canonical reference photo** instead of a best-effort SERP seed.

## Background (root cause)
Today identity is sourced from the image model's training prior:
- `agents/m0/story_concept.py` collapses a story to one `entity_name`; for a role ("Fed chair") it loses the story's actual named person and gemini-2.5-flash backfills the famous-but-former incumbent.
- `agents/m0/serper_image_search.py` queries the role/event; Google Images returns the former incumbent; the scorer can't tell *current* from *former*.
- `agents/m0/reference_prompt_synthesizer.py::_entity_rule` only emits "keep a recognizable likeness of `<name>`" as text; `generate_from_reference` then conditions Nano Banana Pro on the stale SERP seed.

The registry (`entities`, migration 0007) holds **no photos** and is wired only to ranking.

## Design decisions (locked with user, 2026-06-18)
- **Keep the current photoreal pipeline.** No symbolic fallback, no skipping posters.
- Identity comes from the **story text** (the name) + a **verified photo** (the face). The image model is never the source of truth for *who* a person is.
- Office-holder resolution is **demand-driven from the news**, not a global "who-holds-office" table: L1 resolves the person the story names; the photo store is keyed by that resolved entity, not by the role.
- When no verified photo exists for a resolved person, behaviour is **unchanged** (today's best-SERP-seed path) — we stop *guessing* only when we hold a trusted photo.

## Sub-phases

### Sub-phase 1: Canonical reference-image store (schema + bucket)
- **Files touched:** `supabase/migrations/0019_entity_reference_images.sql` (new), `reference/poster-pipeline.md` (append section)
- **What ships:** A table `entity_reference_images` (`reference_id` pk, `entity_key` text unique — normalized resolved name, `entity_kind` text, `reference_storage_path` text, `reference_public_url` text, `source_page_url` text, `verified_at` timestamptz, `valid_as_of` date, `verification_confidence` real, `created_at`/`updated_at`) plus a Supabase storage bucket `entity-reference-images`. RLS: service-role write, public read (mirrors `story-posters`).
- **Definition of done:** Migration applies on remote via the IPv4 session pooler (`db push --db-url`, port `:6543` — see [supabase-ddl memory]); `select * from entity_reference_images` returns 0 rows without error; the `entity-reference-images` bucket is listable. ⚠ irreversible (DB migration + bucket).
- **Dependencies:** none

### Sub-phase 2: Real-name resolution in concept extraction (L1)
- **Files touched:** `agents/m0/story_concept.py`, `agents/m0/poster_models.py`, `scripts/eval_entity_resolution.py` (new)
- **What ships:** `extract_story_concept` consumes the **full story body + story date** (not just the headline) and resolves the **specific individual the story is about as of that date**, never a role; for multi-leader/event stories it resolves the single **primary** named person (`central_subject_count` already exists). `StoryConcept` gains `entity_key` (normalized for store lookup) and `entity_as_of` (date). The `_CONCEPT_INSTRUCTION` is amended: *"If the story references a role (Fed chair, US president, PM, CEO), resolve to the person named in THIS story's text as of {story_date}; never use your own knowledge of who currently holds the office."*
- **Definition of done:** `scripts/eval_entity_resolution.py` runs the **real** concept extractor over fixtures for the two reported failures + 2 controls and asserts resolved `entity_name`: "Trump names <X> as Fed chair" → `<X>` (not "Powell", not "Fed chair"); "G7 summit" (Trump-led, 2026) → "Donald Trump" (not "Joe Biden"); plus a mock-based unit test in `tests/agents/m0/test_story_concept.py` proving story body + date are threaded into the prompt. (LLM-dependent eval — flagged; Rule 12.)
- **Dependencies:** none

### Sub-phase 3: Fetch + verify + cache canonical photos (L5 population)
- **Files touched:** `agents/m0/entity_reference_images.py` (new), `tests/agents/m0/test_entity_reference_images.py` (new)
- **What ships:** `get_or_fetch_entity_reference_image(entity_key, entity_name, entity_kind, as_of) -> ReferenceImage | None`: returns the cached row when fresh; otherwise SERP-searches the **exact name + recent year** (reuses `serper_image_search`), downloads candidates (`download_candidates`), runs a Flash identity-verification pass (*"Is this <name>, and is it a current/recent likeness — not a former office-holder? confidence 0–1"*), uploads the verified winner to `entity-reference-images`, and upserts the row. Verification failure (or confidence below threshold) returns `None` and caches nothing wrong. Staleness: rows older than `REFERENCE_REFRESH_DAYS` (default 30) are re-fetched.
- **Definition of done:** Unit tests with mocked SERP + LLM + storage: (happy) fresh cached row returns without a network call; (fetch) miss path uploads bytes + upserts a row with `verification_confidence`; (failure) verify-reject returns `None` and writes **no** row; (edge) stale row triggers re-fetch. Per Rule 9 the verify-reject test fails if a low-confidence face is cached.
- **Dependencies:** Sub-phase 1

### Sub-phase 4: Wire canonical photo into poster generation (L3 redirect + SERP fallback)
- **Files touched:** `agents/m0/build_poster_from_news.py`, `agents/m0/batch_posters.py`, `tests/agents/m0/test_build_poster_from_news.py`
- **What ships:** Before SERP seeding, when `concept.entity_kind in {person}` (and the primary subject is a person), call `get_or_fetch_entity_reference_image`; if it returns a verified photo, **that** becomes the conditioning image passed to `generate_from_reference` (sync) / the batch seed (`batch_posters` line ~244-251) — replacing the SERP winner. If it returns `None`, the existing SERP path runs **unchanged**. A post-generation identity check failure triggers one canonical re-fetch (refresh), not a symbolic fallback.
- **Definition of done:** Integration test (generation mocked): when a verified canonical photo exists for the resolved entity, the bytes handed to `generate_from_reference` **equal the canonical bytes**, not the SERP winner; when none exists, the SERP winner bytes are passed exactly as today (no regression). Both sync and batch paths covered.
- **Dependencies:** Sub-phases 1, 2, 3

## Phase-level definition of done
Run the live poster path (sync) for the two reported stories with `RUN_FILL`/poster generation enabled: the G7 story poster depicts **Trump** (the resolved primary subject) and the Fed-chair story depicts the **person named in the story**, each conditioned on a verified `entity-reference-images` photo — confirmed by the `entity_reference_images` rows written + visual check of the uploaded posters. Long-tail stories with no canonical photo still produce a poster via the unchanged SERP path (no regression).

## Out of scope
- A global office-holder / role→person table (resolution stays demand-driven from story text).
- Rendering multiple identifiable real faces in one poster (crowd scenes resolve to the single primary subject).
- Symbolic / faceless fallback treatments (explicitly rejected by user).
- Backfilling reference photos for all 248 registry entities up front (population is demand-driven per story).
- Changes to ranking's `EntityBonus`.

## Open questions
- **Verification confidence threshold** for accepting a fetched photo — proposed default `0.7`. Confirm or override at run time via env (`REFERENCE_MIN_CONFIDENCE`).
- **Refresh cadence** — proposed `REFERENCE_REFRESH_DAYS=30`. A person who changes role inside that window could serve a stale photo until refresh; acceptable for v1?

## Self-critique

**Product lens:** PASS. The MVP capability at stake is "factually correct poster of the person in the news" — every sub-phase traces to it (SP1 stores the truth, SP2 names the right person, SP3 verifies the face, SP4 renders it). The riskiest assumption (SERP+verify can reliably surface a *current* photo of an arbitrary named person) is exercised in SP3, before SP4 depends on it. No scope creep beyond the user-locked L1+L5+wiring decision; symbolic-fallback temptation explicitly cut.

**Engineering lens:** PASS with one flag. Every DoD is fresh-context-verifiable (row counts, byte-equality assertions, migration apply). SP2's DoD is **LLM-dependent** (real concept extractor over fixtures) — flagged per Rule 12; paired with a deterministic mock test for the wiring so the sub-agent isn't blocked on flaky model output. SP4 is the lock-in step and correctly ordered last (depends on 1+2+3); it cements the "canonical-over-SERP" seam, which is the intended commitment. No sub-phase steps outside the Python/Supabase stack in the master plan.

**Risk lens:** PASS. File boundaries are disjoint (SP1 migration, SP2 concept, SP3 new module, SP4 generation wiring) — no two sub-phases edit the same file. Each DoD carries a test; SP2's live eval is flagged. **Irreversible: Sub-phase 1** (migration 0019 + new bucket) — `/run-phase` proceeds with extra care; the migration is additive (new table + bucket, no drops/alters of existing objects), so rollback is a drop. Painting-into-a-corner check: SP1→2→3 leave a populated store + resolver; SP4 consumes both and the SERP fallback preserves the pre-phase behaviour for the uncovered tail — no dead end.

**Irreversible sub-phases:** Sub-phase 1 (additive migration `0019_entity_reference_images.sql` + `entity-reference-images` storage bucket).
