# Phase 5b · Sub-phase 3 — Per-archetype catalog seeder (port TL;DW `seed_catalog`)

**Status:** SUCCESS · **Commit:** none (orchestrator commits at phase end)

## What shipped
A ported, fully-offline-testable catalog seeder that reads
`data/{type}.{archetype}.json` (file array position = popularity rank), resolves
sources via the relevant external API, and **upserts** into the migration-0009
`content_sources` / `personalities` tables tagged with archetype `personas`
(union across files) + 8-category `topic_tags` + `popularity_score`.

- **Channels** → YouTube `channels.list?forHandle` → `external_id` (UC… id),
  `thumbnail_url`, `subscriber_count`.
- **Podcasts** → iTunes search → `itunes-<collectionId>` external_id, `feed_url`
  captured into `platform_metadata jsonb`.
- **Personalities** → Wikipedia REST `page/summary` photo → `photo_url`.
- **X handles** → `content_sources` rows `content_source_type='x_account'`
  **WITHOUT** live resolution (handle = `external_id`, no thumbnail) — the X
  resolver is 5c/5d work.
- Idempotent via the 0009 unique keys: `content_sources` upserts on
  `(content_source_type, external_id)`; `personalities` on `display_name`.
- Both the Supabase client and the httpx client are **injected** into `run_seed`,
  so the test suite mocks both at the boundary and the CLI path wires the real
  ones. `make_admin_client()` mirrors News20's persist idiom
  (`supabase.create_client` + service-role key) and imports `supabase` lazily so
  tests need neither the package nor any key.

## Files touched
**Created (within authorized list):**
- `scripts/seed_catalog/seed_catalog.py` — orchestrator: load+merge JSON →
  resolve → typed row builders → upsert; `SeedSummary` result model; CLI.
- `scripts/seed_catalog/youtube_resolve.py` — `ChannelMeta` + injected-client resolver.
- `scripts/seed_catalog/itunes_resolve.py` — `PodcastMeta` (feed_url) + injected-client resolver.
- `scripts/seed_catalog/data/channels.ai-frontier-tech.json`,
  `channels.tech-generalist.json`,
  `podcasts.markets-macro.json`, `podcasts.ai-frontier-tech.json`,
  `personalities.ai-frontier-tech.json`, `personalities.startup-operator.json`,
  `x.ai-frontier-tech.json` — seed input (11 distinct channels, 11 podcasts, 11
  personalities, 4 X accounts; cross-archetype overlaps: `lexfridman` +
  `TwoMinutePapers` channels, `Odd Lots` podcast, `Sam Altman` personality).

**Edited:**
- `agents/shared/settings.py` — added `youtube_api_key: str | None = None`
  (Optional so existing envs without it still load; docstring updated).

