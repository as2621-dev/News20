# Phase 5c — Sub-phase 3a (LOGIC ONLY) execution report

**Scope:** the palette-agnostic LOGIC half of Sub-phase 3 (search backend + TS client + X resolver). NO React/.tsx UI — `SourceSearchModal.tsx` / `FollowButton.tsx` come later from the user's HTML.

## What was implemented (logic half)

1. **Worker source-search endpoint** — `POST /api/sources/search` added to `agents/worker/main.py` (surgical; existing QA/voice routes untouched). Pydantic boundary: `SourceSearchRequest { query: str(min_length=1), kind: Literal["youtube_channel","podcast","x_account"] }` → `SourceSearchResponse { results: SourceSearchResult[], search_ok: bool }`.
   - **YouTube channel search** — donor 2-step ported: `search.list?type=channel` → collect `channelId`s → `channels.list?part=snippet,statistics` for subscriber counts + hi-res thumbnails. Hidden/non-numeric sub counts → `None`. Step-2 enrich failure falls back to step-1 snippets (partial, still `search_ok=True`). Uses the EXISTING `Settings().youtube_api_key` (Phase 5b) — no new settings, no hardcoded key.
   - **iTunes podcast search** — keyless donor port; `external_id = itunes-{collectionId}` (seeder convention, so a podcast added here dedups to the catalog row).
   - **X-handle path** — delegates to the build-fresh resolver; pending free-text follow fallback.
   - **Wiring:** reuses the existing CORS middleware + per-IP rate limiter (added `/api/sources/search` to `_RATE_LIMITED_PREFIXES`), the existing `_log_error_response`-style typed logging, and `httpx.AsyncClient(timeout=10s)` async I/O (matches the GDELT adapter's httpx posture). Never raises a 5xx.
   - **Missing-key / upstream-failure handling (the Rule-12 honesty fix, see review below):** these signal **unavailability** (`search_ok=False`) via an internal `_SourceSearchUnavailable`, distinct from a genuine empty "no matches" (`search_ok=True`). Every failure is LOGGED with `fix_suggestion`; the YouTube API key value is NEVER logged (only a description) and is passed only into httpx params.

2. **X-handle resolver** — NEW `agents/ingestion/adapters/x_resolver.py` (build-fresh; reuse-map §6). Pure deterministic parse (`@handle`, bare handle, x.com/twitter.com URL with scheme/query/trailing-slash → canonical handle + lower-cased `external_id`; reserved feature paths + non-X hosts rejected with `XHandleParseError`). LIVE enrichment is an **injectable, OFF-by-default seam** (`live_lookup`): with none wired (current reality), or on lookup failure / no-profile, returns a **pending** `XAccountResolution(is_pending=True)` carrying just the handle — the DoD "store as a pending x_account free-text follow" fallback. Fully mockable; no test hits a real X API.

3. **`src/lib/sourceSearch.ts`** — NEW typed, **debounce-agnostic** fetch client (debounce belongs to the future UI). Calls the worker, validates the envelope, annotates `is_already_added` by `external_id` against the caller's RLS-scoped follows (joins `user_content_sources → content_sources(external_id, content_source_type)`; degrades to all-false on an anon search WITHOUT throwing — mirrors SP1). Reuses the SAME worker base-URL env var as `askQuestion.ts` (`NEXT_PUBLIC_QA_API_BASE_URL`). Full TS strict, no `any` (uses `unknown` + narrowing). Every transport/non-200/malformed/`search_ok:false` path → `{ results: [], search_ok: false }` (never throws on the search). A REAL authed follow-read error IS surfaced (a silent miss would mis-badge a followed source).

## Files created / modified

| File | Change |
|---|---|
| `agents/worker/main.py` | MODIFIED — added the source-search endpoint + models + helpers; added the path to the rate-limit prefixes; imports `httpx`, `Settings`, X resolver |
| `agents/ingestion/adapters/x_resolver.py` | NEW — build-fresh X-handle resolver |
| `src/lib/sourceSearch.ts` | NEW — typed worker client + follow annotation |
| `tests/agents/qa/test_source_search.py` | NEW — worker endpoint tests (httpx + Settings mocked) |
| `tests/agents/ingestion/test_x_resolver.py` | NEW — resolver tests (live seam mocked) |
| `tests/lib/sourceSearch.test.ts` | NEW — client tests (fetch + Supabase mocked) |

**Not touched** (per scope): `src/lib/sources.ts`, `src/types/source.ts`, `agents/shared/settings.py` (read-only reuse of the existing `youtube_api_key`). The working-tree change to `plans/phase-5d-source-ingestion.md` is pre-existing / not mine.

## Divergences (+ why)

- **`SourceSearchResponse` is a typed ENVELOPE (`{ results, search_ok }`), not the donor's bare array.** The donor returned `SourceSearchResult[]` and used HTTP status for failures; this worker follows the News20 pattern of HTTP-200-always + an honest in-body flag, so the client distinguishes "search unavailable" (missing key / outage) from "no matches" (Rule 12). The TS client surfaces both correctly.
- **`is_already_added` is annotated client-side, NOT in the worker.** The donor annotated server-side from the authed session; the News20 worker holds the service-role key and has no per-user follow context (static-export device + RLS), so annotation lives in `sourceSearch.ts` against the user's own follows — matching how SP1's `getRecommendedSources` annotates.
- **`personality` is excluded from the worker search** (the `kind` Literal omits it). Personalities are a client-side catalog read (SP1 / donor's local `personalities` ILIKE), not a live external API search; including it would mean a Supabase read in the worker for no reason.

## Code-review findings + fixes (Step B/C)

- **MEDIUM (FIXED):** missing-key and upstream-failure paths originally returned `[]` with `search_ok=True`, conflating "could not run" with "no matches". Fixed via `_SourceSearchUnavailable` → endpoint returns `search_ok=False` for those, while a genuinely empty-but-successful search stays `search_ok=True`. Tests updated to assert the distinction (`test_missing_api_key_returns_unavailable_not_empty`, `test_*_failure_returns_unavailable`, `test_entry_missing_collection_id_is_skipped_search_still_ok`).
- **LOW (FIXED):** `_fake_http_client` test helper mis-classified a single `MagicMock` response as a `side_effect` (MagicMocks are callable) → empty results. Fixed to branch on `list` / `Exception` / single-response.
- Verified clean: YouTube 2-step actually chains searchId→channels.list; no secret logged; httpx timeouts set (10s); Pydantic validates at the boundary (422 on bad `kind` / empty `query`); X pending fallback path; TS strictness / no `any`.

## Validation (exact commands → result)

- `.venv/bin/ruff check agents/worker/main.py agents/ingestion/adapters/x_resolver.py tests/agents/qa/test_source_search.py tests/agents/ingestion/test_x_resolver.py` → **PASS** (All checks passed)
- `.venv/bin/ruff format --check` (same files) → **PASS** (reformatted to match the repo's ruff-formatted style)
- `.venv/bin/python -m pytest tests/agents/qa/test_source_search.py tests/agents/ingestion/test_x_resolver.py -q` → **PASS** (36 passed)
- `.venv/bin/python -m pytest tests/agents -q` (full Python agents suite, regression) → **PASS** (313 passed)
- `npx tsc --noEmit` → **PASS** (no errors)
- `npx biome check src/lib/sourceSearch.ts tests/lib/sourceSearch.test.ts` → **PASS** (after `--write` autoformat)
- `npx vitest run tests/lib/sourceSearch.test.ts` → **PASS** (8 passed)
- `npx vitest run` (full TS suite, regression) → **PASS** (319 passed, 35 files)

All external HTTP (YouTube / iTunes / X) and Supabase mocked at the boundary — no test hits a real service.

## Definition of done — SP3 (LOGIC parts PASS / UI parts DEFERRED)

**PASS (logic, mock-verified):**
- Searching a channel name returns addable YouTube results — `test_two_step_chain_returns_enriched_results` (mocked 2-step → typed result w/ subs + thumbnail).
- An already-followed source is annotated `is_already_added: true` — `sourceSearch.test.ts` "annotates is_already_added TRUE for followed external_ids and FALSE otherwise" (mocked follow-join read).
- An `@handle` resolves via the X resolver (mocked live seam) OR is stored as a **pending** `x_account` free-text follow (fallback, no key wired) — `test_x_resolver.py` + `test_handle_resolves_to_pending_x_account`.

**DEFERRED to the UI pass (NOT faked, NOT claimed):**
- Optimistic Follow→Following flip + toast rollback on failure (`personality-grid.tsx` port) — belongs to `FollowButton.tsx`.
- Add / Adding / Added button states + the 300ms debounce + skeletons — belong to `SourceSearchModal.tsx`.

## Concerns / hand-offs

### Contract the future UI consumes

**Worker endpoint** — `POST {NEXT_PUBLIC_QA_API_BASE_URL}/api/sources/search`
- Request: `{ "query": string (non-empty), "kind": "youtube_channel" | "podcast" | "x_account" }`
- Response (always HTTP 200): `{ "results": SourceSearchResult[], "search_ok": boolean }`
- `SourceSearchResult` = `{ source_name, external_id, content_source_type, thumbnail_url|null, description|null, subscriber_count|null, is_pending }`. `is_already_added` is NOT here (annotated client-side).

**TS client** — `searchSources({ query, kind, client?, fetchImpl? }) → Promise<SourceSearchOutcome>`
- `SourceSearchOutcome = { results: SourceSearchResult[], search_ok: boolean }`
- `SourceSearchResult` (client) = the worker shape **plus** `is_already_added: boolean`.
- The modal should: debounce input 300ms + stale-guard (this client is debounce-agnostic), call `searchSources`, render `search_ok===false` as "search unavailable" (NOT "no results"), show "Added" when `is_already_added`, and surface `is_pending` X results with a "pending" affordance. To persist a follow, call the existing `followSource(sourceId, …)` from `src/lib/sources.ts` — note the GAP below.

### Open question #3 — X API choice (NOT decided silently)
No X/Twitter API is wired (the resolver's `live_lookup` is `None`). X handles currently resolve to **pending** records (handle only, no display name/avatar). Deciding the live path (X API v2 user-lookup is paid; cost + rate limits TBD) is required to enrich beyond pending. The seam (`XLiveLookup`) is ready — wiring a real lookup is additive.

### Hand-offs for SP4 / future UI (gaps I could not close in scope)
1. **`followSource` takes a catalog `source_id`, but search results carry only `external_id`.** A NEW source found via search has no `content_sources` row yet. SP4 (or the Follow wiring) needs an **upsert-then-follow** path: upsert a `content_sources` row (with `is_curated=false`, `content_source_type`, `external_id`, `source_name`, `thumbnail_url`) → get its `source_id` → `followSource(source_id)`. This belongs in `src/lib/sources.ts` (a new `upsertUserAddedSource`-style helper) — OUT of my scope (Rule 3), flagged here.
2. **`x_account` pending follows** need a place to live. The schema has an `x_account` `content_source_type` (migration 0009); a pending X follow should upsert a `content_sources` row with the lower-cased handle as `external_id` + `platform_metadata.is_pending=true` (so 5d ingestion can later enrich/poll). The exact pending-marker column is a SP4/5d data decision — not invented here.
3. **`subscriber_count`** is returned for YouTube only; iTunes/X always `null` (the providers don't expose one on these endpoints) — expected, not a bug.
