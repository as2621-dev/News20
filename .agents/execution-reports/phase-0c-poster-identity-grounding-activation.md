# Phase 0c — Activation report (thread live clients into the SP4 canonical seam)

**Task:** Turn ON the dormant canonical-reference grounding by threading the live service-role
Supabase client (and the genai client) into the two production call sites. SP4 landed the seam +
params; both defaulted to `None` in prod, so the canonical path never activated.
**Status:** ACTIVATED — canonical path now reachable in production.
**Date:** 2026-06-18

## Files touched

- `agents/pipeline/orchestrator.py`  (sync poster path)
- `scripts/fill_batch_posters.py`    (batch poster path)

No edits to `build_poster_from_news.py` / `batch_posters.py` were needed — SP4's optional
`supabase_client` params were already present at both seams. (`generate_poster_bytes` was passed the
genai client `poster_genai_client` already; the seam uses that same client for SP3's verification call,
so no separate genai client construction was required at either site.)

## Call site 1 — sync path: `agents/pipeline/orchestrator.py`

### Where the client comes from
`render_phase(write_result, tts_client, supabase_client: Any, ...)` already receives an injected
service-role `supabase_client` (used at stage 7 `persist_digest`). I threaded that same in-scope client
into the poster stage — no new client construction, no env reads, no secrets.

### Change A — `render_phase` → `generate_poster_bytes` (stage 5)
Before:
```python
    poster_bytes = generate_poster_bytes(
        story=editorial_story,
        script=script,
        poster_genai_client=poster_genai_client,
        poster_builder=poster_builder,
    )
```
After:
```python
    poster_bytes = generate_poster_bytes(
        story=editorial_story,
        script=script,
        poster_genai_client=poster_genai_client,
        poster_builder=poster_builder,
        supabase_client=supabase_client,
    )
```

### Change B — `generate_poster_bytes` signature + builder call
Added a keyword-only `supabase_client: Any | None = None` param to `generate_poster_bytes`, and threaded
it to the M0 builder on the NEWS path:
```python
        elif supabase_client is not None and _builder_accepts_supabase_client(builder):
            report = builder(
                m0_digest,
                poster_genai_client,
                supabase_client=supabase_client,
            )
        else:
            report = builder(m0_digest, poster_genai_client)
```
The source-origin branch (`supplied_poster_image_url is not None`) is untouched, and the final `else`
is the unchanged two-arg call.

### Why the `_builder_accepts_supabase_client` signature guard
The pre-existing seam-contract test `tests/agents/pipeline/test_orchestrator.py::
test_poster_bytes_persisted_when_builder_succeeds` drives the FULL `render_phase` with a real
`FakeSupabaseClient` AND injects a strict two-arg stub `def fake_builder(digest, client)` (no `**kwargs`).
Passing `supabase_client=` to that stub raised `TypeError` and broke the contract. A new thin helper
inspects the builder's signature and only forwards `supabase_client` to a builder that declares the
param — the real `build_poster_for_digest` (which has it) gets the live client; two-arg stubs keep their
exact `builder(digest, client)` shape. No test files were edited.

## Call site 2 — batch path: `scripts/fill_batch_posters.py`

### Where the client comes from
`main()` already builds the service-role client:
`supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])`. The
nested `_prep` closure already closes over both `client` (genai) and `supabase`. I threaded `supabase`
into the batch prep call.

Before:
```python
        def _prep(reel: dict) -> tuple[dict, PreparedPoster | None]:
            try:
                return reel, prepare_poster_generation(_digest_for(reel), client)
```
After:
```python
        def _prep(reel: dict) -> tuple[dict, PreparedPoster | None]:
            try:
                return reel, prepare_poster_generation(
                    _digest_for(reel), client, supabase_client=supabase
                )
```
The genai `client` is the same `genai.Client(api_key=os.environ["GEMINI_API_KEY"])` constructed in
`main()` — the seam uses it for the SP3 verification call. No new genai client constructed; no secret
hardcoded.

## SERP fallback unchanged — confirmed

The byte-for-byte SERP path is preserved. `resolve_canonical_reference_seed` (SP4) still returns `None`
— and the caller still uses `winner_downloaded.image_bytes` / `winner_downloaded.mime_type` exactly as
before — whenever the entity is not a person, no verified photo exists, or the photo URL fails to fetch.
The only behavioural change is: when a person now HAS a verified canonical photo (previously impossible
because `supabase_client` was always `None`), those bytes condition generation. The 5 SP4 seam tests that
assert exact SERP-winner bytes on the fallback (and that `_fetch` is never touched on that path) remain
green.

## Validation

- **Ruff** (`--line-length 120`, both files): **All checks passed!**
- **Import sanity:** `import agents.pipeline.orchestrator` → OK; `import scripts.fill_batch_posters` → OK.
- **`pytest tests/agents/m0/ -v`:** **33 passed** (all SP1–SP4 seam tests, unchanged).
- **Seam-contract regression check** (`tests/agents/pipeline/test_orchestrator.py` +
  `test_orchestrator_source_poster.py`): **11 passed** — confirms the two-arg builder stub contract and
  the source-origin path are intact after the signature-guard wiring.

## Clients threaded — grep proof
```
agents/pipeline/orchestrator.py:592:        supabase_client=supabase_client,   # render_phase -> generate_poster_bytes
agents/pipeline/orchestrator.py:372:                supabase_client=supabase_client,   # generate_poster_bytes -> builder
scripts/fill_batch_posters.py:194:                _digest_for(reel), client, supabase_client=supabase
```
None of these default to `None`: line 592 forwards `render_phase`'s injected `supabase_client`; line 372
forwards it into the real builder (guarded for stub safety); the script line forwards the service-role
`supabase` from `main()`.

## Concerns

1. **Per-render blocking SP3 call on a cache miss** (flagged by SP4): a verified-photo cache miss now adds
   a synchronous SERP+verify inside the async render wave. Handled correctly by SP4's
   `_run_coroutine_blocking` thread bridge; cache hits are zero-network. Acceptable for v1, flagged.
2. **L3 post-generation identity check** remains absent by design (none exists in the pipeline to hook;
   SP4 noted it out of scope). The L5 conditioning fix is what this activation turns on.
3. **No live billed run performed** (per the brief). Activation = wiring only. The phase-level DoD (which
   runs the live canonical path for the two reported stories) should be run separately with user consent.

## STATUS: ACTIVATED
