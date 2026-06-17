# Phase 5d — Sub-phase 1 execution report: YouTube adapter

**Status:** SUCCESS
**Date:** 2026-06-17

## What I implemented

A source-keyed YouTube ingestion adapter that detects fresh channel uploads via the
keyless channel RSS feed and transcribes them with yt-dlp.

- **Upload detection (keyless):** `fetch_new_items(source_external_id, since_utc)` fetches
  `https://www.youtube.com/feeds/videos.xml?channel_id=<id>`, parses the Atom/Media-RSS
  XML with `xml.etree.ElementTree`, and keeps only entries published strictly after the
  `since` cutoff. No YouTube Data API key.
- **Transcript + thumbnail (yt-dlp):** `extract_body(candidate)` runs yt-dlp's synchronous
  `extract_info` off the event loop (`asyncio.to_thread`), pulls the best English caption
  track (uploaded `subtitles` preferred over `automatic_captions`), converts the WebVTT to
  de-duplicated plain text, and sets `candidate_body_text` + the best `candidate_social_image_url`.
- **CandidateStory shape:** `candidate_external_id` = `candidate_url` = watch URL,
  `candidate_title` = video title, `candidate_outlet_domain` = `youtube.com`,
  `candidate_outlet_name` = channel name (from feed `<title>`),
  `candidate_social_image_url` = best thumbnail (yt-dlp metadata overrides the RSS one),
  `candidate_body_text` = transcript.
- **Caption-less = skipped, not fatal:** `extract_body` raises a typed
  `CaptionUnavailableError` (subclass of `AdapterFetchError`) which `fetch_new_items`
  catches per-video and skips. Any other per-video yt-dlp error is swallowed (body left
  None, logged loud with `fix_suggestion`) so one bad video can't fail the channel batch.
- **Base contract honoured:** `search(search_query, since_utc)` treats `search_query` as a
  channel id and delegates to `fetch_new_items` (documented in the module + method
  docstrings — YouTube is source-keyed, there is no free-text query path).
- **RSS-level failures fail loud:** an HTTP error, empty channel id, or malformed XML raises
  `AdapterFetchError` (the caller catches per-source).
- **Registry:** `agents/ingestion/adapters/__init__.py` now re-exports `BaseNewsAdapter` and
  `YouTubeAdapter` (there is no `get_adapter()` factory in this codebase — adapters are
  imported by path; I matched that and added discoverable re-exports).
- **Dependency:** added `yt-dlp>=2024.0` to `requirements.txt` with a comment.
- **Settings:** none added — RSS + captions are fully keyless (no secret needed).

## Files created / modified (ONLY these)

- `agents/ingestion/adapters/youtube.py` (created)
- `agents/ingestion/adapters/__init__.py` (modified — re-exports + docstring)
- `requirements.txt` (modified — `yt-dlp>=2024.0`)
- `tests/agents/ingestion/adapters/__init__.py` (created — new test package)
- `tests/agents/ingestion/adapters/test_youtube.py` (created)

## Divergences from plan + why

1. **No `get_adapter()` registration.** The plan said "register in `__init__.py` if there's a
   `get_adapter()`/registry pattern." There is none — `GdeltDocAdapter`/`GdeltBigQueryAdapter`
   are imported by full path. I added re-exports to `__all__` instead (matches the codebase,
   Rule 11). No factory invented.
2. **yt-dlp caption text is fetched, not inline.** Real yt-dlp `extract_info` exposes caption
   tracks as `{lang: [{ext, url, ...}]}` with a downloadable `url`, not inline text. The real
   extractor (`_default_ytdlp_extract_info`) downloads the best caption track's vtt via
   yt-dlp's own `urlopen` and attaches it as `data`, keeping the parsing layer
   (`_extract_transcript_text`) mockable and agnostic to inline-vs-fetched. This is the one
   non-obvious production detail; flagged below.

## Self-review findings + fixes

- **[HIGH, fixed during impl]** Initial draft assumed yt-dlp returns inline caption `data`;
  verified against installed yt-dlp (2026.06.09) that it returns a caption `url`. Reworked the
  real extractor to download + attach the vtt text. Parsing layer unchanged.
- **[LOW, noted]** `_attach_caption_text` attaches `data` to only the first usable track and
  returns; preference order (subtitles → automatic_captions, English first) is consistent with
  `_extract_transcript_text`, so they agree. Acceptable.
- No secret leaks (keyless adapter, no settings touched). No error-swallowing of channel-level
  failures (those raise loud); per-video swallowing is intentional and logged with
  `fix_suggestion`. yt-dlp imported lazily so module load / tests don't need it installed.

## Validation results

`.venv/bin/ruff check agents/ingestion/adapters/youtube.py agents/ingestion/adapters/__init__.py tests/agents/ingestion/adapters/test_youtube.py`
→ **All checks passed!** (after `--fix` + `ruff format`)

`.venv/bin/python -m pytest tests/agents/ingestion/adapters/test_youtube.py -q`
→ **11 passed in 0.19s**

Regression check — `.venv/bin/python -m pytest tests/agents/ingestion/ -q`
→ **107 passed in 0.55s** (no regressions from the `__init__.py` re-export).

Test coverage: post-cutoff filtering, captioned-video → CandidateStory (transcript + thumbnail),
caption-less skip (no raise), naive-cutoff-as-UTC, transient yt-dlp error swallowed, empty
channel id raises, RSS HTTP error raises, malformed XML raises, `search()` shim delegation,
`extract_body` caption-less raise, and the WebVTT→plain-text parser (dedup + tag/timing strip).
All externals (httpx + yt-dlp) mocked at the boundary — no network.

## Definition of done: PASS

- Given a fixture RSS feed + cutoff (RSS + yt-dlp mocked), returns new-video CandidateStory
  items with transcript + thumbnail — verified by `test_returns_only_new_captioned_video`.
- A caption-less video is skipped as failed (no crash) — verified by
  `test_caption_less_video_is_skipped_not_raised` + `test_caption_less_raises_caption_unavailable`.
- Conforms to the base interface (`YouTubeAdapter(BaseNewsAdapter)`, both `search` +
  `extract_body` implemented) — verified by import + `test_search_delegates_to_fetch_new_items`.

## Concerns for the orchestrator

1. **Live yt-dlp caption fetch is the one un-tested-against-real-API path.** The
   download-and-attach approach is correct against yt-dlp 2026.06.09's API but is exercised
   only with mocks (per the DoD). Worth a single live smoke against one real channel before SP4
   goes live.
2. **Worker image dep.** `yt-dlp` is added to `requirements.txt`; the Railway worker image must
   install it (already flagged in the phase plan). No ffmpeg needed for captions-only.
3. **`fetch_new_items` is the real entry point** SP3's `source_pipeline` should call (not
   `search`). The `search` shim exists only to satisfy the base ABC.
4. I did not touch the plan files; `plans/phase-5d-source-ingestion.md` and a new
   `phase-5d-source-ingestion-progress.md` show as pre-existing working-tree changes (not mine).
