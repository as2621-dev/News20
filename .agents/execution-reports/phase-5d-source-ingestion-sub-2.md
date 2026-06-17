# Phase 5d Sub-phase 2 — X/Twitter adapter (xAI discovery + Playwright tweet screenshot)

**Status:** SUCCESS · **Date:** 2026-06-17

## Implemented
- `XAccountAdapter` (source-keyed `fetch_new_items(handle, since)` + base `search`/`extract_body` contract), matching SP1's `YouTubeAdapter` conventions:
  - Discovery via the **xAI/Grok Live Search** chat-completions API (`https://api.x.ai/v1`, OpenAI-compatible), behind an injectable `post_discoverer` seam. The real seam reads `XAI_API_KEY` from settings ONLY at call time and sends it solely in the `Authorization` header — never logged.
  - Each post → `CandidateStory`: `candidate_external_id`/`candidate_url` = tweet URL, `candidate_title` = `@handle: <snippet>`, `candidate_outlet_domain` = `x.com`, `candidate_outlet_name` = `@handle`, `candidate_body_text` = full post text, `candidate_platform_metadata` = `TwitterContentMetadata` dump (tweet id, author, quote/thread refs).
  - Cutoff filter (strictly-after `since`), drops posts missing URL/text.
  - `extract_body` renders the tweet screenshot and stamps `candidate_social_image_url`.
- `tweet_screenshot.render_tweet_screenshot` — loads X's login-wall-free publish/embed widget (`platform.twitter.com/embed/Tweet.html?id=<id>`) in Playwright headless Chromium, screenshots the `<twitter-widget>` card to `<repo>/assets/sources/tweets/<tweet_id>.png`. Playwright behind an injectable `page_renderer` seam.
- `TwitterContentMetadata` model + `candidate_platform_metadata: dict | None` field on `CandidateStory` (the carrier).
- `xai_api_key: SecretStr` added to `Settings` (additive, after SP1's keys).
- `playwright>=1.40` added to `requirements.txt` (additive; SP1's `yt-dlp` line untouched). Flagged: Railway worker image needs `playwright install chromium`.
- Registered `XAccountAdapter` in `adapters/__init__.py`.

## Files touched
- `agents/ingestion/adapters/x_account.py` (new)
- `agents/ingestion/tweet_screenshot.py` (new)
- `agents/ingestion/models.py` (added `TwitterContentMetadata` + `candidate_platform_metadata`)
- `agents/ingestion/adapters/__init__.py` (register adapter + doc)
- `agents/shared/settings.py` (added `xai_api_key`)
- `requirements.txt` (added `playwright`)
- `tests/agents/ingestion/adapters/test_x_account.py` (new)
- `tests/agents/ingestion/test_tweet_screenshot.py` (new)

## Divergences (and why)
- **`extract_body` sets the screenshot, not the body.** Unlike YouTube (body filled in `extract_body`), the X post text arrives from the discovery call itself, so the body is set during `fetch_new_items` and `extract_body` does the image enrichment. Documented in the docstring; still conforms to the base contract.
- **Embed host = `platform.twitter.com/embed/Tweet.html`** (not `publish.twitter.com` oEmbed). The phase named either; the embed iframe is the simplest login-free card to screenshot. Note: untested against a live render (no browser in tests) — Open question #3 (Playwright on Railway) still applies.

## Review findings + fixes
- **[medium, fixed]** Initial `test_no_key_leak_in_error_path` embedded the secret inside the simulated discovery exception, so the secret legitimately appeared in the logged `error_message` (a raised exception's text, not the configured key). The test conflated "exception text" with "configured key leak." Fixed the test to raise a realistic transport error that does not echo the key, and assert the *configured* key never appears in logs. The adapter code was correct — it only logs the handle + sanitized message + status code, never the key.
- **[low, noted]** `ScreenshotRenderer` type alias is defined after the class but referenced as a string annotation in `__init__` — lazy-evaluated, harmless; instantiation verified.
- No error-swallowing beyond the intended boundary catches (discovery failure → clean `[]`; bad handle → `[]`; screenshot failure → image left None). All carry `fix_suggestion`.

## Validation
```
ruff check (x_account.py, tweet_screenshot.py, models.py, both tests): All checks passed!
ruff format: applied (2 files reformatted)
pytest tests/agents/ingestion/adapters/test_x_account.py tests/agents/ingestion/test_tweet_screenshot.py -q: 17 passed
pytest tests/agents/ingestion/ -q: 124 passed   (107 SP1 baseline + 17 new, no regressions)
```

## Definition of done: PASS
- Given a handle + cutoff returns recent posts as `CandidateStory` records (xAI mocked) — `test_fetch_new_items_normalizes_post_to_candidate`, `test_fetch_filters_posts_at_or_before_cutoff`.
- Screenshot renderer produces a PNG for a tweet URL (Playwright stubbed) — `test_render_returns_path_when_renderer_writes_png`.
- On rate-limit/no-auth: clean `failed` (empty list) + structured error with `fix_suggestion`, no crash, no secret leak — `test_fetch_returns_empty_on_discovery_failure_no_crash`, `test_default_discoverer_missing_key_raises_without_leaking`, `test_missing_key_fetch_returns_clean_empty`, `test_no_key_leak_in_error_path`.

## Concerns / flags for later sub-phases
1. **Playwright/Chromium on Railway (Open Q#3).** Real render is untested (correctly mocked in tests). The worker image must run `playwright install chromium` + system deps; flag for the SP4/deploy step. If too heavy, fall back to a hosted screenshot API behind the same `page_renderer` seam.
2. **xAI Live Search fidelity (Open Q#1).** Whether Grok reliably returns a handle's *canonical tweet URLs* is unproven without a live call. The JSON-array prompt + parser tolerate fences/garbage and degrade to `[]`. If fidelity is poor, the syndication-timeline fallback noted in the phase can slot in behind `post_discoverer`.
3. **Screenshot path is a local filesystem path**, not yet an uploaded URL. SP3's promote-to-pool / poster-skip stage must decide whether to upload the PNG to storage and rewrite `candidate_social_image_url` (matching how posters are uploaded). Out of SP2 scope.
4. `_XAI_MODEL = "grok-3-latest"` is a reasonable default but unverified against the live xAI catalog; trivially swappable.