**Authorized additions (each flagged):**
- `scripts/__init__.py`, `scripts/seed_catalog/__init__.py` — package files so
  `python -m scripts.seed_catalog.seed_catalog` and tests can import. (Phase
  file's "Files you may touch" anticipated these.)
- `tests/scripts/__init__.py`, `tests/scripts/seed_catalog/__init__.py`,
  `tests/scripts/seed_catalog/test_seed_catalog.py` — the SP3 DoD requires
  pytest; the "Files touched" list omitted a test path, so it lives mirroring the
  repo's `tests/agents/...` layout under `tests/scripts/seed_catalog/`.
- **No** `requirements.txt` change — every needed import (`httpx`, `pydantic`,
  `supabase`, `structlog`, `pytest`) is already present. `pytest-mock` is NOT
  installed in this repo; tests use `unittest.mock` like the existing
  `tests/agents/pipeline/test_persist.py`.

## Donor availability
Donor **WAS available** at `~/TLDW-Phase2/tldw/voice-agent-dashboard/scripts/seed_catalog/`
(`seed_catalog.py`, `youtube_resolve.py`, `itunes_resolve.py`, `wikipedia_photos.py`,
`data/*.json`). Structure/logic ported from it.

## Divergences from the donor (Rule 7, surfaced)
1. **Tables/columns renamed:** `sources` → `content_sources`, `source_type` →
   `content_source_type` (News20 0009 naming-collision guard). Upsert conflict
   key adjusted to `content_source_type,external_id`.
2. **Personas re-authored:** donor's 6 personas
   (`operator/builder/investor/crypto/macro/creator`) → News20's **12 SP2
   archetype slugs**; the archetype now comes from the **filename**, not an inline
   field (donor inline `personas` is still unioned in for compatibility).
3. **`popularity_score` (numeric)** derived from file rank (donor wrote
   `personalities.popularity_rank` int). Rank 0 → 100, −2/rank, floor 10. Maps to
   the 0009 `popularity_score numeric` column.
4. **`x_account`** type added (no donor analog; donor had pruned `twitter_account`)
   — stored without a resolver.
5. **HTTP + Supabase clients injected** (donor created them inside the resolvers /
   used `os.environ` directly). This is what makes the suite fully offline-mockable
   per CLAUDE.md. The YouTube key flows through `Settings.youtube_api_key`, not a
   bare `os.environ` read inside the resolver.
6. **Dropped** the donor's `personality_sources` editorial-linking pass +
   `.cache_personality_links.json` (5d owns `personality_appearances` linking;
   0009 has no `personality_sources` table). `wikipedia_photos.py` logic was
   inlined into `seed_catalog.py` (`fetch_wikipedia_photo`) to stay within the
   authorized file list.

## Self-review findings + fixes
- **Line length / formatting (fixed):** the repo has **no** ruff config, so ruff
  defaults to 88-col format. The existing agent files (`persist.py`, `settings.py`,
  …) are already 88-formatted. My first pass had ≤109-col lines (the global
  CLAUDE 120 default); per Rule 11 (match the codebase) I ran `ruff format` to
  conform to the repo's actual 88-col style. `ruff check` + `ruff format --check`
  both clean.
- **Personas/tags validity (verified):** a script confirmed every data-file
  `topic_tag` ∈ the 8 categories and every archetype slug ∈ the 12 SP2 slugs; a
  test (`test_every_row_is_tagged_with_valid_personas_and_topic_tags`) enforces it
  on the emitted rows too.
- **No swallowed exceptions:** resolver HTTP errors return `None` (logged with
  `fix_suggestion`) so one dead handle skips that row, not the batch (Rule 12 —
  fail per row); a missing `YOUTUBE_API_KEY` on a non-dry channel run raises loud
  in `_main_async`; `load_entries` raises `FileNotFoundError` if the data dir is
  gone.
- **`YOUTUBE_API_KEY` Optional (verified):** `Settings()` loads with it unset
  (`youtube_api_key=None`) — existing env loads don't break.
- **No hardcoded secrets:** the key is read via `Settings` / never logged; YouTube
  `key` param is stripped from all log payloads (`safe_params`).

## Validation
- `ruff check scripts/ tests/scripts/ agents/shared/settings.py` → **All checks passed!** (exit 0)
- `ruff format --check …` → **9 files already formatted**
- `pytest tests/scripts/seed_catalog/ -q` → **12 passed**
- Full repo suite `pytest -q` → **289 passed** (2 pre-existing unrelated
  phase-5a deprecation warnings; no new failures).

## Definition of done (SP3)
| DoD clause | Result | Evidence |
|---|---|---|
| Seeder upserts ≥10 channels, ≥10 podcasts, ≥10 personalities tagged to archetypes | **PASS** | `test_seed_upserts_at_least_ten_of_each_kind` (11/11/11 distinct) + `test_every_row_is_tagged_with_valid_personas_and_topic_tags` |
| Re-running is idempotent (unique-key upsert, no dupes) | **PASS** | `test_re_running_is_idempotent_no_duplicate_rows` (distinct row count stable across a 2nd run; fake keys upserts on the `on_conflict` columns) |
| A channel handle resolves to a real `external_id` + `thumbnail_url` (APIs mocked) | **PASS** | `test_channel_handle_resolves_to_external_id_and_thumbnail` (`UC-lexfridman` + `https://yt.test/lexfridman.jpg`, asserts external_id ≠ raw handle) |
| Persona union across archetype files | **PASS** | `test_overlapping_source_unions_personas_across_archetype_files` (lexfridman spans `ai-frontier-tech`+`tech-generalist`; Sam Altman spans `ai-frontier-tech`+`startup-operator`) |
| X handles stored without live resolution | **PASS** | `test_x_accounts_stored_without_live_resolution` (handle = external_id, `thumbnail_url is None`) |

## Concerns / hand-off
- **5c/5d must build the X resolver.** `x_account` rows are seeded with
  `external_id`=handle, `thumbnail_url=None`, `last_fetched_at=null` (unset) — 5d
  ingestion should treat `last_fetched_at is null` on an `x_account` as
  "needs first resolution".
- **`personality_appearances` linking** (donor `personality_sources`) is
  intentionally NOT seeded here — 5d's hunt adapter populates it.
- **`personalities` has no `popularity_score` ordering index in 0009** for the
  catalog browse beyond `content_sources` — fine for 5b, but if 5c orders the
  people grid by `popularity_score` at scale, consider an index. (Not in scope.)
- **Live run never executed** (no DB, no keys, by design). The DoD is proven by
  mocked assertions; a real seed requires `SUPABASE_URL`,
  `SUPABASE_SERVICE_ROLE_KEY`, `YOUTUBE_API_KEY` exported, then
  `python -m scripts.seed_catalog.seed_catalog`.
- **`.env.example`** was not touched (not in the authorized file list); when a
  `.env.example` is maintained, add `YOUTUBE_API_KEY=` there.
