# Phase 7 — Sub-phase 3 execution report

**Endpoint:** `POST /feed/assemble-for-user` (single-user, partial-friendly, synchronous)
**Status:** SUCCESS

## What shipped

Replaced the SP1 stub handler with the real single-user feed assembler. The endpoint
now builds ONE user's feed from the GLOBAL ready-story pool, persists it to
`daily_feeds`, and returns `{ allocated_count, feed_total: 30 }`. It runs
synchronously (a single-user allocation over an already-produced pool — no
generation — is fast) and returns 200.

### Files touched (ONLY these two)
- `agents/worker/pipeline_routes.py` (edit)
- `tests/agents/worker/test_pipeline_routes.py` (edit)

### New helpers in `pipeline_routes.py`
- `_build_service_role_supabase()` — extracted the service-role client construction
  that SP2's `_run_daily` built inline; now reused by both the daily run and the
  assembler (SP2's inline `create_client` + its now-dead lazy `from supabase import
  create_client` were replaced with a call to this helper — behavior identical).
- `_load_interest_nodes(client)` — `{interest_id: InterestNode}` taxonomy (mirrors
  the loader in `_run_daily`).
- `_load_ready_story_pool(client)` — loads the GLOBAL ready pool: current digests
  (`digest_is_current = true`) that carry BOTH `digest_audio_url` and
  `digest_ambient_poster_url`, joined to their `stories` rows, reconstructed as
  lightweight `CanonicalStory` objects (only the fields the scorer reads: id, title,
  `story_outlet_count`, `canonical_published_utc`) plus their `story_interests` tags.
  Empty pool → `([], [])`.
- `_load_single_user_inputs(client, user_id, feed_date)` — scopes the daily batch's
  per-user loaders to ONE user by REUSING `daily_batch._load_followed_entities`,
  `_load_category_allocation`, `_load_prior_feed_story_ids` with a single-element
  `user_ids` list. Returns `None` when the user has no `user_interest_profile` rows
  (unknown user → 404). `prior_feed_story_ids` is loaded for repeat-day correctness.
- `_assemble_for_user(user_id, feed_date)` — orchestrates: build client → load inputs
  (None → `LookupError`) → load ready pool + taxonomy → `assemble_user_feed` (pure
  ranking) → `write_daily_feed` (idempotent produce-once). On a fresh write returns
  `slots_written`; on a produce-once skip (`already_present`) returns `len(slots)` so
  a repeat call reports the same non-zero count without a duplicate insert.

### Handler
- Unknown user (`LookupError`) → **404**; unexpected failure → **500** (logged with
  `fix_suggestion`, not swallowed — Rule 12); empty pool → **200** `allocated_count=0`
  (NOT an error); validation errors → **422** (Pydantic). Router-wide bearer auth from
  SP1 is unchanged (no duplication).
- `feed_total` is sourced from the pipeline's own `FEED_SLOT_BUDGET` (= 30), not a
  magic literal.

> **Re-used `daily_batch` private loaders (`_load_*`) rather than editing
> `daily_batch.py`** — they already accept a `user_ids` list and group by user, so a
> single-element list scopes them to one user with no change to those modules. No edits
> were made to `main.py`, `settings.py`, `feed_assembly.py`, or `daily_batch.py`.

## Tests added (boundary-mocked — no real DB/network)
`patched_assemble` fixture mocks the client builder + the three loaders, and patches
`assemble_user_feed` / `write_daily_feed` AT THEIR SOURCE module (`feed_assembly`)
because the handler imports them lazily.
- `test_assemble_ready_pool_of_24_writes_24_slots` — 24 slots → 200,
  `allocated_count == 24`, `write_daily_feed` called once with exactly those 24 slots.
- `test_assemble_empty_pool_returns_zero_without_raising` — ranker returns `[]` → 200,
  `allocated_count == 0`.
- `test_assemble_is_idempotent_on_already_present_feed` — `write_daily_feed` reports
  `already_present=True` → 200, count 24, zero new rows written (no duplicate insert).
- `test_assemble_unknown_user_returns_404` — loader returns `None` → 404; ranker +
  writer never called.
- `test_assemble_with_missing_user_id_returns_422` — Pydantic validation.
- Updated the SP1 `…reaches_stub_200` test to `…reaches_handler_200` (now exercises the
  real handler behind the boundary mocks; still asserts only the auth contract).
- All SP1 + SP2 tests kept green.

## Validation

```
$ .venv/bin/python -m ruff check agents/worker/pipeline_routes.py tests/agents/worker/test_pipeline_routes.py
All checks passed!

$ .venv/bin/python -m ruff format --check agents/worker/pipeline_routes.py tests/agents/worker/test_pipeline_routes.py
2 files already formatted

$ .venv/bin/python -m pytest tests/agents/worker/test_pipeline_routes.py -q
.................                                                        [100%]
17 passed, 1 warning in 0.21s
```

(The single warning is the pre-existing StarletteDeprecationWarning from FastAPI's
TestClient — unrelated to this change.)

**Validation: PASS.**

## Definition of done — PASS
- A pool of 24 ready stories → `allocated_count == 24` and 24 `daily_feeds` rows
  written (`write_daily_feed` called once with those 24 slots). ✅
- A second identical call → `write_daily_feed`'s produce-once path writes 0 additional
  rows; endpoint still returns 200 with a sensible (non-zero) count. ✅
- An empty pool → `allocated_count == 0` without raising. ✅
- Unknown user → 404; missing/wrong token → 401 (SP1 guard). ✅

## Concerns / flags
1. **Service-role key on the worker (phase open question).** `_assemble_for_user`
   requires `SUPABASE_SERVICE_ROLE_KEY` (to read any user's profile + write their
   `daily_feeds`). If the Railway worker env only has the anon key, this endpoint will
   500 at runtime. This is the phase's stated open question — flagging, not resolved.
2. **Ready-pool definition.** "Ready" = current digest with BOTH audio AND poster URL.
   If a story is intentionally shippable without a poster, this loader would exclude it.
   Matches the brief ("audio + poster ready"); revisit if poster becomes optional.
3. **`CanonicalStory` reconstruction is lightweight** — only the fields the scorer
   reads are populated (id/title/outlet_count/published). The synthetic
   `canonical_url`/normalized_url are placeholders; allocation never uses them. If a
   future ranking term reads body/source fields, this loader must be extended.
4. Two `daily_batch` private (`_`-prefixed) loaders are imported across modules. They
   were not changed; if `daily_batch` later renames them, this import breaks (caught by
   the import-sanity check + tests).
